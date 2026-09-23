"""HTTP API for the track-pair audit service (Python standard library only)."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from solver import ValidationError, audit

HEALTH_PATH = "/healthz"
AUDIT_PATH = "/audit"


class AuditHandler(BaseHTTPRequestHandler):
    server_version = "TrackAudit/1.0"

    def _write_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] == HEALTH_PATH:
            self._write_json(200, {"status": "ready"})
        else:
            self._write_json(404, {"error": {"message": "not found", "path": ""}})

    def do_POST(self):  # noqa: N802 - http.server API
        if self.path.split("?", 1)[0] != AUDIT_PATH:
            self._write_json(404, {"error": {"message": "not found", "path": ""}})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._write_json(
                400,
                {"error": {"message": "invalid Content-Length", "path": ""}},
            )
            return
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._write_json(
                400, {"error": {"message": f"invalid JSON: {exc.msg}", "path": ""}}
            )
            return

        try:
            result = audit(payload)
        except ValidationError as exc:
            # Errors never carry audit results.
            self._write_json(
                422, {"error": {"message": exc.message, "path": exc.path}}
            )
            return
        self._write_json(200, result)

    def log_message(self, fmt, *args):  # keep container logs tidy
        pass


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AuditHandler)
    print(f"track audit service listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
