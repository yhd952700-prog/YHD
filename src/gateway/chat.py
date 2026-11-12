"""贾维斯对话 HTTP 端点。

- ``POST /v1/chat``          发送一条消息，返回结构化回复
- ``GET  /v1/chat/stats``    查看会话统计

每个 ``session_id`` 维护独立的多轮历史（不串会话）。默认 session "default"。
provider 由环境变量 AI_PROVIDER_TYPE / AI_PROVIDER_MODEL 控制（默认 ollama）。
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .knowledge_context import (
    build_knowledge_context,
    knowledge_prompt,
    public_metadata,
)
from .policy import require_human_principal
from .account_context import (
    AccountContext,
    audit_key_use,
    deny,
    key_scope_for,
    require_account_principal,
)
from ..kernels.identity import _accounts
from .account_usage import record_usage
from ..ai.conversation_store import ConversationStoreError
from ..ai.context_budget import estimate_tokens
from ..ai.model_config import ModelConfigError
from ..ai.usage_meter import UsageMeter, meter_scope, snapshot, use_meter
from ..common.atomic_write import (
    StorageWriteError,
    describe_storage_failure,
    is_storage_failure,
)
from ..common.input_guard import (
    normalize_required_name,
    normalize_required_text,
)
from ..kernels.memory.store import MemoryStoreError

#: 落盘失败（磁盘满 / 只读盘 / 无权限 / 被占用）。这些错误必须变成
#: 中文可行动提示：对话正文可以生成成功，但历史没保存不能静默。
_STORAGE_ERRORS = (ConversationStoreError, MemoryStoreError, StorageWriteError)

#: ``describe_storage_failure`` / ``ConversationStoreError`` 的文案末尾会附上
#: 「（原始错误：…）」。原文只留在日志里，给用户的 503 只保留可行动部分。
_RAW_ERROR_TAIL_RE = re.compile(r"（原始错误：[\s\S]*?）\s*$")
_STORAGE_FALLBACK_MESSAGE = "会话历史没有保存成功。请检查磁盘空间与目录权限后重试。"


def _public_storage_message(exc: BaseException, text: Optional[str] = None) -> str:
    """去掉内部原始错误尾巴，保留「磁盘已满/只读/被占用 + 下一步」可操作文案。"""
    message = _RAW_ERROR_TAIL_RE.sub("", text if text is not None else str(exc)).strip()
    return message or _STORAGE_FALLBACK_MESSAGE


def _storage_http_error(exc: BaseException) -> Optional[HTTPException]:
    """把存储类异常统一映射成 503 中文可行动提示。

    知识检索/个人上下文/工具装配等底层 SQLite 在磁盘满时会抛裸
    ``sqlite3.OperationalError``，类型不在 ``_STORAGE_ERRORS`` 里；若不拦截，
    用户只会看到英文 500。这里只按"存储故障"特征识别，其它异常照常抛出。
    """
    if not is_storage_failure(exc):
        return None
    # 原始异常（可能含路径/驱动原文）只进日志，不进用户响应。
    logger.warning("chat storage failure -> 503: %s", exc)
    return HTTPException(
        status_code=503,
        detail={
            "code": "storage_unavailable",
            "message": _public_storage_message(
                exc, describe_storage_failure(exc, "数据目录")
            ),
        },
    )


router = APIRouter(prefix="/v1", tags=["chat"])

logger = logging.getLogger(__name__)


# ─── 流式生成与客户端断开的桥接 ──────────────────────────────
#
# ``StreamingResponse`` 在 uvicorn（ASGI spec 2.3）下会在收到 ``http.disconnect``
# 时取消消费任务。问题在于：同步生成器默认经 ``iterate_in_threadpool`` 消费，
# anyio 的取消会**等待**线程返回；而线程往往正阻塞在 provider 的读取上，于是
# 「客户端已走、会话锁还被占着」。这里改成生产者线程 + 队列：消费端被取消时
# 只置位 ``TurnCancelToken``，生产端继续把同步生成器跑到收口（写 interrupted
# 审计、释放 ``serialized_turn`` 锁），不会被丢在半路。


class _StreamStepError:
    """生产线程里的异常，原样转交给异步消费端。"""

    __slots__ = ("exc",)

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc


_STREAM_END = object()


def _next_stream_item(iterator):
    try:
        return next(iterator)
    except StopIteration:
        return _STREAM_END


def _accepts_kwarg(fn, name: str) -> bool:
    """``fn`` 是否接受关键字 ``name``（兼容测试里的简化替身）。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in params.values()
    )


async def _stream_with_cancel(iterator, cancel_token):
    """把同步生成器搬进异步流；消费端取消时置位令牌并让生产端继续收口。"""
    loop = asyncio.get_running_loop()
    queue: "asyncio.Queue[object]" = asyncio.Queue()

    def deliver(value: object) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, value)
        except RuntimeError:
            # 事件循环已关闭：请求已经结束，继续把生成器跑完以释放会话锁。
            pass

    def pump() -> None:
        try:
            for item in iterator:
                deliver(item)
        except BaseException as exc:  # noqa: BLE001 - 由消费端决定如何收口
            deliver(_StreamStepError(exc))
        finally:
            deliver(_STREAM_END)

    threading.Thread(target=pump, name="liuhao-sse-pump", daemon=True).start()
    completed = False
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_END:
                completed = True
                return
            if isinstance(item, _StreamStepError):
                raise item.exc
            yield item
    finally:
        if not completed:
            # 客户端断开 / 消费端异常：生成线程在下一个检查点按 interrupted 收口。
            cancel_token.cancel()


def _done_payload(
    req: ChatRequest,
    assistant,
    knowledge,
    *,
    status: str,
    reason: Optional[str] = None,
    error: Optional[str] = None,
    usage_recorded: bool = False,
) -> Dict[str, object]:
    """构造结束帧：状态、原因与中性中文文案都在同一处，前端只认这一帧。"""
    done: Dict[str, object] = {
        "type": "done",
        "status": status,
        "turn": getattr(assistant, "turn", 0),
        "session_id": req.session_id,
        "correlation_id": str(
            getattr(assistant, "last_turn_correlation_id", "") or ""
        ),
        "usage_recorded": usage_recorded,
        "knowledge": public_metadata(knowledge),
    }
    if error:
        done["error"] = str(error)
    if reason:
        done["reason"] = str(reason)
    return done


# ─── 重量级依赖的惰性接缝 ────────────────────────────────────
#
# 为什么**不能**在模块顶层 ``from ..ai.liuhao import LiuHaoAssistant``：
# ``src.ai.liuhao`` → ``.employee`` → ``..knowledge.memory`` → ``mem0``，而
# mem0 会连带导入 qdrant_client / openai / opentelemetry / grpc / numpy。
# 实测这一步在冷启动里占 **4.4 秒**（本机，空数据目录），但它只在"真的要
# 对话"时才需要，与"登录 / 健康检查 / 模型配置"无关。把它留在启动路径上
# 就是拿用户等待时间换模块整洁。
#
# 保留同名模块级函数而不是在端点内直接 import，是因为测试把
# ``chat_module.LiuHaoAssistant`` / ``chat_module.get_conversation_store``
# 当作真实接缝替换（monkeypatch）；这些名字必须继续可被替换、可被调用。
def LiuHaoAssistant(*args, **kwargs):  # noqa: N802 - 保持原类名以兼容测试接缝
    from ..ai.liuhao import LiuHaoAssistant as _impl

    return _impl(*args, **kwargs)


def provider_for_task(task_type: str, *, model_id=None, key_scope=None):
    from ..ai.model_router import provider_for_task as _impl

    if key_scope is None:
        return _impl(task_type, model_id=model_id)
    return _impl(
        task_type,
        model_id=model_id,
        visible_to=list(key_scope.visible_to),
        allow_unowned=key_scope.allow_unowned,
        allow_owners=list(key_scope.allow_owners),
        allow_env_fallback=key_scope.is_root,
    )


