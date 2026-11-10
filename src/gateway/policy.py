"""鎏灏 Policy Controlled 审批端点（内核层真拦截的**真实入口**）。

内核层 44 个 ``@kernel_action`` 中，HIGH/CRITICAL 共 17 个动作在开启拦截后，必须由
**经核验的人类**授权才能执行（OD-010）。C-3 提供了 ``human_sovereign`` 上下文，
C-4 把授权本身变成可审计凭据。本模块提供那个凭据的**真实签发/查询/撤销入口**。

端点：

- ``POST   /v1/policy/approvals``         签发审批凭据（人工授权 HIGH/CRITICAL 动作）
- ``GET    /v1/policy/approvals``         列出当前有效的审批凭据
- ``DELETE /v1/policy/approvals/{id}``    撤销审批凭据
- ``GET    /v1/policy/enforcement``       查看内核层拦截的当前配置（只读）

安全设计（**关键**）
--------------------
1. **主体只来自身份令牌**：``principal`` 一律取自 ``Authorization: Bearer <JWT>``
   的 ``sub``，**绝不从请求体读取**。请求体里就算带 ``principal`` 也不会被采信
   —— 否则任何人都能替别人"授权"，那正是 C-3 通道此前唯一的真实缺口。
2. **签发前先验人**：``principal`` 必须是身份内核中**存在且 ACTIVE 的非 service
   身份**，否则 400 拒绝（``issue_grant`` 内校验）。service 身份永远不能持有
   人类主权。
3. **凭据有限**：动作集必须全部是**被拦截门管辖的 HIGH/CRITICAL 动作**
   （白名单由 ``_risk_classification`` 唯一裁决），TTL 有上限，可撤销。
4. **有据可查**：签发/撤销各写一条 ``HUMAN_SOVEREIGNTY_OVERRIDE`` 审计事件；
   内核动作执行时的审计事件带 ``sovereignty_grant`` 字段，构成
   「动作 ← 凭据 ← 授权人」的完整因果链。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..common.request_context import current_origin
from ..kernels._enforcement import describe as describe_enforcement
from ..kernels._risk_classification import get_kernel_action_risk
from .rate_limiter import identity_unavailable_exception
from ..kernels._sovereignty import (
    DEFAULT_GRANT_TTL_SECONDS,
    MAX_GRANT_TTL_SECONDS,
    WILDCARD_TARGET,
    get_grant,
    issue_grant,
    list_grants,
    revoke_grant,
)

router = APIRouter(prefix="/v1", tags=["policy"])

#: Header the console carries its bearer token in.
#:
#: Deliberately *not* ``Authorization``. On the managed host that serves the
#: published bundle a front gateway (``CloudStudio Gateway``) overwrites
#: ``Authorization`` with its own JWT before the request reaches this app.
#: Measured, not assumed: a request with no such header, one with a 17-char
#: canary and one with a real 824-char token all arrived as the *same*
#: 379-char ``HS256`` token -- so on that host every ``Authorization``-based
#: check rejects everybody, and login "works" only because it reads no header.
#: A header the gateway does not know passes through untouched, and a value
#: the infrastructure rewrites is not something to authenticate against
#: anyway. ``Authorization`` is still honoured so local and Docker
#: deployments are unchanged.
TOKEN_HEADER = "X-Liuhao-Token"

#: ``Authorization`` remains accepted as the fallback for non-proxied hosts.
STANDARD_TOKEN_HEADER = "Authorization"


def extract_bearer_token(*values: Optional[str]) -> Optional[str]:
    """Return the first well-formed ``Bearer <token>`` from ``values``.

    Order matters: the caller passes the private header first, so a rewritten
    ``Authorization`` can never shadow a token the client actually sent.
    """
    for value in values:
        if not value:
            continue
        scheme, _, token = value.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            continue
        return token.strip()
    return None


def require_bearer_payload(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Validate a bearer token and return the verified payload.

    The token is read from :data:`TOKEN_HEADER` first and ``Authorization``
    second -- see that constant for why the private header has to win.

    This is the **single** implementation of "bearer token -> verified claims"
    in the gateway. ``/v1/auth/*`` depends on it too, so there is exactly one
    place where a token is parsed and checked -- two implementations would be
    two chances to disagree.

    The JWT signature is verified by ``src.security``; a caller-supplied
    identity is never trusted (see the module docstring, point 1).

    On top of signature verification this performs the **account revocation
    precheck** (fail-closed): a suspended/deleted account, or a token whose
    ``account_epoch`` no longer matches the account, is refused immediately --
    downgrades and suspensions therefore bite on the very next request instead
    of waiting for the token to expire.
    """
    token = extract_bearer_token(x_liuhao_token, authorization)
    if token is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未提供访问令牌，请先登录"},
        )
    try:
        from ..security import get_jwt_handler
        from ..security.jwt_handler import IdentityStoreUnavailable

        payload = get_jwt_handler().validate_access_token(token)
    except IdentityStoreUnavailable:
        # 撤销库读不出来是**本机基础设施故障**，不是令牌无效：回 503，
        # 文案明确"这次不是你密码的问题"。与下面的 401 是两套话、两个码。
        #
        # **仅测试路径**：真实 HTTP 请求在到达本依赖前，已经由
        # ``GlobalRateLimitMiddleware``（全部 /v1）与 ``RateLimitMiddleware``
        # （受保护前缀）完成身份解析，503 由它们先行返回；本分支唯一被直接
        # 调用覆盖的地方是 tests/test_overload_isolation.py 的
        # ``test_identity_store_unavailable_maps_to_503_not_401``（另有"中间件
        # 解析成功后数据库立刻再次卡住"的窄竞态）。因此保留而不是删除，形状
        # 必须与 rate_limiter.identity_unavailable_payload 的四字段一致。
        raise identity_unavailable_exception("unavailable")
    except Exception:  # noqa: BLE001 - 具体原因不外泄（可能含解析细节）
        raise HTTPException(
            status_code=401,
            detail={"code": "AUTH_TOKEN_INVALID", "message": "访问令牌无效或已过期，请重新登录"},
        )
    _account_revocation_precheck(payload)
    return payload


