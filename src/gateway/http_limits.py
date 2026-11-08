"""Request-body limits and trusted-proxy aware client addressing.

``server.limits.max_body_bytes`` / ``max_upload_bytes`` were declared in
configuration but never enforced by the gateway. This ASGI middleware makes
them real: it rejects oversized ``Content-Length`` up front and, for chunked /
streamed bodies that omit the header, counts bytes as they arrive and aborts
once the cap is crossed.
"""

from __future__ import annotations

from typing import Iterable, Tuple

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class _BodyTooLarge(Exception):
    pass


#: Paths that legitimately accept large bodies (file/knowledge ingestion).
DEFAULT_UPLOAD_PREFIXES: Tuple[str, ...] = (
    "/v1/files",
    "/v1/knowledge",
    "/v1/backup",
)
#: 备份包的导入路径不在 ``/v1/backup`` 下，且包本身允许到 ``MAX_BUNDLE_BYTES``
#: （256 MiB，见 ``src/kernels/identity/_backup.py``），因此单独给一档上限。
DEFAULT_BACKUP_PREFIXES: Tuple[str, ...] = (
    "/v1/accounts/backup",
)


class BodyLimitMiddleware:
    """Enforce ``max_body_bytes`` (ordinary requests) / ``max_upload_bytes``.

    A value of ``0`` means "unlimited" (explicitly disabled).
    """

    def __init__(
        self,
        app,
        max_body_bytes: int = 0,
        max_upload_bytes: int = 0,
        upload_prefixes: Iterable[str] = DEFAULT_UPLOAD_PREFIXES,
        max_backup_bytes: int = 0,
        backup_prefixes: Iterable[str] = DEFAULT_BACKUP_PREFIXES,
    ):
        self.app = app
        self.max_body_bytes = max(0, int(max_body_bytes or 0))
        self.max_upload_bytes = max(0, int(max_upload_bytes or 0))
        self.upload_prefixes = tuple(upload_prefixes or DEFAULT_UPLOAD_PREFIXES)
        self.max_backup_bytes = max(0, int(max_backup_bytes or 0))
        self.backup_prefixes = tuple(backup_prefixes or DEFAULT_BACKUP_PREFIXES)

    @staticmethod
    def _matches(path: str, prefixes: Iterable[str]) -> bool:
        return any(
            path == p or path.startswith(p.rstrip("/") + "/")
            for p in prefixes
        )

    def _limit_for(self, scope) -> int:
        path = scope.get("path", "")
        headers = Headers(scope=scope)
        multipart = "multipart/form-data" in headers.get("content-type", "").lower()
        if self._matches(path, self.backup_prefixes):
            return self.max_backup_bytes or self.max_upload_bytes or self.max_body_bytes
        if multipart or self._matches(path, self.upload_prefixes):
            return self.max_upload_bytes or self.max_body_bytes
        return self.max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        if limit <= 0:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        try:
            declared_bytes = int(declared) if declared else None
        except ValueError:
            declared_bytes = None
        if declared_bytes is not None and declared_bytes > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0
        response_started = False

        # If the client sends no Content-Length (chunked), drain *before*
        # dispatch. Waiting for the app to read means an endpoint that ignores
        # the body (or a slow consumer) can accept an unbounded upload.
        buffered: list = []
        if declared_bytes is None:
            prefetched_total = 0
            while True:
                message = await receive()
                buffered.append(message)
                if message["type"] == "http.request":
                    prefetched_total += len(message.get("body", b""))
                    if prefetched_total > limit:
                        await self._reject(scope, receive, send, limit)
                        return
                    if not message.get("more_body", False):
                        break
                elif message["type"] == "http.disconnect":
                    break

        async def replay_receive():
            if buffered:
                return buffered.pop(0)
            return await receive()

        async def limited_receive():
            nonlocal received
            message = await replay_receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge()
            return message

        async def tracking_send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLarge:
            if response_started:
                raise
            await self._reject(scope, receive, send, limit)

    @staticmethod
    async def _reject(scope, receive, send, limit: int) -> None:
        response = JSONResponse(
            {
                "detail": "请求体超过大小限制",
                "error": "request_entity_too_large",
                "limit_bytes": limit,
            },
            status_code=413,
        )
        await response(scope, receive, send)