def get_conversation_store():
    from ..ai.conversation_store import get_conversation_store as _impl

    return _impl()


def warmup() -> None:
    """后台预热对话链的重依赖（不阻塞就绪；失败由调用方如实上报）。

    只做导入与历史搜索索引构建，不创建会话实例、不读模型配置 —— 不会因为
    "用户还没配模型"而失败。如果这里失败，第一次真实对话也会以同样的方式
    失败，而且错误会在启动日志与 ``/v1/startup`` 的 background.errors 里
    出现，不会被拖到用户按下发送键才暴露。
    """
    from ..ai.conversation_store import get_conversation_store as _store  # noqa: F401
    from ..ai.liuhao import LiuHaoAssistant as _assistant  # noqa: F401
    from ..ai.model_router import provider_for_task as _router  # noqa: F401

    # 历史全文搜索索引：存量库（换机恢复 / 老数据）第一次启动时在后台建，
    # 建完之前搜索接口会如实返回进度，不阻塞首屏。可用
    # LIUHAO_SEARCH_INDEX_WARMUP=0 关闭（压测需要干净 CPU 时用）。
    if (os.environ.get("LIUHAO_SEARCH_INDEX_WARMUP") or "1").strip().lower() not in (
        "0",
        "false",
        "off",
        "no",
    ):
        try:
            from ..ai.conversation_store import (
                get_conversation_store as _get_store,
            )

            _get_store().build_search_index()
        except Exception:  # noqa: BLE001 - 预热失败不影响对话/搜索降级
            logger.warning("history search index warmup failed", exc_info=True)


# 进程内会话表：**principal 与 session_id 的复合键** -> 贾维斯实例。
#
# 复合键是冲突隔离的关键：此前直接拿客户端传来的 ``session_id`` 当键，于是
# 另一个已登录用户只要猜中 ``"default"`` 就能读到同一实例的历史与画像。
# 现在键里带上 **令牌主体**（``sub``），两个用户即使都用 ``session_id=default``
# 也是两份互不可见实例。
_SESSION_KEY_SEP = "\x00"
_sessions: Dict[str, LiuHaoAssistant] = {}
# 复合键 -> 主体（供会话列表如实展示归属；历史隔离不依赖它）。
_session_users: Dict[str, str] = {}
# 会话容量上限：此前 _sessions 无上限，任何一次失败请求/大量 session_id
# 都能让进程里无限累积 assistant 及其 provider 句柄。超限按 LRU 淘汰。
_MAX_SESSIONS_DEFAULT = 128
_session_last_used: Dict[str, float] = {}
_sessions_lock = threading.Lock()
# 冷会话构造锁按会话粒度：``LiuHaoAssistant(...)``（历史加载 + 工具生命周期）
# 在全局锁**外**执行，不同会话可并发冷启动；同一会话仍严格只构造一次。
_session_build_locks: Dict[str, threading.Lock] = {}


def _max_sessions() -> int:
    raw = (os.environ.get("LIUHAO_CHAT_MAX_SESSIONS") or "").strip()
    try:
        value = int(raw) if raw else _MAX_SESSIONS_DEFAULT
    except ValueError:
        value = _MAX_SESSIONS_DEFAULT
    return max(1, value)


