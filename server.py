#!/usr/bin/env python3
# cc-cockpit — multi-host dashboard for Claude Code sessions.
# Copyright (C) 2026 GuniWeb moderne Medien GmbH
# SPDX-License-Identifier: AGPL-3.0-or-later
# Free software under the GNU Affero General Public License v3 or later; see LICENSE.
"""cc-cockpit server — pollt lokale + entfernte Hosts, serviert das Dashboard.
Nur Python-stdlib. Bindet standardmaessig an 127.0.0.1 (nur lokal erreichbar)."""
import json, os, re, time, threading, subprocess, shlex, tempfile, urllib.parse
import platform
import http.server, socketserver
from shutil import which

HOME = os.path.expanduser("~")
CONF_DIR = os.environ.get("CC_CONF", os.path.join(HOME, ".config/cc-cockpit"))
HOSTS_FILE = os.path.join(CONF_DIR, "hosts.conf")
KEY = os.path.join(CONF_DIR, "id_cockpit")
KNOWN = os.path.join(CONF_DIR, "known_hosts")
BASE = os.path.join(HOME, ".local/share/cc-cockpit")
WEBROOT = os.path.join(BASE, "web")
ENRICH_PY = os.path.join(BASE, "enrich.py")
RUNDIR = os.path.join(BASE, "run")

PORT = int(os.environ.get("CC_PORT", "8910"))
BIND = os.environ.get("CC_BIND", "127.0.0.1")
INTERVAL = int(os.environ.get("CC_INTERVAL", "8"))

os.environ["PATH"] = HOME + "/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
os.makedirs(RUNDIR, exist_ok=True)
os.makedirs(os.path.join(WEBROOT, "data"), exist_ok=True)

SSH = ["ssh", "-i", KEY, "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
       "-o", "StrictHostKeyChecking=accept-new", "-o", "UserKnownHostsFile=" + KNOWN,
       "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=2"]
SID_RE = re.compile(r"^[0-9a-fA-F-]{6,40}$")

STATE = {"generatedAt": 0, "errors": [], "sessions": []}
LOCK = threading.Lock()
PR_CACHE = {}            # "host|cwd|branch" -> {number,state,url,checks} | None
PR_LOCK = threading.Lock()


def read_hosts():
    hosts = []
    try:
        with open(HOSTS_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                p = [x.strip() for x in line.split("|")]
                if len(p) >= 2:
                    hosts.append((p[0], p[1], p[2] if len(p) > 2 else ""))
    except Exception:
        pass
    return hosts


def collect_host(label, typ, target, src):
    try:
        if typ == "local":
            r = subprocess.run(["python3", ENRICH_PY], capture_output=True, text=True, timeout=45)
            tgt = "local"
        else:
            r = subprocess.run(SSH + [target, "python3", "-"], input=src,
                               capture_output=True, text=True, timeout=45)
            tgt = target
        if r.returncode != 0 or not r.stdout.strip():
            return None
        data = json.loads(r.stdout)
        for s in data:
            s["host"], s["target"] = label, tgt
        return data
    except Exception:
        return None


def poll_loop():
    with open(ENRICH_PY) as f:
        src = f.read()
    while True:
        hosts = read_hosts()
        results, threads = {}, []

        def worker(h):
            results[h[0]] = collect_host(h[0], h[1], h[2], src)

        for h in hosts:
            t = threading.Thread(target=worker, args=(h,))
            t.start()
            threads.append(t)
        for t in threads:
            t.join(timeout=50)

        errs, alls = [], []
        for label, typ, target in hosts:
            d = results.get(label)
            (alls.extend(d) if d is not None else errs.append(label))
        with LOCK:
            STATE.update(generatedAt=int(time.time()), errors=errs, sessions=alls)
        try:
            with open(os.path.join(WEBROOT, "data", "sessions.json"), "w") as f:
                json.dump(STATE, f)
        except Exception:
            pass
        time.sleep(INTERVAL)


def find_session(host, sid):
    with LOCK:
        for s in STATE["sessions"]:
            if s.get("sessionId") == sid and s.get("host") == host:
                return s
    return None


def attach_command(s):
    # Interaktive Sessions koennen nicht "attached" werden (das ist nur fuer
    # Hintergrund-Jobs) -> stattdessen per --resume neu oeffnen.
    sid = s["sessionId"]
    target = s.get("target")
    cwd = s.get("cwd")
    if s.get("kind") == "interactive":
        # --resume ist projektgebunden -> erst in den cwd der Session wechseln
        local = "claude --resume " + sid
        if cwd:
            local = "cd " + shlex.quote(cwd) + " && " + local
    else:
        local = "claude attach " + sid
    if not target or target == "local":
        return local
    return "ssh -t " + shlex.quote(target) + " " + shlex.quote('bash -lc %s' % shlex.quote(local))


def open_terminal(host, sid):
    if not SID_RE.match(sid or ""):
        return False, "ungueltige Session-ID"
    s = find_session(host, sid)
    if not s:
        return False, "Session unbekannt"
    cmd = attach_command(s)
    if platform.system() == "Darwin":
        path = os.path.join(RUNDIR, "attach-%s.command" % sid[:8])
        with open(path, "w") as f:
            f.write("#!/bin/bash\nclear\necho '> %s'\n%s\n" % (cmd.replace("'", "'\\''"), cmd))
        os.chmod(path, 0o755)
        r = subprocess.run(["open", path], capture_output=True, text=True, timeout=10)
        return (r.returncode == 0), (r.stderr.strip() or "Terminal geoeffnet")
    # Linux: ersten verfuegbaren Terminal-Emulator nutzen
    inner = cmd + "; exec bash"
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"],
                 ["konsole", "-e"], ["xfce4-terminal", "-e"], ["xterm", "-e"]):
        if which(term[0]):
            try:
                subprocess.Popen(term + ["bash", "-lc", inner])
                return True, "Terminal geoeffnet"
            except Exception as e:
                return False, str(e)
    return False, "kein Terminal-Emulator gefunden (macOS/Linux unterstuetzt)"


