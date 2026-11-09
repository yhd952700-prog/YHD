"""Request-body limits and trusted-proxy aware client addressing.

``server.limits.max_body_bytes`` / ``max_upload_bytes`` were declared in
configuration but never enforced by the gateway. This ASGI middleware makes
them real: it rejects oversized ``Content-Length`` up front and, for chunked /
streamed bodies that omit the header, counts bytes as they arrive and aborts
once the cap is crossed.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional, Tuple

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import Match

from src.common.input_guard import (
    JsonShapeError,
    body_model_from_field,
    field_label_from_info,
    scan_json_shape,
    translate_validation_error,
    trim_for_error,
)


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

#: 单条消息上限（与 ``src/gateway/chat.py::ChatRequest.message`` 的
#: ``max_length=8000`` 对齐）。聊天类请求在进入 Pydantic 校验之前就按字节
#: 提前拒绝：否则 1MB / 9.9MB 的超长输入会被整包读入并原样回显到 422。
DEFAULT_MESSAGE_PREFIXES: Tuple[str, ...] = ("/v1/chat",)
MESSAGE_MAX_CHARS = 8000
#: 8000 字按最坏情况估算的字节上限：UTF-8 每字最多 4 字节，再留出 JSON
#: 转义（``\uXXXX`` 可到 6 字节）与结构开销。宁可略宽，也不能误伤合法消息。
DEFAULT_MESSAGE_BYTES = MESSAGE_MAX_CHARS * 8 + 1024


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
        max_message_bytes: int = DEFAULT_MESSAGE_BYTES,
        message_prefixes: Iterable[str] = DEFAULT_MESSAGE_PREFIXES,
        message_max_chars: int = MESSAGE_MAX_CHARS,
    ):
        self.app = app
        self.max_body_bytes = max(0, int(max_body_bytes or 0))
        self.max_upload_bytes = max(0, int(max_upload_bytes or 0))
        self.upload_prefixes = tuple(upload_prefixes or DEFAULT_UPLOAD_PREFIXES)
        self.max_backup_bytes = max(0, int(max_backup_bytes or 0))
        self.backup_prefixes = tuple(backup_prefixes or DEFAULT_BACKUP_PREFIXES)
        self.max_message_bytes = max(0, int(max_message_bytes or 0))
        self.message_prefixes = tuple(message_prefixes or DEFAULT_MESSAGE_PREFIXES)
        self.message_max_chars = max(0, int(message_max_chars or 0))

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
        message_cap = (
            self.max_message_bytes
            if self._matches(scope.get("path", ""), self.message_prefixes)
            else 0
        )
        if message_cap and (limit <= 0 or message_cap < limit):
            limit = message_cap
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
            # 声明超限：直接拒绝，一个字节的 body 都不读。
            await self._reject(scope, receive, send, limit, message_cap)
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
                        # 边读边计数：超过上限立刻中断，绝不把整包读完再判断。
                        await self._reject(scope, receive, send, limit, message_cap)
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
            await self._reject(scope, receive, send, limit, message_cap)

    async def _reject(
        self, scope, receive, send, limit: int, message_cap: int = 0
    ) -> None:
        if message_cap:
            # 与 422 长度校验同一套中文说法：说清上限，不说原文、不回显 input。
            content = {
                "detail": (
                    f"单条消息上限 {self.message_max_chars} 字，"
                    "请求内容过长，未读取完整请求体。"
                ),
                "error": "message_too_long",
                "limit_chars": self.message_max_chars,
            }
        else:
            content = {
                "detail": "请求体超过大小限制",
                "error": "request_entity_too_large",
                "limit_bytes": limit,
            }
        response = JSONResponse(content, status_code=413)
        await response(scope, receive, send)


_GUARD_METHODS = frozenset({"POST", "PUT", "PATCH"})
#: 结构化扫描只在 2 MiB 以内做；更大的 JSON 包（备份导入）交给
#: ``json.loads`` 的 ``RecursionError`` 兜底，避免每次请求都做一遍全量词法扫描。
STRUCTURE_SCAN_MAX_CHARS = 2 * 1024 * 1024
#: 预检只看小请求体：422 必须抢在身份校验的 503 前面（见 ``BodyPreflightMiddleware``）。
PREFLIGHT_MAX_BYTES = 1024 * 1024


def _is_json_content_type(headers: Headers) -> bool:
    content_type = (headers.get("content-type") or "").lower()
    if not content_type:
        return False
    main = content_type.split(";", 1)[0].strip()
    return main == "application/json" or main.endswith("+json")


def _validation_body(loc: list, message: str, err_type: str) -> dict:
    return {
        "detail": [
            {
                "loc": loc,
                "msg": message,
                "type": err_type,
            }
        ]
    }


async def _read_body(receive, max_bytes: Optional[int] = None) -> tuple[bytes, list, bool]:
    """读出整个请求体，返回 ``(body, buffered_messages, truncated)`` 供回放。"""

    buffered: list = []
    chunks: list = []
    total = 0
    truncated = False
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            break
        buffered.append(message)
        chunk = message.get("body", b"")
        total += len(chunk)
        chunks.append(chunk)
        if max_bytes is not None and total > max_bytes:
            truncated = True
            break
        if not message.get("more_body", False):
            break
    return b"".join(chunks), buffered, truncated


def _replay(buffered: list, receive):
    if buffered:
        # 回放已缓存的帧；缓存耗尽后继续读上游（此时 body 已经完整读完，
        # 上游返回的只会是后续的 disconnect / 空帧）。
        frames = list(buffered)

        async def _replay_receive():
            if frames:
                return frames.pop(0)
            return await receive()

        return _replay_receive
    return receive


class JsonBodyGuardMiddleware:
    """在**解析阶段**拦住深嵌套 / 超大容器 / 非法编码的 JSON 请求体。

    位置在 ``BodyLimitMiddleware`` 内层，所以读到的字节已受大小上限约束；
    命中 ``MAX_JSON_DEPTH`` / ``MAX_JSON_ELEMENTS`` 时直接 422，绝不把请求交给
    Pydantic —— 否则 ``json.loads`` / 序列化会在几层之后抛 ``RecursionError``
    变成 500。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") not in _GUARD_METHODS
            or scope.get("liuhao_json_guard_done")
        ):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not _is_json_content_type(headers):
            await self.app(scope, receive, send)
            return

        body, buffered, _truncated = await _read_body(receive)
        try:
            text = body.decode("utf-8", errors="strict") if body else ""
        except UnicodeDecodeError:
            response = JSONResponse(
                {"detail": "请求体编码无效（必须是 UTF-8），请重新提交。"},
                status_code=400,
            )
            await response(scope, _replay(buffered, receive), send)
            return

        if text.strip():
            if len(text) <= STRUCTURE_SCAN_MAX_CHARS:
                try:
                    scan_json_shape(text)
                except JsonShapeError as exc:
                    response = JSONResponse(
                        _validation_body(["body"], str(exc), "json_nesting_too_deep"),
                        status_code=422,
                    )
                    await response(scope, _replay(buffered, receive), send)
                    return
            try:
                json.loads(text)
            except RecursionError:
                response = JSONResponse(
                    _validation_body(
                        ["body"],
                        "请求嵌套层级过深，请拆分后再试。",
                        "json_nesting_too_deep",
                    ),
                    status_code=422,
                )
                await response(scope, _replay(buffered, receive), send)
                return
            except json.JSONDecodeError:
                response = JSONResponse(
                    {"detail": "请求体不是合法的 JSON，请检查格式后重试。"},
                    status_code=400,
                )
                await response(scope, _replay(buffered, receive), send)
                return

        scope["liuhao_json_guard_done"] = True
        await self.app(scope, _replay(buffered, receive), send)