def _close_provider(provider) -> None:
    """Best-effort close of a single provider instance."""
    closer = getattr(provider, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass


def _begin_turn(assistant: LiuHaoAssistant) -> None:
    fn = getattr(assistant, "begin_turn", None)
    if callable(fn):
        fn()


def _end_turn(assistant: LiuHaoAssistant) -> None:
    fn = getattr(assistant, "end_turn", None)
    if callable(fn):
        fn()


def _session_key(principal: Optional[str], session_id: str) -> str:
    """复合键：有 principal 时与 session_id 绑定，否则退回纯 session_id（兼容纯函数调用）。"""
    return f"{principal}{_SESSION_KEY_SEP}{session_id}" if principal else session_id


def _owner_of(key: str) -> Optional[str]:
    if _SESSION_KEY_SEP in key:
        return key.split(_SESSION_KEY_SEP, 1)[0] or None
    return None


def _bare_session_id(key: str) -> str:
    return key.split(_SESSION_KEY_SEP, 1)[1] if _SESSION_KEY_SEP in key else key


def _root_principal() -> Optional[str]:
    """当前账号树的根主体；没有账号树（纯人类身份）时返回 ``None``。"""
    try:
        from ..kernels.identity import get_identity_manager

        node = get_identity_manager().account_root()
    except Exception:  # noqa: BLE001 - 身份未就绪时按"无根"处理，不阻断对话
        return None
    return str(node.principal) if node is not None else None


def _legacy_history_allowed(principal: Optional[str]) -> bool:
    """旧会话键 ``liuhao-{session}`` 没有主体段，只允许根/全局视图读取。

    否则普通成员只要猜中别人的会话名，就能读到根/他人的 0.1.0 历史。
    """
    if principal is None:
        return True
    return _root_principal() == principal


def _history_principal_candidates(
    principal: Optional[str], session_id: str
) -> List[str]:
    """历史主体候选：新键优先，根主体再回落旧键（0.1.0 兼容）。"""
    if not principal:
        return [f"liuhao-{session_id}"]
    primary = f"liuhao-{principal}-{session_id}"
    if not _legacy_history_allowed(principal):
        return [primary]
    legacy = f"liuhao-{session_id}"
    return [primary] if legacy == primary else [primary, legacy]


def _resolve_history_principal(principal: Optional[str], session_id: str) -> str:
    """择优：已有旧数据时继续沿用它，避免升级后"接着聊"丢上下文。"""
    store = get_conversation_store()
    candidates = _history_principal_candidates(principal, session_id)
    for candidate in candidates:
        if store.has_rows(candidate):
            return candidate
    return candidates[0]


def get_assistant(
    session_id: str,
    principal: Optional[str] = None,
    model_id: Optional[str] = None,
    key_scope=None,
) -> LiuHaoAssistant:
    """获取或创建会话实例（线程安全，按主体隔离）。

    ``principal`` 必须来自 **身份令牌**（``require_human_principal``），绝不可取
    客户端请求体里的 ``user_id``。存在时：

    * 会话历史主体为 ``liuhao-{principal}-{session_id}``（跨用户/跨会话均隔离）；
    * 画像主体为 ``user-{principal}`` —— 同一主体的多个会话**共享画像**。

    ``model_id`` 为空时按模型配置中心**当前的默认模型**解析 —— 每次请求都
    重新解析，因此界面上切换默认模型/改密钥后，已存在的会话也会在下一轮
    立即换用新配置（不需要重启后端）。
    """
    key = _session_key(principal, session_id)
    history_principal = _resolve_history_principal(principal, session_id)
    profile_principal = f"user-{principal}" if principal else None
    # 先解析 provider，再创建会话：provider 解析失败（如成员没有可用模型，
    # 返回 409）时绝不留下一个空 assistant。此前是先插入 _sessions 再解析，
    # 实测 member 连续 10 次 409 会留下 10 个空会话。
    provider = provider_for_task("chat", model_id=model_id, key_scope=key_scope)

    # 快路径：命中缓存只更新 LRU，不进入构造锁。
    with _sessions_lock:
        assistant = _sessions.get(key)
        if assistant is not None:
            _session_last_used[key] = time.monotonic()
    if assistant is not None:
        # 热加载：显式 model_id 优先；否则按「对话」任务类型在模型管理里
        # 路由，未命中回落默认模型。替换时按活跃轮次安全关闭旧 provider。
        assistant.set_provider(provider)
        return assistant

    # 全局锁只用于「查缓存 + 登记按会话的构造锁」；真正的构造在锁外进行。
    with _sessions_lock:
        assistant = _sessions.get(key)
        if assistant is not None:
            _session_last_used[key] = time.monotonic()
            build_lock = None
        else:
            build_lock = _session_build_locks.setdefault(key, threading.Lock())
    if assistant is not None:
        assistant.set_provider(provider)
        return assistant
    assert build_lock is not None

    built_here = False
    try:
        with build_lock:
            with _sessions_lock:
                assistant = _sessions.get(key)
            if assistant is None:
                try:
                    built = LiuHaoAssistant(
                        name=history_principal,
                        profile_principal=profile_principal,
                        audit_principal=principal,
                        provider=provider,
                    )
                except Exception:
                    _close_provider(provider)
                    raise
                with _sessions_lock:
                    # 构造期间可能有别的线程已经写入；丢弃自己这份，保证同一
                    # 会话只有一个实例（provider 交给胜者复用，不关闭泄漏句柄）。
                    assistant = _sessions.get(key)
                    if assistant is None:
                        # 容量上限：先淘汰最久未使用的会话，再插入新会话。
                        while _sessions and len(_sessions) >= _max_sessions():
                            if _session_last_used:
                                oldest = min(_session_last_used, key=_session_last_used.get)
                            else:
                                oldest = next(iter(_sessions))
                            evicted = _sessions.pop(oldest, None)
                            _session_users.pop(oldest, None)
                            _session_last_used.pop(oldest, None)
                            if evicted is not None:
                                closer = getattr(evicted, "close", None)
                                if callable(closer):
                                    try:
                                        closer()
                                    except Exception:
                                        pass
                        if principal:
                            _session_users[key] = principal
                        _sessions[key] = built
                        assistant = built
                        built_here = True
                    _session_last_used[key] = time.monotonic()
            else:
                _session_last_used[key] = time.monotonic()
    finally:
        # 只在仍是当前这把锁时移除：正在等待的线程已持有引用，醒来后会命中
        # 缓存，因此不会出现同一会话并发构造两份。
        with _sessions_lock:
            if _session_build_locks.get(key) is build_lock:
                _session_build_locks.pop(key, None)

    if not built_here:
        # 等待期间别人已构造完成（或极端重试竞争下自己这份被丢弃）：复用现有
        # 实例，并把本次解析到的 provider 交给它。
        assistant.set_provider(provider)
    return assistant


def list_sessions(
    principal: Optional[str] = None,
    known_principals: Optional[List[str]] = None,
    allow_legacy: Optional[bool] = None,
) -> List[str]:
    """返回会话 ID 列表（进程内 + 持久库并集，重启后不会变空）。

    ``principal`` 非空时只返回该主体的会话 —— HTTP 面必须传令牌主体，
    否则一个用户能看到别人的会话名。``None`` 仅用于进程内全局视图
    （如驾驶舱聚合统计）。

    ``known_principals`` 用于排除"旧键其实是别人新键"的行；``allow_legacy``
    默认只对根/全局视图开放旧键（旧键没有主体段，见 ``_legacy_history_allowed``）。
    """
    try:
        persisted = get_conversation_store().list_session_ids(
            principal,
            known_principals or [],
            _legacy_history_allowed(principal) if allow_legacy is None else allow_legacy,
        )
    except Exception:  # noqa: BLE001 - 列表失败不能阻断接口；至少返回进程内会话
        logger.warning("could not list persisted chat sessions", exc_info=True)
        persisted = []
    with _sessions_lock:
        live = [
            _bare_session_id(key)
            for key in _sessions
            if principal is None or _owner_of(key) == principal
        ]
    return sorted(set(live) | set(persisted))


def delete_session(session_id: str, principal: Optional[str] = None) -> bool:
    """删除一个会话：清空其持久化历史并移除实例。返回是否成功。

    ``principal`` 非空时只能删自己的会话（复合键命中）；不传则按纯
    ``session_id`` 命中（仅供进程内/测试直接调用）。重启后实例已不在内存，
    此时按同一套新旧键规则清空持久库，保证列表里的会话能删掉。
    """
    key = _session_key(principal, session_id)
    with _sessions_lock:
        assistant = _sessions.pop(key, None)
        _session_users.pop(key, None)
        _session_last_used.pop(key, None)
    if assistant is not None:
        # reset 会清空该 principal 的对话历史（conversation store），不影响其它会话。
        assistant.reset()
        closer = getattr(assistant, "close", None)
        if callable(closer):
            try:
                closer()
            except Exception:
                pass
        return True
    # 没有活跃实例：从持久库清空（旧键只在根/全局视图下允许，避免越权删除）。
    store = get_conversation_store()
    cleared = False
    for candidate in _history_principal_candidates(principal, session_id):
        if store.has_rows(candidate):
            store.clear(candidate)
            cleared = True
    return cleared


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="用户消息")
    session_id: Optional[str] = Field("default", description="会话 ID（多轮独立）")
    model_id: Optional[str] = Field(
        None, description="模型配置 id 或名称；留空则用当前默认模型"
    )
    use_knowledge: Optional[bool] = Field(
        None,
        description=(
            "是否检索知识库并注入上下文。null/省略=自动（有文档就开），"
            "false=本次关闭，true=本次启用。"
        ),
    )
    # 客户端幂等键：用户连点两次/网络抖动重发时带上同一个值，
    # 服务端只执行一次并返回同一份回复。也可用 ``Idempotency-Key`` 头。
    client_request_id: Optional[str] = Field(
        None, max_length=128,
        description="客户端请求 id（同一请求重发时保持不变，用于去重）",
    )

    @field_validator("message")
    @classmethod
    def _check_message(cls, value: str) -> str:
        return normalize_required_text(value, "用户消息", max_chars=8000)

    @field_validator("session_id")
    @classmethod
    def _check_session_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return normalize_required_name(value, "会话 ID", max_chars=128)


class ChatResponse(BaseModel):
    reply: str
    status: str
    turn: int
    correlation_id: str
    agent: str
    #: 始终存在：说明本次是否使用知识库、命中来源；未使用必有原因。
    knowledge: Dict[str, object]
    #: 失败时的机器可读原因（quota_exhausted / key_revoked / ...），与 SSE
    #: done 帧的 ``reason`` 同源；成功时为 null。
    reason: Optional[str] = None
    #: 失败时上游返回的原文（只含 message，已脱敏、≤120 字）。前端只能在
    #: 可展开的「技术详情」里展示，不得拼进主文案。
    technical_detail: Optional[str] = None
    #: 中性中文失败说明（status != completed 时给出，含"被长度上限截断"）。
    error: Optional[str] = None
    #: 输出是否被长度上限截断（finish_reason=length/max_tokens）：内容真实
    #: 但没说完，客户端必须提示"继续"，不能当成完整回答。
    truncated: bool = False
    #: 非流式路径的"正在重试"提示（provider 重试回调写入）。
    notice: Optional[str] = None
    #: 本轮发生静默切换时，实际应答模型之前尝试过的模型标签。
    fell_back_from: Optional[List[str]] = None


