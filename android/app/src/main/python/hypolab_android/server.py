"""Boot the HypoLab backend on Android.

Kotlin (BackendRuntime.kt) calls `start()` once per process. The FastAPI app from
backend/app/main.py is served by uvicorn on 127.0.0.1 in a background thread, and the WebView
loads the UI from it, exactly like the desktop build serves frontend/dist.

Android-specific adjustments, all made here so the shared backend code stays unchanged:

* Working directory -> the private data dir. The vendored ace_lib opens `ace.log` and writes result
  files relative to the CWD at import time; the default CWD on Android is "/", which is read-only.
* ACE_DATA_DIR / ACE_REPO_ROOT point the backend at private storage (SQLite DB, caches) and at the
  copy of the built frontend that Kotlin extracts from the APK.
* HYPOLAB_ANDROID=1 selects the Android update checker.
* A per-launch access token guards /api. 127.0.0.1 is shared by every app on the device, and this
  API holds the BRAIN session, saved credentials and AI keys, so only requests carrying the token
  (set by the app as an HttpOnly cookie in its own WebView, or as a header from Kotlin) are served.

HOME (where ~/secrets lives), TMPDIR and the SSL CA bundle are already set up by Chaquopy, and
Chaquopy patches multiprocessing.pool.ThreadPool to use threading primitives.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import traceback

_lock = threading.Lock()
_state: dict = {"status": "idle", "port": 0, "error": "", "started_at": 0.0, "ready_at": 0.0}
_server = None

# Endpoints that reveal nothing sensitive and are useful for liveness checks.
_OPEN_PATHS = {"/api", "/api/health/ping"}
TOKEN_COOKIE = "hypolab_token"
TOKEN_HEADER = b"x-hypolab-token"


class TokenGate:
    """Pure ASGI middleware: reject /api requests that don't carry the launch token."""

    def __init__(self, app, token: str):
        self.app = app
        self.token = token

    def _authorised(self, scope) -> bool:
        cookie_value = ""
        for name, value in scope.get("headers") or []:
            if name == TOKEN_HEADER:
                if value.decode("latin-1") == self.token:
                    return True
            elif name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    k, _, v = part.strip().partition("=")
                    if k == TOKEN_COOKIE:
                        cookie_value = v
        return bool(cookie_value) and cookie_value == self.token

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path") or ""
            if (path == "/api" or path.startswith("/api/")) and path not in _OPEN_PATHS \
                    and not self._authorised(scope):
                body = b'{"error":"This HypoLab API only answers the HypoLab app."}'
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"),
                                        (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _pick_port(preferred: int) -> int:
    # Keep the preferred port whenever possible: the WebView's localStorage (the persisted
    # research context, drafts, theme…) is scoped to the http://127.0.0.1:<port> origin.
    for p in [preferred] + list(range(preferred + 1, preferred + 20)):
        if _port_free(p):
            return p
    raise RuntimeError(f"No free local port between {preferred} and {preferred + 19}.")


def start(data_dir: str, www_root: str, preferred_port: int, token: str, version: str) -> int:
    """Start the backend once. Returns the port it will listen on (idempotent)."""
    global _server
    with _lock:
        if _state["status"] in ("starting", "running"):
            return int(_state["port"])
        os.makedirs(data_dir, exist_ok=True)
        os.chdir(data_dir)
        os.environ["HYPOLAB_ANDROID"] = "1"
        os.environ["ACE_DATA_DIR"] = data_dir
        os.environ["ACE_REPO_ROOT"] = www_root
        os.environ["ACE_VERSION"] = version
        os.environ["ACE_APP_NAME"] = "HypoLab"
        port = _pick_port(int(preferred_port))
        os.environ["ACE_HOST"] = "127.0.0.1"
        os.environ["ACE_PORT"] = str(port)
        _state.update(status="starting", port=port, error="", started_at=time.time(), ready_at=0.0)
        threading.Thread(target=_serve, args=(port, token), name="hypolab-server", daemon=True).start()
        return port


def _serve(port: int, token: str) -> None:
    global _server
    try:
        import uvicorn
        from app.main import app as fastapi_app   # imports and mounts every backend module

        config = uvicorn.Config(
            TokenGate(fastapi_app, token), host="127.0.0.1", port=port,
            loop="asyncio", http="h11", ws="none", lifespan="off",
            log_level="warning", access_log=False, timeout_keep_alive=30,
        )
        server = uvicorn.Server(config)
        _server = server
        threading.Thread(target=_watch_ready, args=(server,), daemon=True).start()
        server.run()
        with _lock:
            if _state["status"] != "error":
                _state.update(status="stopped")
    except BaseException:  # noqa: BLE001 — includes SystemExit raised by uvicorn on bind failure
        with _lock:
            _state.update(status="error", error=traceback.format_exc()[-6000:])


def _watch_ready(server) -> None:
    while _state["status"] == "starting":
        if getattr(server, "started", False):
            with _lock:
                _state.update(status="running", ready_at=time.time())
            return
        time.sleep(0.1)


def status() -> str:
    """JSON status for Kotlin: {status, port, error, boot_seconds, modules}."""
    with _lock:
        out = dict(_state)
    if out["ready_at"]:
        out["boot_seconds"] = round(out["ready_at"] - out["started_at"], 1)
    import sys
    m = sys.modules.get("app.main")   # never import here: that would race the server thread
    out["modules"] = list(getattr(m, "_loaded", []) or []) if m else []
    return json.dumps(out)


def stop() -> None:
    if _server is not None:
        _server.should_exit = True
