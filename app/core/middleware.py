import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging import request_id_var

log = logging.getLogger("app.request")
VALID_ID = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
API_CSP = "default-src 'none'; frame-ancestors 'none'"


def security_headers(path: str, production: bool) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    }
    if path.startswith("/api/") or path.startswith("/internal/"):
        headers["Cache-Control"] = "no-store"  # tokens and personal data must not be cached
        headers["Content-Security-Policy"] = API_CSP
    if production:
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


class RequestContextMiddleware:
    """Adds a request id and security headers, and logs one line per request (no query string)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.production = get_settings().env == "production"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id", "")
        request_id = incoming if VALID_ID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)
        path, method = scope["path"], scope["method"]
        started, status = time.monotonic(), 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                for name, value in security_headers(path, self.production).items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        finally:
            if not path.startswith("/health/") or status >= 500:
                log.info(
                    "request",
                    extra={"method": method, "path": path, "status": status,
                           "duration_ms": int((time.monotonic() - started) * 1000)},
                )