def get_logs(host, sid):
    if not SID_RE.match(sid or ""):
        return "ungueltige Session-ID"
    s = find_session(host, sid)
    if not s:
        return "Session unbekannt"
    target = s.get("target")
    if not target or target == "local":
        cmd = ["claude", "logs", sid]
    else:
        cmd = SSH + [target, "bash", "-lc", "claude logs " + sid]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if not out and (r.returncode != 0 or "--print" in err or "must be provided" in err):
            return "Logs sind nur fuer Hintergrund-Sessions verfuegbar — diese Session laeuft interaktiv (siehe 'Letzte Antwort' oben fuer den aktuellen Stand)."
        return out + (("\n" + err) if err else "")
    except Exception as e:
        return "Fehler: %s" % e


def get_diff(host, sid):
    s = find_session(host, sid)
    if not s:
        return "Session unbekannt"
    cwd, target = s.get("cwd"), s.get("target")
    if not cwd:
        return "kein Arbeitsverzeichnis"
    try:
        if not target or target == "local":
            r = subprocess.run(["git", "-C", cwd, "--no-pager", "diff", "HEAD"],
                               capture_output=True, text=True, timeout=20)
        else:
            inner = "cd %s && git --no-pager diff HEAD" % shlex.quote(cwd)
            r = subprocess.run(SSH + [target, "bash", "-lc", inner],
                               capture_output=True, text=True, timeout=25)
        out = r.stdout or ""
        return out[:200000] if out.strip() else "(keine uncommitteten Änderungen)"
    except Exception as e:
        return "Fehler: %s" % e


def stop_session(host, sid):
    if not SID_RE.match(sid or ""):
        return False, "ungueltige Session-ID"
    s = find_session(host, sid)
    if not s:
        return False, "Session unbekannt"
    target = s.get("target")
    cmd = (["claude", "stop", sid] if not target or target == "local"
           else SSH + [target, "bash", "-lc", "claude stop " + sid])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.returncode == 0), ((r.stdout or r.stderr or "").strip() or "gestoppt")
    except Exception as e:
        return False, str(e)


def host_target(host):
    for label, typ, tgt in read_hosts():
        if label == host:
            return ("local" if typ == "local" else tgt), True
    return None, False