class BodyPreflightMiddleware:
    """把「必填字段缺失 / 请求体不是对象」的 422 抢在身份校验 503 之前。

    ``RateLimitMiddleware`` 在最内层解析身份，并发风暴时会先吐
    ``503 IDENTITY_UNAVAILABLE``，把一个明确的参数错误伪装成身份问题。
    这里只对小请求体（``Content-Length <= 1 MiB``）做**无副作用**的结构预检：
    不发消息、不查库、不写库，只确认必填键在不在。
    """

    def __init__(self, app, fastapi_app=None):
        self.app = app
        self.fastapi_app = fastapi_app
        self._routes = None
        self._route_cache: dict = {}

    def _iter_api_routes(self, routes):
        for route in routes:
            original = getattr(route, "original_router", None)
            if original is not None:
                yield from self._iter_api_routes(original.routes)
                continue
            if hasattr(route, "body_field"):
                yield route

    def _route_for(self, scope):
        if self.fastapi_app is None:
            return None
        cache_key = (scope.get("method"), scope.get("path"))
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        if self._routes is None:
            self._routes = list(self._iter_api_routes(self.fastapi_app.router.routes))
        found = None
        for route in self._routes:
            try:
                match, _child = route.matches(scope)
            except Exception:
                continue
            if match == Match.FULL:
                found = route
                break
        self._route_cache[cache_key] = found
        return found

    @staticmethod
    def _required_labels(model) -> list[tuple[str, str]]:
        fields = getattr(model, "model_fields", None)
        if not fields:
            return []
        labels = []
        for name, info in fields.items():
            if not info.is_required():
                continue
            alias = getattr(info, "alias", None) or name
            labels.append((str(alias), field_label_from_info(info, fallback="该字段")))
        return labels

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("method") not in _GUARD_METHODS
            or scope.get("liuhao_json_guard_done")
        ):
            await self.app(scope, receive, send)
            return
        route = self._route_for(scope)
        body_field = getattr(route, "body_field", None) if route is not None else None
        model = body_model_from_field(body_field)
        required = self._required_labels(model)
        if not required:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        declared = headers.get("content-length")
        try:
            declared_bytes = int(declared) if declared else None
        except ValueError:
            declared_bytes = None
        # 只预检明确的小包；chunked / 大包不在身份风暴之外再读一遍。
        if declared_bytes is None or declared_bytes > PREFLIGHT_MAX_BYTES:
            await self.app(scope, receive, send)
            return

        body, buffered, truncated = await _read_body(receive, max_bytes=PREFLIGHT_MAX_BYTES)
        replay = _replay(buffered, receive)
        if truncated:
            # 声明长度撒谎或 chunked 大包：不在这里做预检，交给内层大小闸与解析闸。
            await self.app(scope, replay, send)
            return
        if not _is_json_content_type(headers) and body.strip():
            await self.app(scope, replay, send)
            return

        parsed: Any
        if not body.strip():
            parsed = {}
        else:
            try:
                text = body.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                response = JSONResponse(
                    {"detail": "请求体编码无效（必须是 UTF-8），请重新提交。"},
                    status_code=400,
                )
                await response(scope, replay, send)
                return
            if len(text) <= STRUCTURE_SCAN_MAX_CHARS:
                try:
                    scan_json_shape(text)
                except JsonShapeError as exc:
                    response = JSONResponse(
                        _validation_body(["body"], str(exc), "json_nesting_too_deep"),
                        status_code=422,
                    )
                    await response(scope, replay, send)
                    return
            try:
                parsed = json.loads(text)
            except RecursionError:
                response = JSONResponse(
                    _validation_body(
                        ["body"],
                        "请求嵌套层级过深，请拆分后再试。",
                        "json_nesting_too_deep",
                    ),
                    status_code=422,
                )
                await response(scope, replay, send)
                return
            except json.JSONDecodeError:
                response = JSONResponse(
                    {"detail": "请求体不是合法的 JSON，请检查格式后重试。"},
                    status_code=400,
                )
                await response(scope, replay, send)
                return
            scope["liuhao_json_guard_done"] = True

        if not isinstance(parsed, dict):
            message = translate_validation_error("model_attributes_type", "请求体")
            response = JSONResponse(
                _validation_body(["body"], message, "model_attributes_type"),
                status_code=422,
            )
            await response(scope, replay, send)
            return

        missing_errors = []
        for field_name, label in required:
            if field_name not in parsed:
                missing_errors.append(
                    {
                        "loc": ["body", field_name],
                        "msg": translate_validation_error("missing", label),
                        "type": "missing",
                    }
                )
        if missing_errors:
            response = JSONResponse({"detail": missing_errors}, status_code=422)
            await response(scope, replay, send)
            return

        await self.app(scope, replay, send)
