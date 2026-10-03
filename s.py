#!/usr/bin/env python3
"""All-in-one bootstrap agent for the benchmark box (stdlib only).
- HTTP cmd server on 0.0.0.0:10200  (POST /exec, header X-Token)
- Downloads cloudflared, opens a quick tunnel to the cmd server,
  prints TUNNEL_URL=... to stdout (lands in container logs) and
  serves it at GET /tunnelurl (after cmd server auth).
"""
import http.server
import json
import re
import subprocess
import threading
import time
import urllib.request

TOKEN = "TJ2EiG3kfLhpLbh"
PORT = 10200
MAXOUT = 30000
TUNNEL_URL = {"url": None}


def log(*a):
    print("[agent]", *a, flush=True)


def ensure_cloudflared():
    try:
        subprocess.run(["/usr/local/bin/cloudflared", "--version"],
                       capture_output=True, timeout=20)
        return "/usr/local/bin/cloudflared"
    except Exception:
        pass
    log("downloading cloudflared...")
    url = ("https://github.com/cloudflare/cloudflared/releases/latest/"
           "download/cloudflared-linux-amd64")
    req = urllib.request.Request(url, headers={"User-Agent": "bench"})
    with urllib.request.urlopen(req, timeout=240) as r, \
            open("/usr/local/bin/cloudflared", "wb") as f:
        f.write(r.read())
    import os
    os.chmod("/usr/local/bin/cloudflared", 0o755)
    log("cloudflared installed")
    return "/usr/local/bin/cloudflared"


def tunnel_thread():
    try:
        cf = ensure_cloudflared()
    except Exception as e:
        log("cloudflared setup failed:", e)
        return
    log("starting tunnel...")
    p = subprocess.Popen(
        [cf, "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pat = re.compile(r"https://[a-zA-Z0-9.-]*\.trycloudflare\.com")
    for line in p.stdout:
        m = pat.search(line)
        if m and not TUNNEL_URL["url"]:
            TUNNEL_URL["url"] = m.group(0)
            log("TUNNEL_URL=" + TUNNEL_URL["url"])


class H(http.server.BaseHTTPRequestHandler):
    def _send(self, code, data, ctype="application/json"):
        body = data if isinstance(data, bytes) else data.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        return self.headers.get("X-Token") == TOKEN

    def do_GET(self):
        if self.path == "/ping":
            self._send(200, "pong", "text/plain")
        elif self.path == "/tunnelurl":
            if not self._authed():
                self._send(403, "forbidden", "text/plain")
                return
            self._send(200, json.dumps({"tunnel_url": TUNNEL_URL["url"]}))
        else:
            self._send(404, "nope", "text/plain")

    def do_POST(self):
        if self.path != "/exec" or not self._authed():
            self._send(403, "forbidden", "text/plain")
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            body = {}
        cmd = body.get("cmd", "")
        timeout = min(int(body.get("timeout", 120)), 1500)
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True,
                               text=True, timeout=timeout)
            out = {"rc": p.returncode, "out": p.stdout[-MAXOUT:],
                   "err": p.stderr[-MAXOUT:]}
        except subprocess.TimeoutExpired as e:
            so = e.stdout or b""
            so = so.decode(errors="replace") if isinstance(so, bytes) else so
            out = {"rc": -1, "out": so[-MAXOUT:], "err": "TIMEOUT"}
        except Exception as e:
            out = {"rc": -2, "out": "", "err": repr(e)}
        self._send(200, json.dumps(out))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=tunnel_thread, daemon=True).start()
    log(f"cmd server on 0.0.0.0:{PORT}")
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
