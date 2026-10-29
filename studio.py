#!/usr/bin/env python3
"""studio — a local web front door for revoice (voice) and edit (stitch).

    studio [--port 8766]

Pure standard library. Everything runs on this Mac: Kokoro in Docker, whisperX, ffmpeg and
the logged-in `claude` CLI, so there are no API costs. Opens http://localhost:8766.
"""
import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "config.json")))
PY = CFG["python"]
VIDEO = (".mp4", ".mov", ".mkv", ".m4v", ".webm")

job = {"running": False, "log": [], "proc": None, "steps": [], "folder": None, "status": "idle"}
lock = threading.Lock()


def videos(d):
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d) if f.lower().endswith(VIDEO) and not f.startswith("."))


def scan(folder):
    folder = os.path.expanduser(folder)
    return {
        "folder": folder,
        "exists": os.path.isdir(folder),
        "sources": videos(folder),
        "revoiced": videos(os.path.join(folder, "revoiced")),
        "edited": videos(os.path.join(folder, "edited")),
        "series": os.path.exists(os.path.join(folder, "series.md")),
    }


def say(line):
    with lock:
        job["log"].append(line.rstrip("\n"))


def run_chain(folder, steps, only, force, dry=False):
    cmds = []
    if "record" in steps:
        # pyobjc (CGEvents) lives in the system python, not the whisperX venv
        cmds.append(("Record", ["/usr/bin/python3", os.path.join(HERE, "record.py"), os.path.join(folder, "script.txt")]
                     + (["--dry"] if dry else [])))
    if "voice" in steps:
        c = [PY, os.path.join(HERE, "revoice.py"), folder]
        if only:
            c += ["--only", *only]
        if force:
            c.append("--force")
        cmds.append(("Voice", c))
    if "edit" in steps:
        c = [PY, os.path.join(HERE, "edit.py"), folder]
        if only:
            c += ["--only", *only]
        cmds.append(("Edit and stitch", c))
    ok = True
    for name, c in cmds:
        say(f"\n=== {name} ===")
        p = subprocess.Popen(c, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env={**os.environ, "PYTHONUNBUFFERED": "1"})
        job["proc"] = p
        for line in p.stdout:
            say(line)
        p.wait()
        if p.returncode != 0:
            say(f"=== {name} finished with problems (exit {p.returncode}) ===")
            ok = False
            if job["status"] == "stopping":
                break
            if name in ("Record", "Voice"):
                say(f"Stopping here: fix the {name.lower()} step first.")
                break
    with lock:
        job["running"] = False
        job["status"] = "done" if ok else ("stopped" if job["status"] == "stopping" else "problems")
    say(f"\n=== {job['status'].upper()} ===")


def pick_folder():
    r = subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Folder of screen recordings")'],
                       capture_output=True, text=True)
    return r.stdout.strip().rstrip("/") if r.returncode == 0 else ""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json", extra=None):
        if not isinstance(body, bytes):
            body = (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def safe(self, folder, rel):
        root = os.path.realpath(os.path.expanduser(folder))
        p = os.path.realpath(os.path.join(root, rel))
        return p if p.startswith(root + os.sep) and os.path.isfile(p) else None

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/":
            return self.send(200, open(os.path.join(HERE, "studio.html"), "rb").read(), "text/html; charset=utf-8")
        if u.path == "/api/pick":
            return self.send(200, {"folder": pick_folder()})
        if u.path == "/api/scan":
            return self.send(200, scan(q.get("folder", "")))
        if u.path == "/api/state":
            since = int(q.get("since", 0))
            with lock:
                return self.send(200, {"running": job["running"], "status": job["status"],
                                       "lines": job["log"][since:], "next": len(job["log"])})
        if u.path == "/api/series":
            p = os.path.join(os.path.expanduser(q.get("folder", "")), "series.md")
            return self.send(200, {"text": open(p).read() if os.path.exists(p) else ""})
        if u.path == "/api/script":
            p = os.path.join(os.path.expanduser(q.get("folder", "")), "script.txt")
            return self.send(200, {"text": open(p).read() if os.path.exists(p) else ""})
        if u.path == "/api/report":
            p = self.safe(q.get("folder", ""), q.get("path", ""))
            return self.send(200, {"text": open(p).read() if p else ""})
        if u.path == "/video":
            p = self.safe(q.get("folder", ""), q.get("path", ""))
            if not p:
                return self.send(404, "not found", "text/plain")
            return self.serve_file(p)
        self.send(404, "not found", "text/plain")

    def serve_file(self, p):
        size = os.path.getsize(p)
        ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m:
            if m.group(1):
                start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if m:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b:
                        break
                    self.wfile.write(b)
                    left -= len(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/run":
            folder = os.path.expanduser(body.get("folder", ""))
            steps = [s for s in body.get("steps", []) if s in ("record", "voice", "edit")]
            if not os.path.isdir(folder) or not steps:
                return self.send(400, {"error": "pick a folder and at least one step"})
            if "record" in steps and not os.path.exists(os.path.join(folder, "script.txt")):
                return self.send(400, {"error": "save a recording script first"})
            with lock:
                if job["running"]:
                    return self.send(409, {"error": "a job is already running"})
                job.update(running=True, log=[], steps=steps, folder=folder, status="running")
            only = [o for o in body.get("only", []) if o]
            threading.Thread(target=run_chain, args=(folder, steps, only, bool(body.get("force")), bool(body.get("dry"))), daemon=True).start()
            return self.send(200, {"ok": True})
        if self.path == "/api/stop":
            with lock:
                p = job.get("proc")
                if job["running"] and p and p.poll() is None:
                    job["status"] = "stopping"
                    p.terminate()
            return self.send(200, {"ok": True})
        if self.path == "/api/series":
            folder = os.path.expanduser(body.get("folder", ""))
            if not os.path.isdir(folder):
                return self.send(400, {"error": "no such folder"})
            open(os.path.join(folder, "series.md"), "w").write(body.get("text", ""))
            return self.send(200, {"ok": True})
        if self.path == "/api/script":
            folder = os.path.expanduser(body.get("folder", ""))
            if not os.path.isdir(folder):
                return self.send(400, {"error": "no such folder"})
            open(os.path.join(folder, "script.txt"), "w").write(body.get("text", ""))
            return self.send(200, {"ok": True})
        if self.path == "/api/reveal":
            p = self.safe(body.get("folder", ""), body.get("path", ""))
            if p:
                subprocess.run(["open", "-R", p])
            return self.send(200, {"ok": bool(p)})
        self.send(404, "not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)  # localhost only
    url = f"http://localhost:{a.port}"
    print(f"studio running at {url}  (ctrl+c to quit)")
    if not a.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
