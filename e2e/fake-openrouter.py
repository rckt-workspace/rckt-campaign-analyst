"""Simulated OpenRouter for end-to-end tests. It never contacts the real provider and consumes no credits.

Chat endpoint: POST /api/v1/chat/completions (same shape as OpenRouter).
Control endpoints (test-only):
  GET  /__health        readiness probe
  POST /__reset         clear recorded calls and return to mode "ok"
  POST /__control       body {"mode": "ok" | "error" | "slow"}
  GET  /__calls         recorded calls: model and whether an Authorization header was sent (never its value)
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 18080
REPORT = (Path(__file__).parent / "fixtures" / "sample-report.md").read_text(encoding="utf-8").strip()
SLOW_SECONDS = 1.5
SECRET_ERROR_DETAIL = b"upstream secret detail"

state = {"mode": "ok", "calls": []}
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def do_GET(self):
        if self.path == "/__health":
            return self._json(200, {"status": "ok"})
        if self.path == "/__calls":
            with lock:
                return self._json(200, {"calls": state["calls"], "mode": state["mode"]})
        self._json(404, {"detail": "not found"})

    def do_POST(self):
        if self.path == "/__reset":
            with lock:
                state["mode"] = "ok"
                state["calls"] = []
            return self._json(200, {"status": "reset"})
        if self.path == "/__control":
            mode = self._body().get("mode")
            if mode not in ("ok", "error", "slow"):
                return self._json(400, {"detail": "invalid mode"})
            with lock:
                state["mode"] = mode
            return self._json(200, {"mode": mode})
        if self.path != "/api/v1/chat/completions":
            return self._json(404, {"detail": "not found"})

        body = self._body()
        with lock:
            state["calls"].append({
                "model": body.get("model"),
                "hasAuthorization": bool(self.headers.get("Authorization", "").startswith("Bearer ")),
            })
            mode = state["mode"]

        if mode == "error":
            self.send_response(500)
            self.send_header("Content-Length", str(len(SECRET_ERROR_DETAIL)))
            self.end_headers()
            self.wfile.write(SECRET_ERROR_DETAIL)
            return
        if mode == "slow":
            time.sleep(SLOW_SECONDS)
        self._json(200, {"choices": [{"message": {"role": "assistant", "content": REPORT}, "finish_reason": "stop"}]})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
