"""The API's HTTP edge: the error shape, security headers, and request IDs, plus
the body limit and the desktop token.

Every error response is {"rule": "...", "message": "..."}, and input errors
add "fields". A 500 never says what went wrong: the log does, under the
response's request ID.
"""

import hmac
import json
import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bowerstone_engine.errors import BowerstoneError, InputError

logger = logging.getLogger("bowerstone_engine.http")

REQUEST_ID_HEADER = "x-request-id"
DESKTOP_HEADER = "x-bowerstone-desktop"

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "permissions-policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    ),
}

_HTTP_ERRORS = {
    400: ("bad_request", "The request could not be read."),
    404: ("not_found", "There is nothing at this address."),
    405: ("method_not_allowed", "This address does not accept that method."),
    413: ("too_large", "The request is larger than the engine accepts."),
}
_UNEXPECTED = ("unexpected", "Something went wrong on the server.")


def error_body(rule: str, message: str, **extra: Any) -> dict[str, Any]:
    """The API's error shape."""
    return {"rule": rule, "message": message, **extra}


def install_error_handlers(app: FastAPI) -> None:
    """Turns the engine's errors, input errors, and HTTP errors into the error shape."""
    app.add_exception_handler(BowerstoneError, _bowerstone_error)
    app.add_exception_handler(RequestValidationError, _input_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)


class EdgeMiddleware:
    """The outermost layer of the API, around everything else.

    It gives every response the security headers and a request ID, errors
    included. In desktop mode it refuses any request without the desktop
    token. It reads the request body before the route does, and refuses it as
    soon as it grows past the limit, whatever Content-Length said. Anything
    that escapes the routes becomes a 500 with no details, and is logged.
    """

    def __init__(
        self, app: ASGIApp, *, max_body_bytes: int, desktop_token: str | None
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.desktop_token = None if desktop_token is None else desktop_token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        is_api = str(scope["path"]).startswith("/api/")
        started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                headers[REQUEST_ID_HEADER] = request_id
                if is_api:
                    headers["cache-control"] = "no-store"
            await send(message)

        try:
            if not self._has_desktop_token(scope):
                await send_error(
                    send_with_headers,
                    403,
                    "desktop_token",
                    "This engine answers only the Bowerstone desktop app.",
                )
                return
            body = await self._read_body(scope, receive, send_with_headers)
            if body is None:
                return
            await self.app(scope, _replay(body, receive), send_with_headers)
        except Exception:
            logger.exception("Unexpected error in request %s", request_id)
            if started:
                raise
            await send_error(send_with_headers, 500, *_UNEXPECTED)

    def _has_desktop_token(self, scope: Scope) -> bool:
        if self.desktop_token is None:
            return True
        for name, value in scope["headers"]:
            if name == DESKTOP_HEADER.encode():
                return hmac.compare_digest(value, self.desktop_token)
        return False

    async def _read_body(
        self, scope: Scope, receive: Receive, send: Send
    ) -> bytes | None:
        """The whole body, or None once an error response has been sent."""
        for name, value in scope["headers"]:
            if name == b"content-length":
                if not value.isdigit():
                    await send_error(send, 400, *_HTTP_ERRORS[400])
                    return None
                if int(value) > self.max_body_bytes:
                    await send_error(send, 413, *_HTTP_ERRORS[413])
                    return None
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return None
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                await send_error(send, 413, *_HTTP_ERRORS[413])
                return None
            chunks.append(chunk)
            if not message.get("more_body", False):
                return b"".join(chunks)


async def send_error(send: Send, status: int, rule: str, message: str) -> None:
    """Sends a whole error response straight from the ASGI layer."""
    body = json.dumps(error_body(rule, message)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _replay(body: bytes, receive: Receive) -> Receive:
    """Hands the route the body already read, then passes the rest through."""
    sent = False

    async def replay() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


async def _bowerstone_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, BowerstoneError):
        raise error
    extra: dict[str, Any] = {}
    if isinstance(error, InputError):
        extra["fields"] = [
            {"field": field, "message": message}
            for field, message in error.fields.items()
        ]
    return JSONResponse(error_body(error.rule, error.message, **extra), error.status)


async def _input_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, RequestValidationError):
        raise error
    fields = [
        {
            "field": ".".join(str(part) for part in detail["loc"]),
            "message": str(detail["msg"]).removeprefix("Value error, "),
        }
        for detail in error.errors()
    ]
    body = error_body("input", "Some of the input cannot be used.", fields=fields)
    return JSONResponse(body, 422)


async def _http_error(request: Request, error: Exception) -> Response:
    if not isinstance(error, StarletteHTTPException):
        raise error
    rule, message = _HTTP_ERRORS.get(error.status_code, ("http", "The request failed."))
    return JSONResponse(
        error_body(rule, message), error.status_code, headers=error.headers
    )
