#!/usr/bin/env python3
# cc-cockpit — multi-host dashboard for Claude Code sessions.
# Copyright (C) 2026 GuniWeb moderne Medien GmbH
# SPDX-License-Identifier: AGPL-3.0-or-later
# Free software under the GNU Affero General Public License v3 or later; see LICENSE.
"""cc-cockpit server — polls local + remote hosts and serves the dashboard.
Python stdlib only. Binds to 127.0.0.1 by default (local access only)."""
import json, os, re, time, threading, subprocess, shlex, tempfile, urllib.parse
import platform, datetime
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
DEMO = os.environ.get("CC_DEMO", "").lower() in ("1", "true", "yes")  # serve built-in sample data

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


# ── Demo mode (CC_DEMO=1): built-in anonymized sample data, no SSH, no real actions ──
_DEMO = [
    {"host": "build01", "target": "deploy@build01.internal", "sessionId": "a1a1a1a1-0000-0000-0000-000000000001", "cwd": "/srv/ml-pipeline", "status": "busy", "pid": 22014, "_st": 5*3600, "_ac": 12, "model": "claude-opus-4-8", "contextTokens": 192000, "contextWindow": 200000, "contextPct": 96, "outputTokens": 1800, "claudeVersion": "2.1.159", "gitBranch": "main", "gitDirty": 0, "diffStat": None, "permMode": "bypassPermissions", "lastText": "Training run kicked off on the new dataset; monitoring validation loss.", "turns": 121, "pr": {"number": 88, "state": "OPEN", "url": "#", "checks": {"label": "1✗", "state": "fail"}}},
    {"host": "mac", "target": "local", "sessionId": "b2b2b2b2-0000-0000-0000-000000000002", "cwd": "/Users/dev/work/billing-service", "status": "waiting", "pid": 5190, "_st": 2*3600, "_ac": 120, "model": "claude-sonnet-4-6", "contextTokens": 176000, "contextWindow": 200000, "contextPct": 88, "outputTokens": 900, "claudeVersion": "2.1.161", "gitBranch": "fix/proration", "gitDirty": 3, "diffStat": "3 files changed, 84 insertions(+), 12 deletions(-)", "permMode": "acceptEdits", "lastText": "I need a decision before continuing: should mid-cycle proration round up to the next cent or down?", "turns": 39, "pr": {"number": 412, "state": "OPEN", "url": "#", "checks": {"label": "3✓", "state": "pass"}}},
    {"host": "gpu01", "target": "ubuntu@gpu01", "sessionId": "c3c3c3c3-0000-0000-0000-000000000003", "cwd": "/opt/infra", "status": "busy", "pid": 7733, "_st": 90*60, "_ac": 45, "model": "claude-opus-4-8[1m]", "contextTokens": 331000, "contextWindow": 1000000, "contextPct": 33, "outputTokens": 2400, "claudeVersion": "2.1.161", "gitBranch": "tf/network-refactor", "gitDirty": 21, "diffStat": "21 files changed, 612 insertions(+), 188 deletions(-)", "permMode": "bypassPermissions", "lastText": "Applied the VPC subnet split across 21 Terraform files; plan is clean.", "turns": 88, "pr": {"number": 1567, "state": "MERGED", "url": "#", "checks": None}},
    {"host": "mac", "target": "local", "sessionId": "d4d4d4d4-0000-0000-0000-000000000004", "cwd": "/Users/dev/work/mobile-app", "status": "busy", "pid": 4990, "_st": 40*60, "_ac": 60, "model": "claude-opus-4-8", "contextTokens": 710000, "contextWindow": 1000000, "contextPct": 71, "outputTokens": 1500, "claudeVersion": "2.1.161", "gitBranch": "feat/push-notifs", "gitDirty": 4, "diffStat": "4 files changed, 156 insertions(+), 9 deletions(-)", "permMode": "default", "lastText": "Wired up APNs token registration and added a retry queue for failed pushes.", "turns": 53, "pr": {"number": 233, "state": "OPEN", "url": "#", "checks": {"label": "2…", "state": "pending"}}},
    {"host": "build01", "target": "deploy@build01.internal", "sessionId": "e5e5e5e5-0000-0000-0000-000000000005", "cwd": "/srv/api-gateway", "status": "idle", "pid": 21888, "_st": 9*3600, "_ac": 3*3600, "model": "claude-haiku-4-5", "contextTokens": 28000, "contextWindow": 200000, "contextPct": 14, "outputTokens": 300, "claudeVersion": "2.1.161", "gitBranch": "chore/deps", "gitDirty": 12, "diffStat": "12 files changed, 240 insertions(+), 240 deletions(-)", "permMode": "default", "lastText": "Bumped 12 dependencies; tests green.", "turns": 18, "pr": None},
    {"host": "gpu01", "target": "ubuntu@gpu01", "sessionId": "f6f6f6f6-0000-0000-0000-000000000006", "cwd": "/opt/docs-site", "status": "idle", "pid": 7012, "_st": 26*3600, "_ac": 24*3600, "model": "claude-sonnet-4-6", "contextTokens": 44000, "contextWindow": 200000, "contextPct": 22, "outputTokens": 200, "claudeVersion": "2.1.160", "gitBranch": "main", "gitDirty": 0, "diffStat": None, "permMode": "plan", "lastText": "Drafted the migration guide outline; ready for review.", "turns": 12, "pr": None},
    {"host": "mac", "target": "local", "sessionId": "a7a7a7a7-0000-0000-0000-000000000007", "cwd": "/Users/dev/work/web-frontend", "status": "busy", "pid": 4821, "_st": 25*60, "_ac": 30, "model": "claude-opus-4-8", "contextTokens": 410000, "contextWindow": 1000000, "contextPct": 41, "outputTokens": 2100, "claudeVersion": "2.1.161", "gitBranch": "feat/checkout-redesign", "gitDirty": 7, "diffStat": "7 files changed, 240 insertions(+), 58 deletions(-)", "permMode": "default", "lastText": "Refactored the checkout flow into a multi-step wizard with optimistic UI.", "turns": 64, "pr": None},
]