def _assistant_for(req: "ChatRequest", principal: str) -> LiuHaoAssistant:
    """按账号 Key 作用域构造会话；越级/冒用在此转成稳定错误码。"""
    scope = key_scope_for(principal=principal)
    try:
        assistant = get_assistant(
            req.session_id, principal, req.model_id, key_scope=scope
        )
    except ModelConfigError as exc:
        # 请求级 correlation id：写进 key.use 审计并回给客户端，失败的 HTTP
        # 调用与审计条目因此可以一一对上（而不是只能按时间猜）。
        correlation_id = uuid.uuid4().hex[:16]
        audit_key_use(
            scope,
            None,
            outcome="denied",
            source="explicit",
            extra={"model_config_id": req.model_id, "error_code": exc.code},
            correlation_id=correlation_id,
        )
        detail: object = (
            {"code": exc.code, "message": exc.detail, "correlation_id": correlation_id}
            if exc.code
            else exc.detail
        )
        raise HTTPException(
            status_code=exc.status_code,
            detail=detail,
            headers={"X-LiuHao-Correlation-Id": correlation_id},
        )
    except _STORAGE_ERRORS as exc:
        # 会话历史库都打不开（磁盘满/只读/被占用）：明确 503，而不是英文 500。
        logger.warning("session history store failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "code": "storage_unavailable",
                "message": _public_storage_message(exc),
            },
        ) from exc
    owner = str(getattr(assistant.provider, "owner_principal", "") or "")
    if owner and owner != principal and scope.can_use(owner):
        # 主账号使用成员 Key：actor 仍是主账号，另记 key_owner 与授权来源。
        audit_key_use(scope, owner, outcome="allowed", source="authorized_share")
    return assistant


def _record_chat_usage(
    assistant: LiuHaoAssistant,
    principal: str,
    req: "ChatRequest",
    *,
    tokens_in: int,
    tokens_out: int,
    correlation_id: str,
    estimated: bool = False,
) -> bool:
    """记一行用量：账单归 ``key_owner``，用量归真实 actor。

    返回是否真的写入了账本。失败不阻断对话，但调用方必须感知到（流式结束帧
    携带 ``usage_recorded``，readiness 里也有 ``usage_ledger`` 状态）。
    """
    # 跨厂商 failover 后，额度实际消耗在"实际应答"的那家：账本归属必须跟随
    # 真实 provider，不能继续记在主模型名下（否则成本会记到错的模型/错的 Key）。
    owner = str(
        getattr(assistant.provider, "last_served_owner", "")
        or getattr(assistant.provider, "owner_principal", "")
        or ""
    ) or principal
    model = str(
        getattr(assistant.provider, "last_served_model", "")
        or getattr(assistant.provider, "model", "")
        or req.model_id
        or ""
    )
    recorded = record_usage(
        actor=principal,
        key_owner=owner,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        session_id=req.session_id,
        correlation_id=correlation_id,
        estimated=estimated,
    )
    if not recorded:
        logger.error(
            "chat usage was NOT recorded (session=%s actor=%s)", req.session_id, principal
        )
    return recorded


def _is_mock_provider(provider) -> bool:
    """判断这轮应答是否来自 Mock/演示 provider（不能算“真实可用”）。"""
    from ..ai.providers import MockProvider

    if isinstance(provider, MockProvider):
        return True
    # FailoverProvider 内部还包着一层；任何一个候选是 Mock 都保守跳过，
    # 不把“演示应答”写成“真实模型已验证”。
    for inner in getattr(provider, "_providers", ()) or ():
        if isinstance(inner, MockProvider):
            return True
    return False


def _mark_served_model_verified(assistant: LiuHaoAssistant, principal: str) -> None:
    """真实模型成功应答后回写「已验证」，让模型列表不再显示“未测试”。

    只在单次真实调用成功、且没有切到备用模型时回写：failover 后的主配置
    未必可用，不能冒领；Mock/演示应答也不算真实可用。回写失败不阻断对话。
    """
    provider = getattr(assistant, "provider", None)
    if provider is None or _is_mock_provider(provider):
        return
    if getattr(provider, "last_fell_back_from", None):
        return
    model_id = str(getattr(provider, "model_config_id", "") or "")
    if not model_id:
        return
    try:
        from ..ai.model_config import get_model_config_store
        from ..ai.providers import provider_mode_for

        store = get_model_config_store()
        config = store.resolve(model_id)
        # 诚实性判据：只有 provider 类型是真实服务时才回写。mock/未配置
        # （provider_mode_for 判为 "mock"）即使这一轮"成功"，也不能标成
        # 「已验证」——否则这个字段会被演示应答污染，用户会以为真的连通过。
        provider_type = str((config or {}).get("provider") or "")
        if provider_mode_for(provider_type) != "real":
            logger.debug(
                "skip marking model %s verified: provider mode is not real (%r)",
                model_id,
                provider_type,
            )
            return
        store.mark_verified(model_id, True, actor=principal or "chat")
    except Exception as exc:  # noqa: BLE001 -- 回写失败不影响本轮对话
        logger.warning(
            "could not mark model %s verified after successful chat: %s",
            model_id,
            exc,
        )


# ─── 重复提交去重（幂等）──────────────────────────────────────
#
# 缺陷背景：用户连点两次发送，或网络抖动导致同一请求重发时，后端会真的
# 调用两次模型、写两条历史、记两笔用量。这里在**进入生成之前**做去重：
#
# * 显式幂等键（请求体 ``client_request_id`` 或 ``Idempotency-Key`` 头）：
#   同一主体 + 同一键只执行一次，TTL 内重发直接拿到同一份回复；
# * 无显式键：按「主体 + 会话 + 模型 + 知识开关 + 消息正文」指纹在短时间窗
#   （默认 8 秒，``LIUHAO_CHAT_DEDUP_WINDOW_SECONDS`` 可调）内去重；
# * 并发重发：后来者等待首个请求完成并复用结果，**不会**各自调用模型；
# * 失败不缓存：只有 ``completed`` 的结果会被复用，失败后重试仍会真实执行。
#
# 状态是进程内内存（单机桌面产品只有一个后端进程），有容量上限与过期清理。

_DEDUP_WAIT_SECONDS = 300.0
_DEDUP_MAX_ENTRIES = 512
_DEDUP_WINDOW_DEFAULT = 8.0
_DEDUP_IDEMPOTENCY_TTL_DEFAULT = 3600.0
_DEDUP_SEP = "\x00"

_dedup_lock = threading.Lock()
_dedup_entries: Dict[str, "_DedupEntry"] = {}


class _DedupEntry:
    """一次「在途/已完成」请求的状态；``event`` 让并发重发等到结果。"""

    __slots__ = ("key", "ttl", "state", "payload", "event", "created_at", "finished_at")

    def __init__(self, key: str, ttl: float) -> None:
        self.key = key
        self.ttl = ttl
        self.state = "inflight"
        self.payload: Optional[Dict[str, object]] = None
        self.event = threading.Event()
        self.created_at = time.monotonic()
        self.finished_at = 0.0


