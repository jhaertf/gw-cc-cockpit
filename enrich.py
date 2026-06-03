#!/usr/bin/env python3
# cc-cockpit — Copyright (C) 2026 GuniWeb moderne Medien GmbH
# SPDX-License-Identifier: AGPL-3.0-or-later  (see LICENSE)
"""cc-cockpit enrichment — runs locally OR identically via `ssh host python3 -`.
Outputs `claude agents --json` enriched with model, context fill, last activity,
CC version, git status, permission mode and diff stat. Stdlib only."""
import json, os, glob, subprocess, re
from shutil import which

HOME = os.path.expanduser("~")
PROJECTS = os.path.join(HOME, ".claude", "projects")
PERM_RE = re.compile(r'"permissionMode"\s*:\s*"([a-zA-Z]+)"')


def claude_bin():
    for c in ["claude", os.path.join(HOME, ".local/bin/claude"),
              "/opt/homebrew/bin/claude", "/usr/local/bin/claude", "/usr/bin/claude"]:
        if c == "claude":
            if which("claude"):
                return "claude"
        elif os.path.exists(c):
            return c
    return "claude"


def run(cmd, timeout=15):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception:
        return None


def get_sessions():
    r = run([claude_bin(), "agents", "--json"])
    if r and r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout)
        except Exception:
            return []
    return []


def tail_lines(path, max_bytes=300000):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
                f.readline()
            data = f.read()
        return data.decode("utf-8", "replace").splitlines()
    except Exception:
        return []


def transcript_for(sid):
    hits = glob.glob(os.path.join(PROJECTS, "*", sid + ".jsonl"))
    return max(hits, key=lambda p: os.path.getmtime(p)) if hits else None


def git(cwd, *args, timeout=6):
    r = run(["git", "-C", cwd] + list(args), timeout=timeout)
    return r.stdout.strip() if (r and r.returncode == 0) else None


def enrich_one(s):
    sid = s.get("sessionId") or ""
    o = {
        "sessionId": sid, "shortId": sid[:8], "name": s.get("name"),
        "cwd": s.get("cwd"), "project": os.path.basename(s.get("cwd") or "") or None,
        "status": s.get("status") or "unknown", "kind": s.get("kind"),
        "pid": s.get("pid"), "startedAt": s.get("startedAt"),
        "model": None, "contextTokens": None, "contextWindow": None, "contextPct": None,
        "outputTokens": None, "lastActivity": None, "claudeVersion": None,
        "gitBranch": None, "gitDirty": None, "diffStat": None,
        "permMode": None, "lastText": None, "transcript": None, "turns": None,
    }
    tpath = transcript_for(sid) if sid else None
    o["transcript"] = tpath
    if tpath:
        lines = tail_lines(tpath)
        usage = model = ts = ver = branch = text = perm = None
        turns = 0
        max_ctx = 0
        for ln in lines:
            ln = ln.strip()
            if not ln:
                continue
            m = PERM_RE.search(ln)
            if m:
                perm = m.group(1)
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if obj.get("timestamp"):
                ts = obj["timestamp"]
            if obj.get("version"):
                ver = obj["version"]
            if obj.get("gitBranch"):
                branch = obj["gitBranch"]
            if obj.get("type") in ("user", "assistant"):
                turns += 1
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
            if obj.get("type") == "assistant" and msg:
                if msg.get("model"):
                    model = msg["model"]
                u = msg.get("usage")
                if isinstance(u, dict):
                    usage = u
                    cc = ((u.get("input_tokens") or 0)
                          + (u.get("cache_read_input_tokens") or 0)
                          + (u.get("cache_creation_input_tokens") or 0))
                    if cc > max_ctx:
                        max_ctx = cc
                cont = msg.get("content")
                if isinstance(cont, list):
                    t = " ".join(c.get("text", "") for c in cont
                                 if isinstance(c, dict) and c.get("type") == "text" and c.get("text"))
                    if t.strip():
                        text = t.strip()[:280]
        o["model"], o["lastActivity"], o["claudeVersion"] = model, ts, ver
        o["gitBranch"], o["lastText"], o["turns"], o["permMode"] = branch, text, turns, perm
        if usage:
            ctx = ((usage.get("input_tokens") or 0)
                   + (usage.get("cache_read_input_tokens") or 0)
                   + (usage.get("cache_creation_input_tokens") or 0))
            o["contextTokens"] = ctx
            o["outputTokens"] = usage.get("output_tokens")
            win = 1000000 if max(ctx, max_ctx) > 200000 else 200000
            o["contextWindow"] = win
            o["contextPct"] = round(100 * ctx / win) if win else None
    cwd = s.get("cwd")
    if cwd and os.path.isdir(cwd):
        st = git(cwd, "status", "--porcelain")
        if st is not None:
            o["gitDirty"] = sum(1 for l in st.splitlines() if l.strip())
        if not o["gitBranch"]:
            o["gitBranch"] = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
        short = git(cwd, "diff", "HEAD", "--shortstat")
        if short:
            o["diffStat"] = short.strip()
    return o


def main():
    print(json.dumps([enrich_one(s) for s in get_sessions()]))


if __name__ == "__main__":
    main()
