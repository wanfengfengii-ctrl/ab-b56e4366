"""HTTP 服务：JSON API、健康检查与静态前端（仅依赖标准库）。"""

from __future__ import annotations

import json
import os
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from .store import (
    RejectedError,
    StaleRevisionError,
    ValidationError,
    EventStore,
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
}


def build_handler(store: EventStore) -> type:
    class Handler(BaseHTTPRequestHandler):
        server_version = "DomeDril/1.0"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            print(f"[http] {self.address_string()} - {fmt % args}", flush=True)

        # ------------------------------------------------------------------
        def _send_json(self, status: int, body: Dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_static(self, rel: str, content_type: str) -> None:
            path = os.path.join(STATIC_DIR, rel)
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "资源不存在"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _read_json(self) -> Optional[Dict[str, Any]]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValidationError("请求体不是合法的 JSON")
            if not isinstance(body, dict):
                raise ValidationError("请求体必须是 JSON 对象")
            return body

        def _fail(self, exc: Exception) -> None:
            if isinstance(exc, StaleRevisionError):
                self._send_json(
                    HTTPStatus.CONFLICT,
                    {"error": "stale_revision", "reason": str(exc),
                     "state": store.snapshot()},
                )
            elif isinstance(exc, RejectedError):
                self._send_json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {"error": "operation_rejected", "reason": str(exc),
                     "state": store.snapshot()},
                )
            elif isinstance(exc, ValidationError):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": "validation_error", "reason": str(exc)},
                )
            else:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"error": "internal_error", "reason": str(exc)},
                )

        # ------------------------------------------------------------------ GET
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/healthz":
                self._send_json(HTTPStatus.OK, {"status": "ok", "revision": store.revision})
                return
            if path == "/api/state":
                self._send_json(HTTPStatus.OK, store.snapshot())
                return
            static = _STATIC_FILES.get(path)
            if static is not None:
                self._send_static(*static)
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        # ------------------------------------------------------------------ POST
        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._read_json()
                if path == "/api/session":
                    result = store.start_session(body)
                elif path == "/api/legs/toggle":
                    result = store.toggle_leg(body)
                elif path == "/api/poses/switch":
                    result = store.switch_pose(body)
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
            except Exception as exc:  # 业务异常统一映射为明确的拒绝响应
                self._fail(exc)
                return
            self._send_json(HTTPStatus.OK, result)

    return Handler


def run(host: str = "0.0.0.0", port: int = 8000, events_path: Optional[str] = None) -> None:
    events_path = events_path or os.environ.get("EVENTS_PATH", "/data/events.jsonl")
    store = EventStore(events_path)
    server = ThreadingHTTPServer((host, port), build_handler(store))
    server.daemon_threads = True
    print(f"[http] 穹顶演练服务监听 {host}:{port}，事件轨迹 {events_path}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    run(port=port)