def _dedup_window() -> float:
    raw = (os.environ.get("LIUHAO_CHAT_DEDUP_WINDOW_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else _DEDUP_WINDOW_DEFAULT
    except ValueError:
        value = _DEDUP_WINDOW_DEFAULT
    return max(0.0, min(value, 300.0))


def _dedup_idempotency_ttl() -> float:
    raw = (os.environ.get("LIUHAO_CHAT_IDEMPOTENCY_TTL_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else _DEDUP_IDEMPOTENCY_TTL_DEFAULT
    except ValueError:
        value = _DEDUP_IDEMPOTENCY_TTL_DEFAULT
    return max(1.0, min(value, 86400.0))


def _dedup_token(
    principal: str, req: "ChatRequest", header_key: object
) -> "tuple[Optional[str], float]":
    """计算去重键与有效期；返回 ``(None, 0)`` 表示本次不做去重。

    ``header_key`` 只在**真的是字符串**时才算显式键：FastAPI 的
    ``Header(...)`` 在直接函数调用（测试/内部调用）里会把参数默认值原样传进来，
    此时它是一个描述对象而不是 None；若把它 str 成键，所有请求会共用同一个
    idempotency key 并互相串结果。
    """
    header_value = header_key if isinstance(header_key, str) else ""
    explicit = header_value.strip() or str(req.client_request_id or "").strip()
    if explicit:
        # 显式键按主体隔离，避免两个账号用同一个 id 互相串结果。
        return f"idem{_DEDUP_SEP}{principal}{_DEDUP_SEP}{explicit}", _dedup_idempotency_ttl()
    window = _dedup_window()
    if window <= 0:
        return None, 0.0
    flag = (
        "true" if req.use_knowledge is True
        else "false" if req.use_knowledge is False
        else "auto"
    )
    raw = _DEDUP_SEP.join(
        [principal, str(req.session_id or ""), str(req.model_id or ""), flag, req.message]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"fp{_DEDUP_SEP}{digest}", window


def _prune_dedup(now: float) -> None:
    for key, entry in list(_dedup_entries.items()):
        if entry.state == "inflight":
            # 在途请求由等待方/超时逻辑处理，不能在这里删掉别人的占位。
            continue
        if now - entry.finished_at > entry.ttl:
            _dedup_entries.pop(key, None)


def _enforce_dedup_capacity() -> None:
    """容量上限：优先淘汰已完成最久的；全是在途时淘汰最早的占位。

    在途占位不能无限累积（客户端拿到 StreamingResponse 却永不消费时，生成器
    不会启动也不会收口）。超过上限时按下限淘汰，保证内存有界；去重本身是
    尽力而为，宁可少去重也不能拖垮进程。
    """
    if len(_dedup_entries) < _DEDUP_MAX_ENTRIES:
        return
    finished = sorted(
        (entry.finished_at, key)
        for key, entry in _dedup_entries.items()
        if entry.state != "inflight"
    )
    drain = max(1, len(_dedup_entries) - _DEDUP_MAX_ENTRIES + 1)
    for _, key in finished[:drain]:
        _dedup_entries.pop(key, None)
    if len(_dedup_entries) >= _DEDUP_MAX_ENTRIES:
        inflight = sorted(
            (entry.created_at, key)
            for key, entry in _dedup_entries.items()
            if entry.state == "inflight"
        )
        for _, key in inflight[:max(1, len(_dedup_entries) - _DEDUP_MAX_ENTRIES + 1)]:
            _dedup_entries.pop(key, None)


def _dedup_claim(
    key: Optional[str], ttl: float
) -> "tuple[Optional[_DedupEntry], Optional[Dict[str, object]]]":
    """占位或取回已有结果。

    返回 ``(entry, cached)``：``cached`` 非空 = 直接复用它；``entry`` 非空 =
    本请求是执行者，必须调用 ``_dedup_complete`` / ``_dedup_fail`` 收口。
    """
    if not key:
        return None, None
    deadline = time.monotonic() + _DEDUP_WAIT_SECONDS
    while True:
        with _dedup_lock:
            now = time.monotonic()
            _prune_dedup(now)
            entry = _dedup_entries.get(key)
            if entry is None:
                entry = _DedupEntry(key, ttl)
                _dedup_entries[key] = entry
                _enforce_dedup_capacity()
                return entry, None
            if entry.state == "completed":
                return entry, dict(entry.payload or {})
            if entry.state == "failed":
                # 首个请求失败了：本次请求成为新的执行者（真实重试）。
                _dedup_entries.pop(key, None)
                continue
            waiter = entry.event
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "chat dedup wait timed out after %.0fs; running without dedupe",
                _DEDUP_WAIT_SECONDS,
            )
            return None, None
        waiter.wait(timeout=remaining)


def _dedup_complete(entry: Optional[_DedupEntry], payload: Dict[str, object]) -> None:
    if entry is None:
        return
    with _dedup_lock:
        entry.payload = dict(payload)
        entry.state = "completed"
        entry.finished_at = time.monotonic()
        entry.event.set()


def _dedup_fail(entry: Optional[_DedupEntry]) -> None:
    """失败释放占位：后来的重试可以真实执行，且失败结果永不缓存。"""
    if entry is None:
        return
    with _dedup_lock:
        if _dedup_entries.get(entry.key) is entry:
            _dedup_entries.pop(entry.key, None)
        entry.state = "failed"
        entry.event.set()


def _cached_chat_response(cached: Dict[str, object]) -> ChatResponse:
    return ChatResponse(
        reply=str(cached.get("reply") or ""),
        status=str(cached.get("status") or "completed"),
        turn=int(cached.get("turn") or 0),
        correlation_id=str(cached.get("correlation_id") or ""),
        agent=str(cached.get("agent") or ""),
        knowledge=dict(cached.get("knowledge") or {}),
    )


def _replay_cached_stream(req: "ChatRequest", cached: Dict[str, object]):
    """把去重命中的结果重放成一份合法的 SSE 事件流（不再次调用模型）。"""
    reply = str(cached.get("reply") or "")

    def replay():
        if reply:
            yield f"data: {json.dumps({'type': 'token', 'content': reply}, ensure_ascii=False)}\n\n"
        done = {
            "type": "done",
            "status": str(cached.get("status") or "completed"),
            "turn": int(cached.get("turn") or 0),
            "session_id": req.session_id,
            "correlation_id": str(cached.get("correlation_id") or ""),
            "usage_recorded": bool(cached.get("usage_recorded")),
            "knowledge": dict(cached.get("knowledge") or {}),
            "deduplicated": True,
        }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    return replay()


def _denial_http_error(result: Dict[str, object]) -> HTTPException:
    """把对话拒绝结果转成 402/403（流式建流前与非流式共用同一口径）。

    402 = 预算/额度不足（`budget_exceeded`），403 = 没有被授权
    （`permission_denied`）。两者都带同一个 `correlation_id`，可直接对上
    审计里的 `ACCESS_DENIED`；响应头也回同一个值，便于用户贴给管理员。
    """
    denial_reason = str(result.get("reason") or "permission_denied")
    status_code = 402 if denial_reason == "budget_exceeded" else 403
    correlation_id = str(result.get("correlation_id") or "")
    return HTTPException(
        status_code=status_code,
        detail={
            "code": denial_reason,
            "message": str(result.get("reply") or ""),
            "correlation_id": correlation_id,
        },
        headers={"X-LiuHao-Correlation-Id": correlation_id},
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    principal: str = Depends(require_human_principal),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> ChatResponse:
    # 去重必须在任何生成/计费之前：连点两次或网络重发只执行一次。
    dedup_key, dedup_ttl = _dedup_token(principal, req, idempotency_key)
    dedup_entry, cached = _dedup_claim(dedup_key, dedup_ttl)
    if cached is not None:
        logger.info(
            "chat request deduplicated (session=%s principal=%s)",
            req.session_id, principal,
        )
        return _cached_chat_response(cached)
    try:
        # 主体只来自令牌；请求体里的 user_id（即使传了）一律不采信。
        try:
            assistant = _assistant_for(req, principal)
            # 先检索再生成：空库/不可用/检索失败都静默降级，元信息如实说明。
            knowledge = build_knowledge_context(
                req.message, enabled=req.use_knowledge, principal=principal
            )
        except Exception as exc:
            mapped = _storage_http_error(exc)
            if mapped is not None:
                raise mapped from exc
            raise

        # 同一会话串行：两个标签页并发写同一 session 时不能同时改 turn/history。
        with assistant.serialized_turn():
            with knowledge_prompt(assistant, knowledge):
                _begin_turn(assistant)
                try:
                    # 一轮 = 一个 meter：provider 每次真实调用都会累加进来（含工具多轮）。
                    with meter_scope() as meter:
                        try:
                            result = assistant.chat(req.message)
                        except _STORAGE_ERRORS as exc:
                            # 正文可能已生成，但会话/记忆没落盘：如实 503，不静默丢历史。
                            logger.warning("chat turn persist failed: %s", exc)
                            raise HTTPException(
                                status_code=503,
                                detail={
                                    "code": "storage_unavailable",
                                    "message": _public_storage_message(exc),
                                },
                            ) from exc
                        except Exception as exc:
                            # 知识库/个人上下文等底层 SQLite 直接抛磁盘满错误时，
                            # 同样给中文 503，而不是英文 500。
                            mapped = _storage_http_error(exc)
                            if mapped is not None:
                                raise mapped from exc
                            raise

                    usage = snapshot(meter)
                finally:
                    _end_turn(assistant)
            if result.get("status") == "completed":
                if usage["reported"]:
                    tokens_in = int(usage["tokens_in"])
                    tokens_out = int(usage["tokens_out"])
                    estimated = False
                else:
                    # 无 provider usage：用全项目统一的 estimate_tokens 估算，
                    # 与裁剪/配额口径一致；账本标记 estimated=1。
                    tokens_in = max(1, estimate_tokens(req.message))
                    tokens_out = max(1, estimate_tokens(result.get("reply")))
                    estimated = True
                _record_chat_usage(
                    assistant,
                    principal,
                    req,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    correlation_id=str(result.get("correlation_id") or ""),
                    estimated=estimated,
                )
                _mark_served_model_verified(assistant, principal)
        if result.get("status") == "denied":
            raise _denial_http_error(result)
        response = ChatResponse(
            reply=result["reply"],
            status=result["status"],
            turn=result["turn"],
            correlation_id=result["correlation_id"],
            agent=str(result.get("agent") or assistant.name),
            knowledge=public_metadata(knowledge),
            reason=(
                getattr(assistant, "last_turn_reason", None)
                if result["status"] != "completed"
                else None
            ),
            technical_detail=(
                getattr(assistant, "last_turn_technical_detail", None)
                if result["status"] != "completed"
                else None
            ),
            error=(
                getattr(assistant, "last_turn_error", None)
                if result["status"] != "completed"
                else None
            ),
            truncated=bool(result["status"] == "truncated"),
            notice=getattr(assistant, "last_turn_retry_notice", None),
            fell_back_from=(
                list(getattr(assistant.provider, "last_fell_back_from", []) or [])
                or None
            ),
        )
    except BaseException:
        # 异常（含客户端取消）不保留占位：重试必须能真实执行。
        _dedup_fail(dedup_entry)
        raise
    if response.status == "completed":
        _dedup_complete(dedup_entry, response.model_dump())
    else:
        _dedup_fail(dedup_entry)
    return response


@router.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    principal: str = Depends(require_human_principal),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
) -> StreamingResponse:
    """流式对话（Server-Sent Events）。

    每个事件一行 ``data: {json}``：

    - ``{"type": "token", "content": "..."}``  逐 token
    - ``{"type": "tool", "name": ..., "args": ..., "output": ...}``  工具调用
    - ``{"type": "done", "turn": N, ...}``      结束帧（落盘/审计已完成）

    **预算/权限判定在建流之前完成**：拒绝时直接物理返回 ``402 budget_exceeded``
    / ``403 permission_denied``（JSON + ``X-LiuHao-Correlation-Id``），不会
    建立 ``text/event-stream``。只有建流之后才发生的兜底拒绝（等会话锁期间
    额度被别的会话用光）才用 ``done`` 帧的 ``status=denied`` + ``reason``
    表达 —— 那时 HTTP 状态已发出，改不了。

    重复提交（连点/重发）同样走幂等：后来者不会第二次调用模型，而是等首个
    流结束后重放同一份内容（结束帧带 ``deduplicated: true``）。
    """
    dedup_key, dedup_ttl = _dedup_token(principal, req, idempotency_key)
    dedup_entry, cached = _dedup_claim(dedup_key, dedup_ttl)
    if cached is not None:
        return StreamingResponse(
            _replay_cached_stream(req, cached), media_type="text/event-stream"
        )
    turn_prepared = False
    try:
        assistant = _assistant_for(req, principal)
        # 授权闸门必须在 build_knowledge_context / StreamingResponse 之前：
        # 被拒绝的请求不该先做检索，更不该先拿到 200 + SSE 再被告知拒绝。
        preflight = getattr(assistant, "preflight_chat", None)
        if callable(preflight):
            denial = preflight(req.message)
            if denial is not None:
                raise _denial_http_error(denial)
            # 本轮 turn/correlation 已由 preflight 开好，生成器不能再开一轮。
            turn_prepared = True
        knowledge = build_knowledge_context(
            req.message, enabled=req.use_knowledge, principal=principal
        )
    except Exception as exc:
        _dedup_fail(dedup_entry)
        mapped = _storage_http_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    except BaseException:
        _dedup_fail(dedup_entry)
        raise

    # 首个请求的最终结果；只有 completed 才会被缓存供重发复用。
    outcome: Dict[str, object] = {"payload": None}

    # 客户端断开时由消费端置位：生成线程据此停止消费、按「用户中断」收口。
    from ..ai.providers import TurnCancelToken

    cancel_token = TurnCancelToken()

    def event_stream():
        # 提示词注入必须覆盖整个惰性生成周期；生成器关闭时 finally 恢复原值。
        reply_chars = 0
        reply_text = ""
        finished = False
        usage_recorded = False
        storage_error: Optional[Exception] = None
        # 生成器本身没兜住、冒到网关层的异常：也必须补 done 帧，不能静默截断。
        unexpected_error: Optional[Exception] = None
        _begin_turn(assistant)
        meter = UsageMeter()
        try:
            with knowledge_prompt(assistant, knowledge):
                # 建流前已开轮（turn_prepared）时不能再开一轮；生成器内部仍
                # 会再授权一次，作为"等锁期间额度被用光"的兜底 —— 那次拒绝
                # 只能走 done 帧（SSE 已建立，HTTP 状态改不了）。
                stream_kwargs: Dict[str, object] = {}
                if turn_prepared and _accepts_kwarg(
                    assistant.chat_stream, "turn_prepared"
                ):
                    stream_kwargs["turn_prepared"] = True
                if _accepts_kwarg(assistant.chat_stream, "cancel_token"):
                    stream_kwargs["cancel_token"] = cancel_token
                stream = assistant.chat_stream(req.message, **stream_kwargs)
                while True:
                    # Starlette 每次 next() 可能在不同 context 执行，因此逐次
                    # 把 meter 挂到当前 context，provider 的 usage 才不会丢。
                    try:
                        with use_meter(meter):
                            item = next(stream)
                    except StopIteration:
                        break
                    if isinstance(item, dict):
                        # 工具调用事件：结构化透传（type/name/args/output）。
                        yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                    else:
                        reply_chars += len(str(item))
                        reply_text += str(item)
                        payload = {"type": "token", "content": item}
                        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finished = True
        except _STORAGE_ERRORS as exc:
            # 落盘失败发生在最后一个 token 之后：若不发错误帧，客户端只会看到
            # 流被截断（静默失败）。这里明确告知"回复已生成但历史没保存"。
            storage_error = exc
        except Exception as exc:
            # 底层 SQLite 裸抛磁盘满/只读错误：同样走 SSE 错误帧，不静默截断。
            if is_storage_failure(exc):
                storage_error = exc
            else:
                # G+：真实异常必须在流里补 done（带中文文案与机器原因），
                # 否则用户只看到半截内容，永远不知道这不是完整回答。
                unexpected_error = exc
                logger.exception("chat stream failed unexpectedly")

        finally:
            _end_turn(assistant)
            # 记账必须放在 finally：客户端断开时生成器被关闭，走不到 finally
            # 之后的代码，但 provider 已经真实消耗了 token（且可能已上报 usage）。
            usage = snapshot(meter)
            turn_status = (
                str(getattr(assistant, "last_turn_status", "") or "completed")
                if finished
                else "interrupted"
            )
            correlation_id = str(
                getattr(assistant, "last_turn_correlation_id", "") or ""
            )
            if turn_status in ("completed", "truncated"):
                # truncated 的 token 是真实消耗（内容也真实产出），照实记账；
                # 失败轮次仍然不记账。
                if usage["reported"]:
                    tokens_in = int(usage["tokens_in"])
                    tokens_out = int(usage["tokens_out"])
                    estimated = False
                else:
                    tokens_in = max(1, estimate_tokens(req.message))
                    tokens_out = max(1, estimate_tokens(reply_text))
                    estimated = True
                usage_recorded = _record_chat_usage(
                    assistant,
                    principal,
                    req,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    correlation_id=correlation_id,
                    estimated=estimated,
                )
            elif (
                not finished
                and storage_error is not None
                and (usage["reported"] or reply_chars)
            ):
                # 回复已生成、只是历史没保存：token 是真实成本，照实记账。
                # 真正的生成异常不记账（失败轮次不得产生"已用量"假账）。
                if usage["reported"]:
                    tokens_in = int(usage["tokens_in"])
                    tokens_out = int(usage["tokens_out"])
                    estimated = False
                else:
                    tokens_in = max(1, estimate_tokens(req.message))
                    tokens_out = max(1, estimate_tokens(reply_text))
                    estimated = True
                usage_recorded = _record_chat_usage(
                    assistant,
                    principal,
                    req,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    correlation_id=correlation_id,
                    estimated=estimated,
                )
        if cancel_token.cancelled:
            # 客户端已断开：没有人接收，补帧会失败；审计已按 interrupted 落好。
            return
        if storage_error is not None:
            message = (
                str(storage_error)
                if isinstance(storage_error, _STORAGE_ERRORS)
                else describe_storage_failure(storage_error, "数据目录")
            )
            payload = {
                "type": "error",
                "code": "storage_unavailable",
                "message": message,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            # G+：错误帧之后仍必须补 done，否则前端只看到"流结束了"却没有结论。
            yield f"data: {json.dumps(_done_payload(req, assistant, knowledge, status='error', reason='storage_unavailable', error=message, usage_recorded=usage_recorded), ensure_ascii=False)}\n\n"
            return
        if unexpected_error is not None:
            from ..ai.providers import technical_detail_for, user_facing_failure

            model = getattr(getattr(assistant, "provider", None), "model", None)
            failure_text = user_facing_failure(unexpected_error, model=model)
            reason = getattr(unexpected_error, "upstream_kind", None)
            done = _done_payload(
                req,
                assistant,
                knowledge,
                status="error",
                reason=reason if isinstance(reason, str) and reason else None,
                error=failure_text,
                usage_recorded=usage_recorded,
            )
            detail = technical_detail_for(unexpected_error)
            if detail:
                done["technical_detail"] = detail
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
            return
        if not finished:
            return
        # 生成器耗尽时 chat_stream 已完成落盘 + 审计，先记用量再发结束帧。
        # 只有真实成功的轮次才记账：失败/拒绝不能产生"已用量"假账。
        turn_status = str(getattr(assistant, "last_turn_status", "") or "completed")
        turn_error = getattr(assistant, "last_turn_error", None)
        correlation_id = str(getattr(assistant, "last_turn_correlation_id", "") or "")
        # 发结束帧：status 让前端能区分成功/失败/拒绝，而不是把 200 + done
        # 当成成功。
        done = {
            "type": "done",
            "status": turn_status,
            "turn": assistant.turn,
            "session_id": req.session_id,
            "correlation_id": correlation_id,
            "usage_recorded": usage_recorded,
            "knowledge": public_metadata(knowledge),
        }
        if turn_status == "truncated":
            # 不能只靠 status：老客户端可能只认 done 就收尾，必须显式标出来。
            done["truncated"] = True
        fell_back_from = getattr(assistant.provider, "last_fell_back_from", None)
        if isinstance(fell_back_from, list) and fell_back_from:
            # 跨厂商切换必须可审计：写明"这一轮之前试过谁"。
            done["fell_back_from"] = [str(item) for item in fell_back_from]
        if turn_status != "completed":
            if not turn_error:
                # 技术细节只进日志；事件流里也必须是中性中文说明。
                from ..ai.providers import GENERATION_FAILURE_MESSAGE

                turn_error = GENERATION_FAILURE_MESSAGE
            done["error"] = str(turn_error)
            # 机器可读的失败原因（quota_exhausted / key_revoked / ...）：
            # 文案是中性化的，前端没法从 error 里重新分类；不带它，"去充值"
            # 就会被笼统的"请求失败"盖掉。
            turn_reason = getattr(assistant, "last_turn_reason", None)
            if isinstance(turn_reason, str) and turn_reason:
                done["reason"] = turn_reason
            # 上游原文只进可展开的「技术详情」（已脱敏、≤120 字）。
            turn_detail = getattr(assistant, "last_turn_technical_detail", None)
            if isinstance(turn_detail, str) and turn_detail:
                done["technical_detail"] = turn_detail
        if turn_status == "completed":
            # 真实模型这一轮成功了：把“已验证”回写到模型配置，模型列表
            # 不再显示“未测试”。Mock/failover 由 helper 内部排除。
            _mark_served_model_verified(assistant, principal)
            # 只有成功轮次才把完整内容缓存给重发请求复用。
            outcome["payload"] = {
                "reply": reply_text,
                "status": turn_status,
                "turn": assistant.turn,
                "correlation_id": correlation_id,
                "usage_recorded": usage_recorded,
                "knowledge": public_metadata(knowledge),
            }
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"

    def serialized_stream():
        # 与 /v1/chat 一致：整轮（含 done 帧与记账）占用会话锁，避免两个标签页
        # 并发写同一 session 时轮次号/历史互相覆盖。客户端断开时 yield from
        # 会把 close 透传给内层生成器，finally 中的记账仍会执行。
        try:
            turn_guard = (
                assistant.serialized_turn(cancel_token=cancel_token)
                if _accepts_kwarg(assistant.serialized_turn, "cancel_token")
                else assistant.serialized_turn()
            )
            with turn_guard:
                yield from event_stream()
        finally:
            payload = outcome.get("payload")
            if isinstance(payload, dict):
                _dedup_complete(dedup_entry, payload)
            else:
                _dedup_fail(dedup_entry)

    return StreamingResponse(
        _stream_with_cancel(serialized_stream(), cancel_token),
        media_type="text/event-stream",
    )


def _readable_session_owners(context: AccountContext) -> Tuple[str, ...]:
    """本次请求可读的会话归属主体：根覆盖整棵子树，成员只有自己。"""
    return tuple(context.subtree) if context.is_root else (context.principal,)


def _session_owner_for_read(
    context: AccountContext, session_id: str
) -> Optional[str]:
    """解析该会话实际归属的**可读**主体；不可读与不存在一律返回 ``None``。

    返回值只用于选择读取的数据，不得据此改变"无权 / 不存在"的响应形状：
    两者必须继续走同一个中性 404（或同一个空历史），否则就成了存在性探针。
    """
    owners = _readable_session_owners(context)
    with _sessions_lock:
        live_keys = set(_sessions)
    # 先看进程内活跃实例：否则会出现"活跃=404、只落盘=200"的第二种口径。
    for owner in owners:
        if _session_key(owner, session_id) in live_keys:
            return owner
    store = get_conversation_store()
    for owner in owners:
        for candidate in _history_principal_candidates(owner, session_id):
            if store.has_rows(candidate):
                return owner
    return None


def _neutral_not_found() -> HTTPException:
    return deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def _empty_history_payload(
    session_id: str, principal: str, limit: Optional[int], offset: int
) -> Dict[str, object]:
    """无权或不存在时的空历史（不读取、不判断对方是否存在）。"""
    return {
        "session_id": session_id,
        "history": [],
        "count": 0,
        "total": 0,
        "has_more": False,
        "limit": limit,
        "offset": max(0, int(offset or 0)),
        "user_id": principal,
        "profile_principal": f"user-{principal}",
    }


@router.get("/chat/stats")
def chat_stats(
    session_id: str = "default",
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    owner = _session_owner_for_read(context, session_id)
    if owner is None:
        if context.standalone:
            # 单账号老部署没有"别人的会话"，保留旧语义（默认会话可空统计）。
            return get_assistant(
                session_id, context.principal, key_scope=key_scope_for(context)
            ).stats()
        raise _neutral_not_found()
    if owner != context.principal:
        # 根读下级会话时只读统计，不构造下级的 assistant：避免 root 的
        # key_scope 改写下级实例的 provider 绑定（读操作不该有写副作用）。
        history_principal = _resolve_history_principal(owner, session_id)
        store = get_conversation_store()
        return {
            "principal": history_principal,
            "provider": None,
            "model": None,
            "turn": store.get_last_turn(history_principal),
            "history_messages": store.load_page(history_principal)["total"],
        }
    return get_assistant(
        session_id, context.principal, key_scope=key_scope_for(context)
    ).stats()


def _list_sessions_in_scope(
    context: AccountContext, limit: Optional[int], offset: int
) -> Tuple[Dict[str, object], Dict[str, str]]:
    """按可读范围聚合会话列表，并给出 会话 -> 归属主体 映射。

    根账号合并整棵子树（子会话仍用裸 session_id，``users`` 标出归属）；
    成员只列自己。同名会话在多个账号下出现时保留先出现者的归属，列表本身
    仍只有一条，避免分页出现重复项。
    """
    mapping: Dict[str, str] = {}
    for owner in _readable_session_owners(context):
        for session_id in list_sessions(
            owner,
            known_principals=list(context.subtree),
            allow_legacy=context.is_root and owner == context.principal,
        ):
            mapping.setdefault(session_id, owner)
    session_ids = sorted(mapping)
    total = len(session_ids)
    start = max(0, int(offset or 0))
    page_ids = (
        session_ids[start:]
        if limit is None
        else session_ids[start:start + max(0, int(limit))]
    )
    return {
        "sessions": page_ids,
        "total": total,
        "has_more": start + len(page_ids) < total,
        "limit": limit,
        "offset": start,
    }, {session_id: mapping[session_id] for session_id in page_ids}


@router.get("/chat/sessions")
def chat_sessions(
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=1000,
        description="本页会话数；省略=全量（旧客户端兼容）。前端默认 50。",
    ),
    offset: int = Query(0, ge=0, description="跳过多少条（按稳定字母序）"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """列出当前主体可读的会话。

    ``users`` 给出 会话→主体 归属：成员恒为自己；根账号会看到子树里的会话
    并在这里标出它实际属于哪个账号。归属是服务端派生事实，不接受客户端声明。
    """
    page, users = _list_sessions_in_scope(context, limit, offset)
    sessions = page["sessions"]
    return {
        "sessions": sessions,
        "count": len(sessions),
        "total": page["total"],
        "has_more": page["has_more"],
        "limit": limit,
        "offset": page["offset"],
        "users": users,
    }


def read_history(
    session_id: str = "default",
    principal: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> Dict[str, object]:
    """从持久化 store 读取某会话的历史（按主体+会话复合隔离）。

    新旧键都兼容：优先当前键 ``liuhao-{principal}-{session}``，根主体在
    新键无数据时回落到 0.1.0 的 ``liuhao-{session}``。

    ``offset`` 从**最近一条**往前数：``limit=200&offset=0`` 是最新 200 条，
    "加载更早"传 ``offset=已加载条数``。省略 ``limit`` 时保持旧的全量语义。

    ``profile_principal`` 单独给出该会话读写的画像主体：有主体时恒为
    ``user-{principal}``（跨会话共享）。
    """
    history_principal = _resolve_history_principal(principal, session_id)
    profile_principal = f"user-{principal}" if principal else history_principal
    page = get_conversation_store().load_page(
        history_principal, limit=limit, offset=offset
    )
    return {
        "session_id": session_id,
        "history": page["history"],
        "count": len(page["history"]),
        "total": page["total"],
        "has_more": page["has_more"],
        "limit": limit,
        "offset": page["offset"],
        "user_id": principal,
        "profile_principal": profile_principal,
    }


@router.get("/chat/history")
def chat_history(
    session_id: str = "default",
    limit: Optional[int] = Query(
        None, ge=1, le=2000,
        description="本页消息数（从最近一条往前）；省略=全量（旧客户端兼容）",
    ),
    offset: int = Query(0, ge=0, description="从最近一条往前跳过多少条消息"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """返回某会话的历史消息（按可读范围读，跨后端重启有效）。

    无权与不存在必须**完全同形**：账号树里省略 ``limit`` 时一律中性 404；
    显式分页（控制台首屏）则一律返回空历史。两个分支都不依赖"该名字是否
    属于别人"，所以无法据此探测会话是否存在。
    """
    owner = _session_owner_for_read(context, session_id)
    if owner is None:
        if context.standalone or limit is not None:
            return _empty_history_payload(
                session_id, context.principal, limit, offset
            )
        raise _neutral_not_found()
    return read_history(session_id, owner, limit=limit, offset=offset)


def _search_in_scope(
    context: AccountContext, q: str, limit: int, offset: int
) -> Dict[str, object]:
    """在当前可读范围内搜索；根账号合并子树并标出每条结果的归属。"""
    owners = _readable_session_owners(context)
    store = get_conversation_store()
    if len(owners) == 1:
        return store.search(
            owners[0],
            q,
            limit=limit,
            offset=offset,
            known_principals=context.subtree,
        )
    merged: List[Dict[str, object]] = []
    totals = 0
    ready = True
    degraded = False
    indexed = 0
    index_total = 0
    notices: List[str] = []
    modes: List[str] = []
    for owner in owners:
        result = store.search(
            owner,
            q,
            limit=min(200, offset + limit),
            offset=0,
            known_principals=context.subtree,
        )
        totals += int(result.get("total") or 0)
        ready = ready and bool(result.get("ready", True))
        degraded = degraded or bool(result.get("degraded"))
        indexed += int(result.get("indexed") or 0)
        index_total += int(result.get("index_total") or 0)
        if result.get("notice"):
            notices.append(str(result["notice"]))
        if result.get("mode"):
            modes.append(str(result["mode"]))
        for item in result.get("results") or []:
            merged.append({**item, "account": owner})
    merged.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
    page = merged[offset:offset + limit]
    return {
        "results": page,
        "count": len(page),
        "total": totals,
        "has_more": offset + len(page) < totals,
        "limit": limit,
        "offset": offset,
        "mode": "+".join(dict.fromkeys(modes)) or "merged",
        "degraded": degraded,
        "ready": ready,
        "indexed": indexed,
        "index_total": index_total,
        "notice": notices[0] if notices else "",
    }


@router.get("/sessions/search")
def search_sessions(
    q: str = Query(..., min_length=1, max_length=200, description="搜索词（中文子串可直接搜）"),
    limit: int = Query(20, ge=1, le=200, description="本页结果数"),
    offset: int = Query(0, ge=0, description="跳过多少条结果"),
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """在当前账号可读的会话历史里全文搜索。

    * 中文按 2 字及以上子串命中（CJK bigram 索引）；
    * 索引建立中会返回 ``ready=false`` 与进度 ``indexed/index_total``，
      结果可能不完整；
    * FTS5 不可用或单字查询时降级为逐行扫描，``degraded=true`` 且
      ``notice`` 会明确写「搜索变慢」——绝不静默变慢。

    根账号合并子树搜索；每条结果的 ``account`` 标出它属于哪个账号。
    """
    result = _search_in_scope(context, q, limit, offset)
    result["query"] = q
    return result


@router.delete("/chat/sessions/{session_id}")
def chat_delete_session(
    session_id: str,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, object]:
    """删除**自己的**会话（清空历史 + 移除实例）；别人的会话按不存在处理（404）。"""
    if not delete_session(session_id, context.principal):
        raise _neutral_not_found()
    return {"session_id": session_id, "deleted": True}