DEMO_DIFF = """diff --git a/src/checkout/wizard.tsx b/src/checkout/wizard.tsx
index 3f2a1b0..9c4e7d2 100644
--- a/src/checkout/wizard.tsx
+++ b/src/checkout/wizard.tsx
@@ -12,7 +12,10 @@ export function CheckoutWizard() {
   const [step, setStep] = useState(0)
+  const [errors, setErrors] = useState({})
+  // optimistic UI: advance immediately, roll back on failure
   return (
-    <div className="checkout">
+    <div className="checkout checkout--wizard">
       <Steps current={step} />
"""

DEMO_LOG = ("(demo) recent output:\n"
            "  edited src/checkout/wizard.tsx\n"
            "  ran `npm test` -> 142 passed\n"
            "  staged 7 files")


def demo_sessions():
    now = time.time()
    out = []
    for d in _DEMO:
        s = dict(d)
        s["startedAt"] = int((now - s.pop("_st")) * 1000)
        s["lastActivity"] = datetime.datetime.fromtimestamp(
            now - s.pop("_ac"), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s["shortId"] = s["sessionId"][:8]
        s["project"] = os.path.basename(s["cwd"])
        s["name"] = None
        s["kind"] = "interactive"
        s["transcript"] = "~/.claude/projects/demo/%s.jsonl" % s["shortId"]
        out.append(s)
    return out


def poll_loop():
    if DEMO:
        while True:
            with LOCK:
                STATE.update(generatedAt=int(time.time()), errors=[], sessions=demo_sessions())
            try:
                with open(os.path.join(WEBROOT, "data", "sessions.json"), "w") as f:
                    json.dump(STATE, f)
            except Exception:
                pass
            time.sleep(INTERVAL)
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
    # Interactive sessions can't be "attached" (that's only for background jobs)
    # -> reopen them with --resume instead.
    sid = s["sessionId"]
    target = s.get("target")
    cwd = s.get("cwd")
    if s.get("kind") == "interactive":
        # --resume is project-scoped -> cd into the session's cwd first
        local = "claude --resume " + sid
        if cwd:
            local = "cd " + shlex.quote(cwd) + " && " + local
    else:
        local = "claude attach " + sid
    if not target or target == "local":
        return local
    return "ssh -t " + shlex.quote(target) + " " + shlex.quote('bash -lc %s' % shlex.quote(local))


def open_terminal(host, sid):
    if DEMO:
        return False, "demo mode — terminal actions are disabled"
    if not SID_RE.match(sid or ""):
        return False, "invalid session id"
    s = find_session(host, sid)
    if not s:
        return False, "unknown session"
    cmd = attach_command(s)
    if platform.system() == "Darwin":
        path = os.path.join(RUNDIR, "attach-%s.command" % sid[:8])
        with open(path, "w") as f:
            f.write("#!/bin/bash\nclear\necho '> %s'\n%s\n" % (cmd.replace("'", "'\\''"), cmd))
        os.chmod(path, 0o755)
        r = subprocess.run(["open", path], capture_output=True, text=True, timeout=10)
        return (r.returncode == 0), (r.stderr.strip() or "terminal opened")
    # Linux: use the first available terminal emulator
    inner = cmd + "; exec bash"
    for term in (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"],
                 ["konsole", "-e"], ["xfce4-terminal", "-e"], ["xterm", "-e"]):
        if which(term[0]):
            try:
                subprocess.Popen(term + ["bash", "-lc", inner])
                return True, "terminal opened"
            except Exception as e:
                return False, str(e)
    return False, "no terminal emulator found (macOS/Linux supported)"


def get_logs(host, sid):
    if DEMO:
        return DEMO_LOG
    if not SID_RE.match(sid or ""):
        return "invalid session id"
    s = find_session(host, sid)
    if not s:
        return "unknown session"
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
            return "Logs are only available for background sessions — this one is interactive (see 'Last reply' above for the current state)."
        return out + (("\n" + err) if err else "")
    except Exception as e:
        return "Error: %s" % e


def get_diff(host, sid):
    if DEMO:
        return DEMO_DIFF
    s = find_session(host, sid)
    if not s:
        return "unknown session"
    cwd, target = s.get("cwd"), s.get("target")
    if not cwd:
        return "no working directory"
    try:
        if not target or target == "local":
            r = subprocess.run(["git", "-C", cwd, "--no-pager", "diff", "HEAD"],
                               capture_output=True, text=True, timeout=20)
        else:
            inner = "cd %s && git --no-pager diff HEAD" % shlex.quote(cwd)
            r = subprocess.run(SSH + [target, "bash", "-lc", inner],
                               capture_output=True, text=True, timeout=25)
        out = r.stdout or ""
        return out[:200000] if out.strip() else "(no uncommitted changes)"
    except Exception as e:
        return "Error: %s" % e


def stop_session(host, sid):
    if DEMO:
        return False, "demo mode — stop is disabled"
    if not SID_RE.match(sid or ""):
        return False, "invalid session id"
    s = find_session(host, sid)
    if not s:
        return False, "unknown session"
    target = s.get("target")
    cmd = (["claude", "stop", sid] if not target or target == "local"
           else SSH + [target, "bash", "-lc", "claude stop " + sid])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.returncode == 0), ((r.stdout or r.stderr or "").strip() or "stopped")
    except Exception as e:
        return False, str(e)


def host_target(host):
    for label, typ, tgt in read_hosts():
        if label == host:
            return ("local" if typ == "local" else tgt), True
    return None, False


def new_session(host, cwd, prompt):
    if DEMO:
        return False, "demo mode — starting sessions is disabled"
    prompt = (prompt or "").strip()
    if len(prompt) < 4:
        return False, "prompt too short"
    target, ok = host_target(host)
    if not ok:
        return False, "unknown host"
    base = "claude --bg %s" % shlex.quote(prompt)
    if cwd:
        base = "cd %s && %s" % (shlex.quote(cwd), base)
    try:
        if target == "local":
            r = subprocess.run(["bash", "-lc", base], capture_output=True, text=True, timeout=25)
        else:
            r = subprocess.run(SSH + [target, "bash", "-lc", base],
                               capture_output=True, text=True, timeout=30)
        return (r.returncode == 0), ((r.stdout or r.stderr or "").strip() or "started")
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
    if DEMO:
        return
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
            if not DEMO:
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
    print("cc-cockpit server on http://%s:%d%s" % (BIND, PORT, "  [DEMO MODE]" if DEMO else ""), flush=True)
    Server((BIND, PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