def new_session(host, cwd, prompt):
    prompt = (prompt or "").strip()
    if len(prompt) < 4:
        return False, "Prompt zu kurz"
    target, ok = host_target(host)
    if not ok:
        return False, "Host unbekannt"
    base = "claude --bg %s" % shlex.quote(prompt)
    if cwd:
        base = "cd %s && %s" % (shlex.quote(cwd), base)
    try:
        if target == "local":
            r = subprocess.run(["bash", "-lc", base], capture_output=True, text=True, timeout=25)
        else:
            r = subprocess.run(SSH + [target, "bash", "-lc", base],
                               capture_output=True, text=True, timeout=30)
        return (r.returncode == 0), ((r.stdout or r.stderr or "").strip() or "gestartet")
    except Exception as e:
        return False, str(e)


def summarize_checks(roll):
    if not isinstance(roll, list) or not roll:
        return None
    p = f = x = 0
    for c in roll:
        st = (c.get("conclusion") or c.get("state") or "").upper()
        if st in ("SUCCESS", "NEUTRAL", "SKIPPED"):
            p += 1
        elif st in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"):
            f += 1
        else:
            x += 1
    if f:
        return {"label": "%d✗" % f, "state": "fail"}
    if x:
        return {"label": "%d…" % x, "state": "pending"}
    return {"label": "%d✓" % p, "state": "pass"}


def gh_pr(target, cwd, branch):
    if not cwd or not branch or branch in ("HEAD", ""):
        return None
    fields = "number,state,url,statusCheckRollup"
    try:
        if not target or target == "local":
            r = subprocess.run(["gh", "pr", "list", "--head", branch, "--json", fields, "--limit", "3"],
                               cwd=cwd, capture_output=True, text=True, timeout=12)
        else:
            inner = "cd %s && gh pr list --head %s --json %s --limit 3" % (
                shlex.quote(cwd), shlex.quote(branch), fields)
            r = subprocess.run(SSH + [target, "bash", "-lc", inner],
                               capture_output=True, text=True, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        arr = json.loads(r.stdout)
        if not arr:
            return None
        pr = arr[0]
        return {"number": pr.get("number"), "state": pr.get("state"),
                "url": pr.get("url"), "checks": summarize_checks(pr.get("statusCheckRollup"))}
    except Exception:
        return None


def pr_loop():
    while True:
        with LOCK:
            sess = list(STATE["sessions"])
        seen = {}
        for s in sess:
            key = "%s|%s|%s" % (s.get("host"), s.get("cwd"), s.get("gitBranch"))
            if key not in seen:
                seen[key] = gh_pr(s.get("target"), s.get("cwd"), s.get("gitBranch"))
        with PR_LOCK:
            PR_CACHE.clear()
            PR_CACHE.update(seen)
        time.sleep(90)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=WEBROOT, **k)

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/sessions":
            with LOCK:
                data = dict(STATE)
            sess = [dict(s) for s in data.get("sessions", [])]
            with PR_LOCK:
                for s in sess:
                    s["pr"] = PR_CACHE.get("%s|%s|%s" % (s.get("host"), s.get("cwd"), s.get("gitBranch")))
            data["sessions"] = sess
            self._json(data)
            return
        if u.path == "/api/logs":
            q = urllib.parse.parse_qs(u.query)
            self._json({"text": get_logs(q.get("host", [""])[0], q.get("id", [""])[0])})
            return
        if u.path == "/api/diff":
            q = urllib.parse.parse_qs(u.query)
            self._json({"text": get_diff(q.get("host", [""])[0], q.get("id", [""])[0])})
            return
        if u.path == "/healthz":
            self._json({"ok": True})
            return
        return super().do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/api/open":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            ok, msg = open_terminal(body.get("host"), body.get("sessionId"))
            self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
            return
        if u.path == "/api/stop":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            ok, msg = stop_session(body.get("host"), body.get("sessionId"))
            self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
            return
        if u.path == "/api/new":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            ok, msg = new_session(body.get("host"), body.get("cwd"), body.get("prompt"))
            self._json({"ok": ok, "msg": msg}, 200 if ok else 400)
            return
        self._json({"ok": False, "msg": "not found"}, 404)

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=pr_loop, daemon=True).start()
    print("cc-cockpit server on http://%s:%d" % (BIND, PORT), flush=True)
    Server((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