def optional_bearer_payload(
    x_liuhao_token: Optional[str] = Header(None),
    authorization: Optional[str] = Header(None),
):
    """Like :func:`require_bearer_payload`, but ``None`` when no token is sent.

    A *present but invalid* token is still rejected: silently treating it as
    anonymous would widen visibility exactly when someone is probing.
    """
    token = extract_bearer_token(x_liuhao_token, authorization)
    if token is None:
        return None
    return require_bearer_payload(x_liuhao_token, authorization)


def _account_revocation_precheck(payload) -> None:
    """Refuse tokens whose account is no longer valid (fail-closed).

    Principals without an account node are **not** refused: that is the legacy
    single-account / standalone-peer path, whose behaviour must stay identical.
    """
    subject = str(getattr(payload, "sub", "") or "").strip()
    if not subject:
        return
    try:
        from ..kernels.identity import get_identity_manager
    except Exception:  # noqa: BLE001 - kernel unavailable -> old behaviour
        return
    node = get_identity_manager().get_account(subject)
    if node is None:
        return
    if node.status != "active":
        raise HTTPException(
            status_code=403,
            detail={"code": "ACCOUNT_UNAVAILABLE", "message": "该账号当前不可用"},
        )
    metadata = getattr(payload, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if "account_epoch" not in metadata:
        return  # legacy token minted before the tree existed
    try:
        token_epoch = int(metadata.get("account_epoch"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=401,
            detail={"code": "CREDENTIAL_STALE", "message": "凭据已失效，请重新登录"},
        )
    if token_epoch != int(node.security_epoch):
        raise HTTPException(
            status_code=401,
            detail={"code": "CREDENTIAL_STALE", "message": "凭据已失效，请重新登录"},
        )


#: Roles/permissions that authorise a write. Same vocabulary as the model
#: management gate (``models.require_model_admin``), so there is one answer to
#: "who is an administrator" rather than two that can drift apart.
_WRITE_ROLES = frozenset({"admin", "administrator", "owner"})

#: Highest human sovereignty scope. The seeded local human carries no
#: permissions but ``scope=L0``; treating L0 as administrative keeps the
#: single-human deployment usable without inventing a role nobody holds.
#: Any unknown/absent scope is *not* promoted to L0: authorisation is an
#: explicit claim, never a default.
_WRITE_SCOPES = frozenset({"L0"})

logger = logging.getLogger(__name__)

#: ``issue_grant`` / ``revoke_grant`` 抛出的 ValueError 原文是给开发者看的
#: （"unknown principal"、"enforcement-gated" 之类）。同一类错误在界面上必须
#: 说成"发生了什么 + 下一步"；原始英文交给日志。
_APPROVAL_REFUSALS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (
        ("unknown principal", "not registered"),
        "这个账号还没有登记，无法签发授权。请先在账号设置里创建并启用这个人，"
        "然后重新登录后再试。",
    ),
    (
        ("not active", "inactive", "disabled"),
        "这个账号当前不可用（已停用或未激活），无法签发授权。请先启用该账号后再试。",
    ),
    (
        ("service", "machine", "not a human"),
        "机器账号不能签发人工授权。请改用个人账号登录后再试。",
    ),
    (
        ("unknown kernel action", "unknown action"),
        "选中的动作不存在，无法签发授权。请刷新页面后从列表里重新选择。",
    ),
    (
        ("enforcement-gated", "not eligible", "not gated"),
        "选中的动作不需要审批（只有高风险动作才可授权）。请改选列表中的高风险动作。",
    ),
    (
        ("at least one action", "actions must"),
        "请至少选择一个需要授权的动作。",
    ),
    (
        ("explicit target", "wildcard", "confirmation", "non-empty", "higher-sovereignty"),
        "请填写这条授权对应的具体对象；如果要授权全部对象，必须勾选二次确认。",
    ),
    (
        ("expired", "revoked"),
        "这条授权已失效（已撤销或已过期），请重新签发一条。",
    ),
)


def _approval_refusal(exc: BaseException, *, fallback: str) -> str:
    """把审批相关的内部异常翻成中性中文；原文只进日志。"""
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    for needles, message in _APPROVAL_REFUSALS:
        if any(needle in lowered for needle in needles):
            logger.info("approval refused: %s", text)
            return message
    logger.warning("approval request failed (%s): %s", type(exc).__name__, text)
    return fallback


#: Every scope the identity kernel can issue. Anything else in a token's
#: metadata is unknown and therefore non-authorising (fail-closed).
_KNOWN_SCOPES = frozenset({"L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7"})


def _claim_set(value) -> set:
    return {str(item).lower() for item in (value or [])}


def _human_claims(payload) -> tuple[str, dict]:
    """Return ``(subject, metadata)`` for a human token or raise 401/403."""
    subject = getattr(payload, "sub", None)
    if not subject:
        raise HTTPException(
            status_code=401,
            detail="登录凭据不完整，无法确认你的账号。请退出后重新登录再试。",
        )
    metadata = getattr(payload, "metadata", None)
    metadata = metadata if isinstance(metadata, dict) else {}
    if metadata.get("kind") != "human":
        raise HTTPException(
            status_code=403,
            detail=(
                "当前登录的不是个人账号，不能执行这个操作。"
                "请改用管理员为你创建的个人账号登录后重试。"
            ),
        )
    return str(subject), metadata


def _is_admin_claim(payload, metadata: dict) -> bool:
    scope = str(metadata.get("scope") or "")
    if scope not in _KNOWN_SCOPES:
        return False
    claims = _claim_set(getattr(payload, "roles", None)) | _claim_set(
        getattr(payload, "permissions", None)
    )
    return bool(claims & _WRITE_ROLES) or scope in _WRITE_SCOPES


def require_human_principal(payload=Depends(require_bearer_payload)) -> str:
    """Resolve the authenticated human principal from the bearer token, or 401.

    Deliberately *only* the token can name the principal -- see the module
    docstring, point 1. This dependency never trusts a caller-supplied
    identity.

    **Authentication only.** Authorisation is decided per business action by
    ``require_admin_principal`` (or the surface's own gate), not by the HTTP
    verb: ``POST /v1/chat``, ``POST /v1/profile`` and session creation are read
    semantics and must not be blocked just because they use POST.
    """
    subject, _metadata = _human_claims(payload)
    return subject


def require_admin_principal(payload=Depends(require_bearer_payload)) -> str:
    """Authenticate a human *and* require an explicit administrative claim.

    Used by actions that genuinely change governance state (issuing/revoking
    approval grants, kernel lifecycle, plugin activation) rather than by
    ordinary business operations.
    """
    subject, metadata = _human_claims(payload)
    if not _is_admin_claim(payload, metadata):
        raise HTTPException(
            status_code=403,
            detail=(
                "该操作需要管理员权限，本次操作没有完成。"
                "请用管理员账号执行，或让管理员授予权限后再试。"
            ),
        )
    return subject


class ApprovalRequest(BaseModel):
    """审批凭据签发请求。

    注意：**没有** ``principal`` 字段 —— 主体只能来自身份令牌。

    ``target`` 是**必填**的：授权的最小单位是 (principal, action, target)，
    缺省 target 不能解释成"任意 target"，否则一份只为一台资源签发的批准会
    静默覆盖全部资源。要授权全部 target，必须显式传 ``target="*"`` **并且**
    ``confirm_wildcard=True``（二次确认）。
    """

    actions: List[str] = Field(..., min_length=1, description="被授权的内核动作名（HIGH/CRITICAL）")
    target: str = Field(
        ...,
        min_length=1,
        description=(
            "被授权的具体 target（资源标识）。必填；授权仅对该 target 生效。"
            f"授权全部 target 需显式写 '{WILDCARD_TARGET}' 并同时置 confirm_wildcard=true。"
        ),
    )
    confirm_wildcard: bool = Field(
        False,
        description=(
            f"把 target 设为 '{WILDCARD_TARGET}' 时的二次确认。仅为全目标授权时需要；"
            "默认 false，此时通配会被拒绝。"
        ),
    )
    reason: str = Field("", max_length=500, description="授权理由（记入审计）")
    pending_id: str = Field(
        "",
        max_length=64,
        description=(
            "可选的待处理条目 id（pending:<seq>）。审批中心带上它，同一张卡片"
            "重复点击只会得到同一条凭据，不会因为多点一次而叠加第二份授权。"
        ),
    )
    ttl_seconds: float = Field(
        DEFAULT_GRANT_TTL_SECONDS,
        gt=0,
        le=MAX_GRANT_TTL_SECONDS,
        description=f"有效期秒数，上限 {MAX_GRANT_TTL_SECONDS:.0f}",
    )


#: 手机上不允许"一键批准"的风险档。这两个档正是现有策略会 defer 的档，
#: 也是"必须当面点头"这条底线的全部内容。
_REMOTE_BLOCKED_RISKS = frozenset({"HIGH", "CRITICAL"})
_RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _approval_risk_tier(req: ApprovalRequest) -> str:
    """这条批准请求对应的风险档：优先按待确认卡片，否则按动作白名单取最高档。"""
    if req.pending_id:
        now = time.time()
        for item in _pending_items(now):
            if str(item.get("pending_id")) == str(req.pending_id):
                return str(item.get("risk_level") or "UNKNOWN").upper()
        # 找不到卡片：不猜、不放行。
        return "UNKNOWN"
    tiers = [str(get_kernel_action_risk(action) or "UNKNOWN").upper() for action in req.actions]
    if not tiers:
        return "UNKNOWN"
    return max(tiers, key=lambda tier: _RISK_ORDER.get(tier, 3))


def _refuse_remote_approval(req: ApprovalRequest) -> None:
    """手机（局域网来源）只能拒绝，不能批准高风险动作。

    拒绝没有副作用、可以远程做；批准意味着"人类当面对这件事点了头"，
    远程批准等于把这条底线拆掉。UNKNOWN 按最坏情况处理。
    """
    if req.confirm_wildcard:
        raise HTTPException(
            status_code=403,
            detail=(
                "手机端不能确认「通配目标」授权（target=*）。"
                "请回到电脑本机完成这次确认。"
            ),
        )
    tier = _approval_risk_tier(req)
    if tier in _REMOTE_BLOCKED_RISKS or tier == "UNKNOWN":
        raise HTTPException(
            status_code=403,
            detail=(
                "这个动作被判为高风险，只能在电脑本机确认。"
                "手机端可以查看它、也可以拒绝它，但不能批准。"
            ),
        )


@router.post("/policy/approvals", response_model=Dict[str, Any])
def create_approval(
    req: ApprovalRequest,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """签发一条审批凭据（审计留痕）。主体取自令牌，请求体无法指定。"""
    # 来自局域网手机的批准：高危动作必须回到电脑本机。
    if current_origin().is_lan:
        _refuse_remote_approval(req)
    if req.pending_id:
        # 一次批准只生效一次：同一张待确认卡片重复提交时复用已有凭据，
        # 不叠加第二份授权（界面双击、网络重试都不会放大权限）。
        wanted = set(req.actions)
        for existing in list_grants():
            if str(existing.principal) != principal:
                continue
            if set(existing.actions) != wanted or str(existing.target) != req.target:
                continue
            return {
                "approved": True,
                "already_approved": True,
                "grant": existing.to_dict(),
            }
    try:
        grant = issue_grant(
            principal,
            req.actions,
            target=req.target,
            reason=req.reason,
            ttl_seconds=req.ttl_seconds,
            issued_by=principal,
            # Wildcard authority carries through from the request's explicit
            # second confirmation; without it, target="*" is refused (400).
            allow_wildcard=req.confirm_wildcard,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=_approval_refusal(
                exc,
                fallback="这条授权请求没有通过校验，本次操作没有完成。请检查填写内容后重试。",
            ),
        ) from exc
    return {"approved": True, "grant": grant.to_dict()}


def _approval_scope(payload) -> tuple[Any, Optional[List[str]], str]:
    """审批凭据的可读主体集合（与 audit.py 的账号树口径一致）。

    * 成员：``[自己]``；根：整棵子树（凭据里自带 ``issued_by`` 标出签发主体）；
    * 无账号节点的老部署：L0 不设限、其余只看自己；
    * 非经核验令牌（进程内直接调用）不设限；HTTP 上不可达。
    """
    if payload is None or not hasattr(payload, "sub"):
        return None, None, "all"
    from .audit import _account_scope, _scope_filter

    context, sovereign = _account_scope(payload)
    principals, mode = _scope_filter(context, sovereign, None)
    return context, principals, mode


def _approval_read_scope(context, principals, mode) -> Dict[str, Any]:
    return {
        "mode": mode,
        "principals": principals if principals is not None else "all",
        "root_view": bool(
            context is not None and context.is_root and mode == "subtree"
        ),
    }


@router.get("/policy/approvals", response_model=Dict[str, Any])
def list_approvals(
    include_expired: bool = False,
    include_revoked: bool = False,
    _: str = Depends(require_human_principal),
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """列出审批凭据（默认只看有效的；账号作用域=自己或子树）。"""
    context, principals, mode = _approval_scope(payload)
    grants = list_grants(
        include_expired=include_expired, include_revoked=include_revoked
    )
    if principals is not None:
        visible = set(principals)
        grants = [g for g in grants if str(g.principal) in visible]
    return {
        "count": len(grants),
        "grants": [g.to_dict() for g in grants],
        "read_scope": _approval_read_scope(context, principals, mode),
        "enforcement": describe_enforcement(),
    }


#: How far back the pending queue looks. A blocked action older than this is
#: still in the audit chain (queryable at ``/v1/audit/events``); it just stops
#: being counted as "waiting on me right now" after a week.
_PENDING_LOOKBACK_SECONDS = 7 * 24 * 3600
_PENDING_SCAN_LIMIT = 500
#: 用户在审批中心明确拒绝的标记；与内核拦截记录同源（审计链），不另造台账。
_REJECT_DECISION = "reject"


def _pending_items(now: float) -> List[Dict[str, Any]]:
    """从审计链派生「还在等我确认」的动作（未经拒绝过滤）。"""
    from .audit import _read_events

    events = _read_events(
        outcome="blocked",
        since=now - _PENDING_LOOKBACK_SECONDS,
        limit=_PENDING_SCAN_LIMIT,
        reverse=True,
    )

    # Actions already covered by a live grant are not waiting on anybody.
    covered: Dict[str, str] = {}
    for grant in list_grants():
        for action in grant.actions:
            covered.setdefault(action, grant.grant_id)

    items: List[Dict[str, Any]] = []
    seen: set = set()
    for event in events:
        details = event.get("details") or {}
        if details.get("policy_decision") != "defer":
            continue
        action = details.get("action") or event.get("action")
        if not action:
            continue
        dedupe_key = (action, event.get("correlation_id") or "")
        if dedupe_key in seen:
            # Same action blocked again in the same execution -- one card, not N.
            continue
        seen.add(dedupe_key)
        if action in covered:
            continue
        risk = details.get("risk_level") or "UNKNOWN"
        pending_id = f"pending:{event.get('seq')}"
        items.append({
            "pending_id": pending_id,
            "action": action,
            "risk_level": risk,
            # 这条动作是谁、从哪触发的：local（本机）/ lan（同一 Wi-Fi 的手机）。
            "source_channel": details.get("source_channel") or "local",
            "source_device": details.get("source_device"),
            # 手机端据此禁用"批准"：HIGH/CRITICAL 只能回电脑确认。
            "remote_approvable": str(risk).upper() not in _REMOTE_BLOCKED_RISKS,
            "policy_rule": details.get("policy_rule"),
            "correlation_id": event.get("correlation_id"),
            "blocked_at": event.get("timestamp"),
            "blocked_by": event.get("principal_id"),
            "reason": "动作被策略暂缓，需要你确认后才能继续执行",
            "summary": f"{action}（风险 {risk}）正在等待你确认",
            "approve": {
                "method": "POST",
                "path": "/v1/policy/approvals",
                "body": {
                    "actions": [action],
                    "target": "*",
                    "reason": "通过审批中心确认挂起的动作",
                    "ttl_seconds": 900,
                    # 同一张卡片重复提交只会复用同一条凭据（见 create_approval）。
                    "pending_id": pending_id,
                },
            },
        })
    return items


def _rejected_pending_ids(now: float) -> set:
    """读回审计链里"用户已拒绝"的 pending id。"""
    from .audit import _read_events

    rejected: set = set()
    events = _read_events(
        outcome="denied",
        since=now - _PENDING_LOOKBACK_SECONDS,
        limit=_PENDING_SCAN_LIMIT,
        reverse=True,
    )
    for event in events:
        details = event.get("details") or {}
        if details.get("policy_decision") != _REJECT_DECISION:
            continue
        pending_id = str(details.get("pending_id") or "")
        if pending_id:
            rejected.add(pending_id)
    return rejected


class ApprovalRejection(BaseModel):
    """审批拒绝请求。理由可为空，但字段固定存在并写进审计。"""

    pending_id: str = Field(..., min_length=1, max_length=64,
                            description="待处理条目 id（pending:<seq>）")
    reason: str = Field("", max_length=500, description="拒绝理由（可留空，记入审计）")


@router.post("/policy/approvals/reject", response_model=Dict[str, Any])
def reject_pending_approval(
    req: ApprovalRejection,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """明确拒绝一条待确认动作（审计留痕，不产生任何可执行权限）。

    为什么需要：此前审批中心只有「批准」，用户面对高危动作只剩「同意」这一个
    选择，等同于强制放行。拒绝的语义是「明确不批」：动作在被拦下时就没有执行，
    拒绝只做两件事——写审计、让卡片不再显示为等待中；它不会制造授权，
    也不会重放或自动重试原动作。
    """
    from src.kernels.audit import AuditEventType, AuditScope, log_event

    now = time.time()
    item = next(
        (i for i in _pending_items(now) if i["pending_id"] == req.pending_id),
        None,
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail="这条待确认动作不存在或已经被处理，请刷新后重新查看。",
        )
    if req.pending_id in _rejected_pending_ids(now):
        # 重复点击幂等：不再写第二条审计。
        return {
            "rejected": True,
            "already_rejected": True,
            "pending_id": req.pending_id,
            "action": item["action"],
            "reason": req.reason,
        }
    try:
        log_event(
            AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE,
            principal_id=principal,
            scope=AuditScope.L0,
            outcome="denied",
            details={
                "action": item["action"],
                "risk_level": item["risk_level"],
                "policy_decision": _REJECT_DECISION,
                "policy_rule": item.get("policy_rule"),
                "policy_enforced": True,
                "pending_id": req.pending_id,
                "rejection_reason": req.reason,
                "summary": f"用户在审批中心拒绝了 {item['action']}",
            },
            correlation_id=item.get("correlation_id"),
        )
    except Exception as exc:  # 拒绝本身也要留痕；审计写不进去就不能报成功
        raise HTTPException(
            status_code=503,
            detail="这次拒绝没有记录下来，请稍后重试。",
        ) from exc
    return {
        "rejected": True,
        "already_rejected": False,
        "pending_id": req.pending_id,
        "action": item["action"],
        "reason": req.reason,
    }


@router.get("/policy/approvals/pending", response_model=Dict[str, Any])
def list_pending_approvals(
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """列出**等待当前人工确认**的动作（只读）。

    审批中心此前只有"已签发凭据"，没有"还有什么在等我"。数据来源是内核
    真实的拦截记录：HIGH/CRITICAL 动作被 ``PolicyEffect.DEFER`` 拦下时，审计
    链会写一条 ``outcome="blocked"``、``policy_decision="defer"`` 的事件
    （``deny`` / ``error`` 是硬拒绝，不需要人批，因此不计入）。

    已有一条有效凭据覆盖的动作不再算"待处理"——它已经批过了；被用户明确
    拒绝的条目也不再显示（拒绝记录同样在审计链里）。
    ``approve`` 字段给出可照抄的下一步请求，使页面能直接处理而不是只显示。

    可见性：只有**能真正签发凭据**的人类主体才看得到这个队列
    （``require_admin_principal``，与 ``POST /v1/policy/approvals`` 同一道
    门）。非管理员不是"看到空列表"，而是 403 —— 跨主体不泄露。
    """
    now = time.time()
    rejected = _rejected_pending_ids(now)
    items = [i for i in _pending_items(now) if i["pending_id"] not in rejected]

    return {
        "count": len(items),
        "items": items,
        "as_of": now,
        "lookback_seconds": _PENDING_LOOKBACK_SECONDS,
        "enforcement": describe_enforcement(),
    }


@router.get("/policy/approvals/{grant_id}", response_model=Dict[str, Any])
def get_approval(
    grant_id: str,
    _: str = Depends(require_human_principal),
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """按 id 读取一条审批凭据（含已过期/已撤销；作用域=自己或子树）。"""
    context, principals, mode = _approval_scope(payload)
    grant = get_grant(grant_id)
    if grant is None:
        raise HTTPException(
            status_code=404,
            detail="没有找到这条授权，可能已经撤销。请刷新审批列表后重试。",
        )
    if principals is not None and str(grant.principal) not in set(principals):
        # 存在但不在你的子树里 —— 与"不存在"同一个 404，不泄露存在性。
        raise HTTPException(
            status_code=404,
            detail="没有找到这条授权，可能已经撤销。请刷新审批列表后重试。",
        )
    body = grant.to_dict()
    body["read_scope"] = _approval_read_scope(context, principals, mode)
    return body


@router.delete("/policy/approvals/{grant_id}", response_model=Dict[str, Any])
def revoke_approval(
    grant_id: str,
    reason: str = "",
    principal: str = Depends(require_admin_principal),
    payload=Depends(require_bearer_payload),
) -> Dict[str, Any]:
    """撤销一条审批凭据（审计留痕）。重复撤销幂等。

    管理员也只能撤销**自己子树内**主体的 grant；跨根撤销一律与"授权不存在"
    同一个中性 404（否则 L0 管理员可凭 id 枚举并撤销任意根下的授权）。
    无账号节点的独立管理员（``principals is None``）仍保留全局权限。
    """
    _, principals, _mode = _approval_scope(payload)
    grant = get_grant(grant_id)
    if grant is not None and principals is not None:
        if str(grant.principal) not in set(principals):
            raise HTTPException(
                status_code=404,
                detail="这条授权不存在或已经撤销，本次操作没有完成。"
                       "请刷新后重新查看。",
            )
    try:
        grant = revoke_grant(grant_id, revoked_by=principal, reason=reason)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="这条授权不存在或已经撤销，本次操作没有完成。请刷新后重新查看。",
        ) from exc
    return {"revoked": True, "grant": grant.to_dict()}


@router.get("/policy/enforcement", response_model=Dict[str, Any])
def get_enforcement(_: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """内核层拦截配置快照（只读）。默认 ``enabled: false`` = 记录型（L1）。"""
    return describe_enforcement()
