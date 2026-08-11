"""Early Content-Length guard for chat attachment multipart requests.

This only rejects oversized requests that declare Content-Length before
Starlette parses multipart. Chunked requests still rely on the per-file/count
validation in the route, and the reverse proxy must enforce its own limit.
"""

from starlette.responses import JSONResponse


CHAT_ATTACHMENTS_MAX_BODY_BYTES = 31 * 1024 * 1024
_CHAT_ATTACHMENT_SUFFIX = "/message-with-files"


class ChatAttachmentContentLengthMiddleware:
    def __init__(self, app, max_body_bytes: int = CHAT_ATTACHMENTS_MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") == "http"
            and scope.get("method") == "POST"
            and str(scope.get("path") or "").endswith(_CHAT_ATTACHMENT_SUFFIX)
        ):
            headers = {key.lower(): value for key, value in scope.get("headers") or []}
            raw_length = headers.get(b"content-length")
            if raw_length:
                try:
                    content_length = int(raw_length)
                except (TypeError, ValueError):
                    content_length = 0
                if content_length > self.max_body_bytes:
                    response = JSONResponse(
                        status_code=413,
                        content={
                            "detail": "Размер multipart-запроса превышает 31 МиБ",
                            "code": "chat_attachment_body_too_large",
                        },
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
