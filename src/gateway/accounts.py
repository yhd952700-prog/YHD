"""账号树 HTTP 面：注册 / 成员 / 权限 / Key 共享 / 状态 / 恢复 / 备份。

设计要点（与 ``docs/account-hierarchy.md`` 对齐）：

* 身份内核负责"谁"，账号树负责"能对谁操作"；本模块只做 HTTP 编排，不复制判定；
* 所有拒绝都是稳定错误码 + 中性文案，响应里不出现 ``parent/depth/root``；
* 首个注册者成为唯一根；此后只允许在已有账号下创建成员（激活码一次性）；
* 写操作一律从令牌取主体，请求体里的 ``principal`` 只作为**目标**，绝不作为 actor；
* 恢复码/激活码/凭据包都只存哈希，明文只在成功响应里出现一次。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..common.atomic_write import StorageWriteError
from ..kernels.identity import _accounts, get_identity_manager
from . import account_settings, local_proof, recovery_handoff
from .account_context import (
    AccountContext,
    deny,
    from_account_error,
    require_account_principal,
    require_manage,
    require_visible,
)
from .auth import issue_token_for, resolve_secret_store, rotate_credential

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/accounts", tags=["accounts"])

#: 首启初始化在**进程内**串行：两个并发的首次注册请求若各自先写凭据，
#: 后写者会覆盖先写者的密码；内核的单根判定只能保证"只建一个根"，
#: 保不住"根登录用的就是最后成功那次的密码"。
_ROOT_INIT_LOCK = threading.Lock()

#: 初始化停在半路（根已落盘、凭据没落盘）时的稳定错误码。
CODE_INITIALIZATION_INCOMPLETE = "ACCOUNT_INITIALIZATION_INCOMPLETE"

#: 首次初始化/补全只接受本机来源时的稳定错误码（中性文案，不透露实例状态）。
CODE_INITIALIZATION_LOCAL_ONLY = "ACCOUNT_INITIALIZATION_LOCAL_ONLY"

#: 恢复码交接/重置只接受本机来源时的稳定错误码。
CODE_RECOVERY_LOCAL_ONLY = "ACCOUNT_RECOVERY_LOCAL_ONLY"

#: 本机来源但缺少/重放了桌面端一次性证明时的稳定错误码。
CODE_RECOVERY_PROOF_REQUIRED = "ACCOUNT_RECOVERY_PROOF_REQUIRED"

#: 可信启动模式下，HTTP 对端进程与网关不属于同一个 Windows 用户。
CODE_LOCAL_USER_MISMATCH = "ACCOUNT_LOCAL_USER_MISMATCH"

#: 凭据包导出只接受本机来源时的稳定错误码。
CODE_BACKUP_EXPORT_LOCAL_ONLY = "ACCOUNT_BACKUP_EXPORT_LOCAL_ONLY"


# --------------------------------------------------------------------------- #
# 请求体
# --------------------------------------------------------------------------- #

class RegisterRootRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: Optional[str] = Field(None, max_length=80)


class CreateMemberRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    display_name: Optional[str] = Field(None, max_length=80)
    role: Optional[str] = Field(None, description="角色预设：viewer / operator / manager")
    permissions: Optional[List[str]] = None
    allow_key_sharing: bool = Field(False, description="允许团队共享我的 Key（默认关闭）")


class ActivateRequest(BaseModel):
    principal: str = Field(..., min_length=1, max_length=64)
    code: str = Field(..., min_length=4, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


class PermissionsRequest(BaseModel):
    grant: List[str] = Field(default_factory=list)
    revoke: List[str] = Field(default_factory=list)


class KeySharingRequest(BaseModel):
    allow: bool


class StatusRequest(BaseModel):
    status: str = Field(..., description="active / suspended / deleted")
    reason: str = Field("", max_length=200)


class PasswordResetRequest(BaseModel):
    password: str = Field(..., min_length=8, max_length=256)


class RecoveryResetRequest(BaseModel):
    recovery_code: str = Field(..., min_length=4, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


class BackupExportRequest(BaseModel):
    scope: str = Field("all", description="all（全部可见范围）或 self")
    passphrase: str = Field(..., min_length=8, max_length=256)


class BackupImportRequest(BaseModel):
    bundle: Dict[str, Any]
    passphrase: str = Field(..., min_length=8, max_length=256)


class SelfRegisterRequest(BaseModel):
    """自助注册只接受"我是谁"与密码；父子/深度/权限字段一律丢弃。"""

    # 显式 ignore：请求体里出现 parent_id/depth/root_id/permissions 也不生效。
    model_config = {"extra": "ignore"}

    principal: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: Optional[str] = Field(None, max_length=80)


class SelfRegistrationSettingsRequest(BaseModel):
    allow: bool


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #

def _kernel_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except _accounts.AccountError as exc:
        raise from_account_error(exc)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - 账号面不得 500 裸奔
        logger.exception("account operation failed")
        raise deny(500, "ACCOUNT_INTERNAL_ERROR", "服务暂时不可用")


def _public_member(node: _accounts.AccountNode) -> Dict[str, Any]:
    """**不含** parent_id/depth/root_id —— 成员与根看到同一形状。"""
    return _accounts.public_member(node, show_key_authorization=True)


def _require_scope(context: AccountContext, scope: str, *, code: str) -> None:
    if not context.has_scope(scope):
        raise deny(404, code, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)


def _credential_record(principal: str) -> Optional[Dict[str, Any]]:
    """该主体当前的凭据记录（只读快照）；没有或读不出来都返回 ``None``。"""
    try:
        return resolve_secret_store().credential_for(principal)
    except Exception:  # noqa: BLE001 - 读失败按"没有凭据"处理（fail-closed）
        return None


def _write_credential(principal: str, password: str, *, message: str) -> None:
    """写凭据，失败一律转成可重试的 500（绝不留下"半套初始化"）。"""
    try:
        ok = rotate_credential(principal, password)
    except Exception:  # noqa: BLE001 - CredentialStoreError 等，本次未落盘
        logger.exception("could not write credential for %r", principal)
        ok = False
    if not ok:
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", message)


def _restore_credential(principal: str, snapshot: Optional[Dict[str, Any]]) -> None:
    """把凭据库恢复到提交前：没有就删除，有就原样写回。"""
    store = resolve_secret_store()
    try:
        if snapshot is None:
            store.remove_credential(principal)
        else:
            store.import_credentials({principal: snapshot})
    except Exception:  # noqa: BLE001 - 回滚失败要显式记录，不能静默
        logger.exception("could not roll back credential for %r", principal)


# --------------------------------------------------------------------------- #
# 首次初始化的来源门禁
# --------------------------------------------------------------------------- #

#: ``testclient`` 是 Starlette TestClient 在进程内伪造的来源名，真实 socket
#: 不可能产生它；只用于让同进程测试表现为"本机请求"。
_LOOPBACK_CLIENT_NAMES = frozenset({"localhost", "testclient"})


def _is_loopback_client(host: str) -> bool:
    """来源地址是否回环（含 IPv6 方括号形式）。"""
    value = (host or "").strip().lower()
    if not value:
        return False
    if value.startswith("[") and "]" in value:
        value = value[1:value.find("]")]
    if value in _LOOPBACK_CLIENT_NAMES:
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def request_is_local(request: Request) -> bool:
    """请求是否来自本机。

    只认回环对端。``X-Forwarded-For`` 只有在对端**本来就是回环**（本机反代）
    时才参与判定，且整条转发链都必须是回环 —— 远程直连者无法伪造出回环对端，
    所以不会被冒充成本机。
    """
    client = getattr(request, "client", None)
    peer = str(getattr(client, "host", "") or "") if client else ""
    if not _is_loopback_client(peer):
        return False
    forwarded = request.headers.get("x-forwarded-for") or ""
    for hop in forwarded.split(","):
        hop = hop.strip()
        if hop and not _is_loopback_client(hop):
            return False
    return True


def _require_local_for_initialization(request: Request) -> None:
    """首次初始化/补全只允许本机来源；远程一律中性拒绝。"""
    if request_is_local(request):
        return
    raise deny(
        403,
        CODE_INITIALIZATION_LOCAL_ONLY,
        "该操作只能在本机完成：请在这台机器上打开控制台或使用本机命令行重试。",
    )


# --------------------------------------------------------------------------- #
# 本机恢复码交接（忘记密码 + 手上没有恢复码）
# --------------------------------------------------------------------------- #

#: 交接文件写在数据目录的 ``config/`` 下，与安装版布局一致；内容由
#: :mod:`src.gateway.recovery_handoff` 以 DPAPI（优先）或 ACL + 短 TTL 保护。
LOCAL_RECOVERY_FILE_NAME = recovery_handoff.LOCAL_RECOVERY_FILE_NAME


def _clear_local_recovery_file() -> None:
    """删除上一次交接留下的码文件：新码一生成，上一次的码就作废了。"""
    recovery_handoff.clear_handoff()
    recovery_handoff.clear_legacy_notice()


def _require_loopback(request: Request, *, message: str) -> None:
    """本机交接只认回环来源；远程一律中性拒绝。"""
    if request_is_local(request):
        return
    raise deny(403, CODE_RECOVERY_LOCAL_ONLY, message)


_PROOF_MESSAGE = (
    "本机恢复流程还需要桌面端的一次性证明：请从鎏灏桌面窗口点“打开控制台”"
    "重新进入后再试（直接在浏览器里输入地址不算本机证明）。"
)


def _require_same_user(request: Request) -> None:
    """可信启动模式下，HTTP 调用进程必须与网关属于同一个 Windows 用户。

    回环 + 令牌仍然不够：如果令牌通过浏览器历史/崩溃转储泄露，同机另一个
    Windows 账户拿着它就能发 HTTP。这里用 TCP 表反查对端 PID，再比对进程
    令牌里的用户 SID，把“同一台机器”收紧成“同一个 Windows 账户”。

    开发模式（未设置 ``LIUHAO_TRUSTED_LAUNCH``）不阻断，只告警；非 Windows
    无法解析 TCP 对端身份，返回 ``None``，同样按运行模式处理。
    """
    if not local_proof.trusted_launch():
        return
    verdict = local_proof.peer_is_same_user(request)
    if verdict is True:
        return
    client = getattr(request, "client", None)
    logger.warning(
        "本机请求与网关不属于同一 Windows 用户：verdict=%s client=%s",
        verdict,
        getattr(client, "host", "?"),
    )
    raise deny(
        403,
        CODE_LOCAL_USER_MISMATCH,
        "该操作只允许启动鎏灏的那个 Windows 账户完成，请回到桌面窗口重试。",
    )


def _require_bootstrap_source(request: Request) -> str:
    """首次注册的来源门禁，返回已占用的交接令牌（空串表示开发模式放行）。

    可信启动（桌面壳/启动器）下，首次注册等同于“认领这台机器上的唯一根”，
    必须同时满足：回环 + 与网关同一 Windows 用户 + 桌面端一次性证明。
    直接 ``python -m src.gateway`` 的开发用法不设置 ``LIUHAO_TRUSTED_LAUNCH``，
    只打印醒目告警并放行，避免把本地开发锁死。
    """
    _require_local_for_initialization(request)
    _require_same_user(request)
    raw = local_proof.request_token(request, local_proof.HANDOFF_HEADER)
    if raw and local_proof.claim_token(raw, scope=local_proof.SCOPE_HANDOFF):
        return raw
    if not local_proof.trusted_launch():
        logger.warning(
            "开发模式：首次注册未校验桌面端证明（设置 %s=1 可强制启用）",
            local_proof.TRUSTED_LAUNCH_ENV,
        )
        return ""
    raise deny(
        403,
        CODE_RECOVERY_PROOF_REQUIRED,
        "首次注册需要从鎏灏桌面窗口点“打开控制台”进入后再试"
        "（直接在浏览器里输入地址不算本机可信入口）。",
    )


def _require_local_proof(request: Request) -> None:
    """恢复码交接：回环之外，还必须持有桌面端通过 DACL 通道发放的一次性令牌。

    回环只证明“同一台机器”，不证明“同一个账户”。令牌由网关的命名管道
    发给当前 Windows 用户，HTTP 请求本身不再被当作身份证明。
    """
    _require_same_user(request)
    raw = local_proof.request_token(request, local_proof.HANDOFF_HEADER)
    if raw and local_proof.redeem_token(raw, scope=local_proof.SCOPE_HANDOFF):
        return
    raise deny(403, CODE_RECOVERY_PROOF_REQUIRED, _PROOF_MESSAGE)


def _require_initialization_proof(request: Request) -> str:
    """半初始化补全：必须是本机桌面端发放的一次性证明，而不只是回环。

    回环只说明“同一台机器”，同机其他 Windows 账户也能连 127.0.0.1，并且
    可能已经知道根账号名。令牌由网关的 DACL 命名管道只发给网关所属用户，
    因此“知道名字”不再等于“能接管”。
    """
    _require_same_user(request)
    raw = local_proof.request_token(request, local_proof.HANDOFF_HEADER)
    if raw and local_proof.claim_token(raw, scope=local_proof.SCOPE_HANDOFF):
        return raw
    raise deny(
        403,
        CODE_RECOVERY_PROOF_REQUIRED,
        "补全初始化需要桌面端的一次性证明：请从鎏灏桌面窗口点“打开控制台”"
        "重新进入后再试（直接在浏览器里输入地址不算本机证明）。",
    )


def _require_recovery_source(request: Request) -> str:
    """重置来源判定，返回已占用的重置令牌（空串表示受控运维开关放行）。

    默认：本机 + 一次性重置令牌。远程只在显式开关打开时放行（受控运维，
    风险见 :func:`recovery_handoff.remote_recovery_enabled`）。
    令牌在这里“占用”而不是立即消费：改密失败时可以释放重试，并发请求里
    只有一个能占用成功（避免同一令牌并发重置两次都成功）。
    """
    local = request_is_local(request)
    if not local:
        if recovery_handoff.remote_recovery_enabled():
            # 开关一开就放宽了“码只能在本机用”的边界，必须留下审计线索。
            logger.warning(
                "非本机来源通过了恢复码重置（%s=1）",
                recovery_handoff.REMOTE_RECOVERY_ENV,
            )
            return ""
        raise deny(
            403,
            CODE_RECOVERY_LOCAL_ONLY,
            "恢复码重置只能在本机完成：请在这台机器的控制台重试，"
            "或由管理员显式开启远程恢复。",
        )

    # 可信启动下，本机也要是“同一个 Windows 用户”，否则拿泄露的令牌也没用。
    _require_same_user(request)
    token = local_proof.request_token(request, local_proof.RESET_HEADER)
    if token:
        if local_proof.claim_token(token, scope=local_proof.SCOPE_RESET):
            return token
        raise deny(403, CODE_RECOVERY_PROOF_REQUIRED, _PROOF_MESSAGE)
    if recovery_handoff.remote_recovery_enabled():
        logger.warning(
            "本机来源但未携带一次性证明，按受控运维开关放行（%s=1）",
            recovery_handoff.REMOTE_RECOVERY_ENV,
        )
        return ""
    raise deny(403, CODE_RECOVERY_PROOF_REQUIRED, _PROOF_MESSAGE)


# --------------------------------------------------------------------------- #
# 静态路由（必须排在 ``/{principal}`` 之前）
# --------------------------------------------------------------------------- #

@router.get("/roles")
def list_roles() -> Dict[str, Any]:
    """角色预设：前端直接渲染，不暴露任何层级信息。"""
    return {
        "roles": [
            {"id": name, "permissions": sorted(_accounts.resolve_role(name, None))}
            for name in sorted(_accounts.ROLE_PRESETS)
        ],
        "scopes": sorted(_accounts.GRANTABLE_SCOPES),
    }


def _settle_existing_root(
    manager,
    root: _accounts.AccountNode,
    payload: RegisterRootRequest,
    request: Request,
) -> Dict[str, Any]:
    """根已存在：要么"已完成"（409），要么"初始化停在半路"（可恢复）。"""
    if _credential_record(root.principal) is not None:
        # 已完成初始化。交给内核产生 409 并写审计，**绝不先碰凭据**。
        _kernel_call(
            manager.register_root_account,
            payload.principal,
            display_name=payload.display_name,
        )
        raise deny(
            409,
            _accounts.CODE_ROOT_REGISTRATION_CLOSED,
            "系统已完成初始化，请使用已有账号登录",
        )
    # 半初始化补全只允许本机：远程匿名者既不能猜名接管，也看不到任何提示。
    _require_local_for_initialization(request)
    # 回环仍然不等于“本人”：同机其他账户也能连回环，并可能已经知道根账号名。
    # 补全必须持有桌面端通过 DACL 命名管道发放的一次性证明。
    proof = _require_initialization_proof(request)
    if payload.principal != root.principal:
        local_proof.release_token(proof)
        # 正文**不回显**根账号名：否则匿名者拿到名字就能借补全路径接管。
        raise deny(
            409,
            CODE_INITIALIZATION_INCOMPLETE,
            "初始化尚未完成。请使用首次注册时的名称在本机重新提交以完成初始化"
            "（密码可以重新设置）。",
        )
    # 恢复路径：上一轮凭据写入失败，根落盘但没有凭据 —— 任何人都登录不了，
    # 所以这是"重新完成初始化"，而不是"已有账号被接管"。
    prior = _credential_record(root.principal)
    try:
        _write_credential(
            root.principal,
            payload.password,
            message="账号初始化失败，可重新提交完成初始化",
        )
        recovery_code = _kernel_call(
            manager.rotate_recovery_code, root.principal
        )
    except HTTPException:
        # 恢复码没签出来就不要把凭据留下：回到"可重试"的半初始化状态。
        _restore_credential(root.principal, prior)
        local_proof.release_token(proof)
        raise
    except Exception:
        local_proof.release_token(proof)
        raise
    local_proof.consume_token(proof)
    _clear_local_recovery_file()
    node = _kernel_call(manager.get_account, root.principal)
    return {
        "account": _public_member(node),
        "recovery_code": recovery_code,
        "resumed": True,
        "notice": "初始化已补全；恢复码只显示这一次，请离线保存，它可用于重置密码。",
    }


@router.post("/register")
def register_root(payload: RegisterRootRequest, request: Request) -> Dict[str, Any]:
    """首个注册者成为唯一根；已完成初始化时 409（并写审计）。

    顺序契约（P0-1）：**先凭据、后身份**。凭据写失败时账号树一个字节都没写，
    接口 500 之后同一个请求可以原样重试；反过来（先身份后凭据）一旦凭据写
    失败，根已经落盘 → ``/v1/auth/config`` 认为已初始化、再注册 409、登录
    401，用户既进不去也重开不了。

    来源契约：**未初始化**（无根 / 根有但无凭据）时只接受本机来源，否则
    任何能连上网关的人都能抢注成根。已完成初始化的 409 保持中性文案，不向
    远程透露根账号名。
    """
    manager = get_identity_manager()
    with _ROOT_INIT_LOCK:
        root = _kernel_call(manager.account_root)
        if root is not None:
            return _settle_existing_root(manager, root, payload, request)
        # 首次注册 = 认领这台机器上的唯一根：可信启动下必须带桌面端证明。
        proof = _require_bootstrap_source(request)
        prior = _credential_record(payload.principal)
        try:
            _write_credential(
                payload.principal,
                payload.password,
                message="账号初始化失败，可重新提交完成初始化",
            )
            node, recovery_code = _kernel_call(
                manager.register_root_account,
                payload.principal,
                display_name=payload.display_name,
            )
        except HTTPException:
            # 内核在任何拒绝/失败前不落盘身份；把刚写的凭据回滚到提交前的
            # 样子，保证"凭据与身份要么都成功，要么都不落盘"。
            _restore_credential(payload.principal, prior)
            if proof:
                local_proof.release_token(proof)
            raise
        except Exception:
            if proof:
                local_proof.release_token(proof)
            raise
        if proof:
            local_proof.consume_token(proof)
    _clear_local_recovery_file()
    return {
        "account": _public_member(node),
        "recovery_code": recovery_code,
        "notice": "恢复码只显示这一次，请离线保存；它没有有效期，但一经使用即失效。",
    }


@router.post("/activate")
def activate(payload: ActivateRequest) -> Dict[str, Any]:
    """用一次性激活码设置密码并登录（一次性 + 限时 + 限次）。"""
    manager = get_identity_manager()
    _kernel_call(manager.verify_activation_code, payload.principal, payload.code)
    if not rotate_credential(payload.principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "账号初始化失败")
    _kernel_call(manager.consume_activation_code, payload.principal)
    token = issue_token_for(payload.principal, client="web")
    return {
        "token": token["access_token"],
        "access_token": token["access_token"],
        "account": _public_member(manager.get_account(payload.principal)),
    }


@router.post("/recovery/reset")
def recovery_reset(payload: RecoveryResetRequest, request: Request) -> Dict[str, Any]:
    """用根恢复码重置密码；改密**成功之后**才消费恢复码。

    顺序契约（P1-6）：先改密、后作废。反序（先消费再改密）在改密失败时会把
    恢复码一起作废且不能重发，用户彻底锁死。这里与激活流程
    （``verify_activation_code`` → 改密 → ``consume_activation_code``）同序。

    来源契约：默认只接受本机回环请求，且必须携带桌面端通过 DACL 命名管道
    发放的一次性重置令牌（防止同机其他账户只凭回环就重置）。远程重置必须
    由管理员显式开启 ``LIUHAO_ALLOW_REMOTE_RECOVERY``（默认关闭；开启后
    任何能访问服务端口、且持有恢复码的来源都可重置根密码，风险自担）。
    """
    proof = _require_recovery_source(request)
    manager = get_identity_manager()
    try:
        principal = _kernel_call(manager.verify_recovery_code, payload.recovery_code)
        _write_credential(
            principal,
            payload.password,
            message="账号恢复失败，恢复码仍然有效，可重新提交",
        )
        _kernel_call(manager.consume_recovery_code, payload.recovery_code)
    except Exception:
        # 本次失败不消耗证明：用户改正密码/换正确恢复码后还能重试，
        # 而并发请求里只有一个能占用成功，不会出现两次重置都成功。
        if proof:
            local_proof.release_token(proof)
        raise
    # 改密成功后才作废一次性证明：成功一次即失效。
    if proof:
        local_proof.consume_token(proof)
    # 本机交接文件里的码已经被作废，删除它避免用户下次误用。
    _clear_local_recovery_file()
    token = issue_token_for(principal, client="web")
    return {
        "token": token["access_token"],
        "access_token": token["access_token"],
        "account": _public_member(manager.get_account(principal)),
    }


@router.post("/self-register")
def self_register(payload: SelfRegisterRequest) -> Dict[str, Any]:
    """自助注册：用户名 + 密码即可，无需邀请码、无需主账号批准。

    * 上级恒为本机根账号的第 1 层（不接受请求体里的父子/深度字段）；
    * 作用域恒为最小集合（不能建下级、不能授权、不能借 Key）；
    * 主账号关闭开关后返回结构化拒绝 ``ACCOUNT_SELF_REGISTER_DISABLED``。
    """
    if not account_settings.allow_self_registration():
        raise deny(
            403, _accounts.CODE_SELF_REGISTER_DISABLED, "当前已关闭自助注册"
        )
    manager = get_identity_manager()
    node = _kernel_call(
        manager.register_self_service_account,
        payload.principal,
        display_name=payload.display_name,
    )
    if not rotate_credential(payload.principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "账号初始化失败")
    token = issue_token_for(payload.principal, client="web")
    return {
        "account": _public_member(node),
        "token": token["access_token"],
        "access_token": token["access_token"],
        "notice": "账号已创建并自动登录；当前使用的是最小权限，如需更多能力可在账号设置中查看。",
    }


@router.get("/self-registration")
def get_self_registration(
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """查询"是否允许自助注册"（仅主账号）。"""
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return {"allow": account_settings.allow_self_registration()}


@router.patch("/self-registration")
def set_self_registration(
    payload: SelfRegistrationSettingsRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """开关自助注册（仅主账号；持久化，默认开启）。"""
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    try:
        settings = account_settings.set_allow_self_registration(payload.allow)
    except StorageWriteError as exc:
        # 磁盘满/只读/被占用：策略没有落盘。必须如实告诉用户"没保存"，
        # 而不是 500 或假装成功。
        raise deny(503, "ACCOUNT_SETTINGS_WRITE_FAILED", str(exc)) from exc
    try:
        get_identity_manager().record_event(
            action="account.self_registration.toggle",
            actor=context.principal,
            target=context.principal,
            outcome="allowed",
            details={"allow": bool(settings.get("allow_self_registration"))},
        )
    except Exception:  # noqa: BLE001 - 审计失败不改变策略已生效的事实
        logger.exception("could not audit self-registration toggle")
    return {"allow": bool(settings.get("allow_self_registration"))}


@router.get("")
def list_accounts(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    """任何账号都只返回**直接成员**；整棵树只走 ``/tree``（仅根可用）。

    不返回 parent/depth/root：界面只需要"谁归我直接管"，层级结构不外泄。
    ``is_direct`` 恒为 ``true``，供前端区分"这是可直接管理的成员列表"。
    """
    if context.standalone:
        return {"members": [], "count": 0}
    manager = get_identity_manager()
    nodes = manager.account_nodes()
    members: List[Dict[str, Any]] = []
    for principal in sorted(nodes):
        node = nodes[principal]
        if node.parent_id != context.principal:
            continue
        entry = _public_member(node)
        entry["is_direct"] = True
        members.append(entry)
    return {"members": members, "count": len(members)}


@router.get("/tree")
def account_tree(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    """成员树视图：仅根账号可用（成员的视角永远是"只有自己这一层"）。"""
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    manager = get_identity_manager()
    nodes = manager.account_nodes()
    return {"tree": _accounts.tree_view(nodes, context.principal)}


@router.post("/backup/export")
def backup_export(
    request: Request,
    payload: BackupExportRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """导出加密凭据包；``scope=all`` 仅根账号可用，且默认只接受本机来源。

    凭据包能解开身份与全部成员数据，把入口暴露给网络等于把整棵树交出去；
    远程导出必须显式设置 ``LIUHAO_ALLOW_REMOTE_BACKUP_EXPORT=1``。
    """
    from .account_backup import export_bundle, remote_export_enabled

    local = request_is_local(request)
    if not local and not remote_export_enabled():
        _kernel_call(
            get_identity_manager().record_event,
            action="account.backup.export",
            actor=context.principal,
            target=context.principal,
            outcome="denied",
            details={
                "scope": payload.scope,
                "error_code": CODE_BACKUP_EXPORT_LOCAL_ONLY,
                "remote_source": True,
            },
        )
        raise deny(
            403,
            CODE_BACKUP_EXPORT_LOCAL_ONLY,
            "备份导出只允许在本机操作：请在这台机器的控制台重试。",
        )
    if not local:
        logger.warning(
            "远程备份导出已被显式开启：来源=%s actor=%s scope=%s",
            request.client.host if request.client else "?",
            context.principal,
            payload.scope,
        )
    return _kernel_call(
        export_bundle,
        actor=context.principal,
        scope=payload.scope,
        passphrase=payload.passphrase,
        subtree=context.subtree,
        is_root=context.is_root,
        remote_source=not local,
    )


@router.post("/backup/import")
def backup_import(payload: BackupImportRequest, request: Request) -> Dict[str, Any]:
    """在本机没有账号树时认领凭据包（口令校验 + 防重放 + 限次 + 审计）。

    导入等同于"建立账号树"，因此与首次注册同一来源契约：未初始化状态只接受
    本机来源，远程请求给中性拒绝。
    """
    from .account_backup import import_bundle

    root = _kernel_call(get_identity_manager().account_root)
    if root is None or _credential_record(root.principal) is None:
        _require_local_for_initialization(request)
    return _kernel_call(import_bundle, bundle=payload.bundle, passphrase=payload.passphrase)


@router.post("/recovery/rotate")
def recovery_rotate(
    context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """生成/重发恢复码（仅根账号；旧码立即失效）。

    这是"老根账号（0.1.0 时代创建、``recovery_hash`` 为空串）"的正规补发
    入口：只要还能登录，就能在这里拿到一枚**立刻可用**的新码。响应里没有
    ``expires_at`` —— 恢复码没有 TTL，返回一个生成时刻会被界面读成"有效期至"，
    让用户以为它会过期。
    """
    manager = get_identity_manager()
    code = _kernel_call(manager.rotate_recovery_code, context.principal)
    _clear_local_recovery_file()
    return {
        "recovery_code": code,
        "notice": "恢复码只显示这一次，请离线保存；它没有有效期，但一经使用即失效。",
    }


@router.post("/recovery/local")
def recovery_local_handoff(request: Request) -> Dict[str, Any]:
    """忘记密码且手上没有恢复码时的**本机**自助出口。

    交接文件始终以“非明文”形态落盘：Windows 用 DPAPI（CurrentUser）加密，
    其他平台或 DPAPI 失败时降级为 ACL 收紧 + 短 TTL；任何情况下都不长期留
    明文。本请求把码解密**一次**、随响应交给本机用户，随后立即删除文件并
    清零解密暂存（一次性取用即焚）。

    只在两种情况下开放：①本机已有加密交接文件（旧明文迁移或上次未取走）；
    ②根账号确实没有恢复码。后者之外不重发，避免任何人作废用户手上有效的
    恢复码（DoS）。重发有效码的正规入口是登录后的 ``/recovery/rotate``。
    """
    _require_loopback(request, message="恢复码只在本机交接：请在这台机器上重试。")
    # 回环不等于“本人”：同机其他 Windows 账户也能连 127.0.0.1。必须再验
    # 桌面端通过 DACL 命名管道发放的一次性令牌（与发起进程绑定）。
    _require_local_proof(request)
    manager = get_identity_manager()
    limit_key = "recovery:local"
    try:
        _accounts.limiter.check(
            limit_key,
            limit=_accounts.MAX_RECOVERY_ATTEMPTS,
            window=_accounts.RECOVERY_ATTEMPT_WINDOW_SECONDS,
        )
    except _accounts.AccountError as exc:
        raise from_account_error(
            _accounts.AccountError(
                429, _accounts.CODE_RECOVERY_LOCKED, "尝试次数过多，请稍后再试。"
            )
        ) from exc

    # 旧版明文文件通常在启动时已迁移；这里兜底重试一次（幂等，失败不阻断）。
    try:
        recovery_handoff.migrate_legacy_plaintext(manager)
    except Exception:  # noqa: BLE001
        logger.warning("旧明文恢复码迁移重试失败", exc_info=True)

    root = _kernel_call(manager.account_root)
    taken = recovery_handoff.take_handoff()
    if taken and root is not None and recovery_handoff.code_matches_root(taken, root):
        _accounts.limiter.register(limit_key)
        return {
            "written": False,
            "recovered": True,
            "file": LOCAL_RECOVERY_FILE_NAME,
            "recovery_code": taken,
            "notice": (
                "已从本机加密交接文件取出恢复码，原文件已删除。"
                "请把它填入“重置密码”；它只显示这一次。"
            ),
            "security_note": (
                "该响应只在本机回环 + 桌面端一次性证明下可用；"
                "码不写日志，文件取用后立即删除。"
            ),
        }

    if root is None:
        raise deny(403, _accounts.CODE_RECOVERY_UNAVAILABLE, "本机还没有可恢复的账号")
    if root.recovery_hash:
        raise deny(
            403,
            _accounts.CODE_RECOVERY_UNAVAILABLE,
            "已存在有效的恢复码；请使用已有的码，或登录后在“恢复码”页重新生成",
        )

    code = _kernel_call(manager.rotate_recovery_code, root.principal)
    try:
        handoff = recovery_handoff.write_handoff(root.principal, code)
        taken = recovery_handoff.take_handoff()
        if not taken:
            raise OSError("交接文件写入后无法取回")
    except Exception:  # noqa: BLE001 - 落盘失败必须回滚哈希，否则用户被永久锁死
        logger.exception("could not write local recovery handoff file")
        _kernel_call(
            manager.clear_recovery_code, root.principal, reason="local_handoff_failed"
        )
        raise deny(
            500,
            "ACCOUNT_RECOVERY_WRITE_FAILED",
            "恢复码未能写入本机数据目录，请重试",
        )
    _accounts.limiter.register(limit_key)
    return {
        "written": True,
        "recovered": False,
        "file": LOCAL_RECOVERY_FILE_NAME,
        "recovery_code": taken,
        "protection": handoff.get("protection"),
        "notice": (
            "已生成新的恢复码并只显示这一次；本机加密交接文件已随取用删除。"
            "请立即填入“重置密码”。"
        ),
        "security_note": (
            "码不以明文长期落盘：Windows 用 DPAPI 加密，其他平台用 ACL + 短 TTL；"
            "响应只在本机回环 + 桌面端一次性证明下可用。"
        ),
    }


# --------------------------------------------------------------------------- #
# 成员管理
# --------------------------------------------------------------------------- #

@router.post("")
def create_member(
    payload: CreateMemberRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    manager = get_identity_manager()
    if not context.has_scope(_accounts.SCOPE_MANAGE_CHILDREN):
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    node, activation_code = _kernel_call(
        manager.create_member_account,
        context.principal,
        payload.principal,
        permissions=payload.permissions,
        role=payload.role,
        display_name=payload.display_name,
        allow_key_sharing=payload.allow_key_sharing,
    )
    return {
        "account": _public_member(node),
        "activation_code": activation_code,
        "notice": "把一次性激活码交给该成员，由其在激活页设置密码。",
    }


@router.get("/{principal}")
def get_account(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    require_visible(context, principal)
    node = get_identity_manager().get_account(principal)
    if node is None:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return {"account": _public_member(node)}


@router.post("/{principal}/activation-code")
def reissue_activation(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    require_manage(context, principal)
    code, stamp = _kernel_call(
        get_identity_manager().issue_activation_code, context.principal, principal
    )
    return {"activation_code": code, "expires_at": stamp}


@router.patch("/{principal}/permissions")
def update_permissions(
    principal: str,
    payload: PermissionsRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    require_manage(context, principal)
    node = _kernel_call(
        get_identity_manager().grant_account_permissions,
        context.principal,
        principal,
        payload.grant,
        payload.revoke,
    )
    return {"account": _public_member(node)}


@router.patch("/{principal}/key-sharing")
def update_key_sharing(
    principal: str,
    payload: KeySharingRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """自己可以随时开启/撤回共享；管理者可代为调整（均写审计）。"""
    if principal != context.principal:
        require_manage(context, principal)
    node = _kernel_call(
        get_identity_manager().set_key_sharing, context.principal, principal, payload.allow
    )
    response: Dict[str, Any] = {"account": _public_member(node)}
    if principal != context.principal:
        # 共享授权变化会 +1 security_epoch：对方已签发的令牌在下一个请求失效。
        # 这是有意保留的安全行为（权限变更即时生效），但必须显式告知操作者。
        response["requires_reauth"] = True
        response["notice"] = "共享设置已更新；该成员当前的登录凭据已失效，需要重新登录。"
    return response


@router.patch("/{principal}/status")
def update_status(
    principal: str,
    payload: StatusRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    # 根账号是整棵树的锚点：先于"只能管直接下级"的判定给出明确结论。
    # 只有根查自己时才会走到这里；非根请求根账号仍被 require_visible 挡成 404。
    if payload.status == "deleted" and context.is_root and principal == context.principal:
        raise deny(
            409,
            _accounts.CODE_DELETE_ROOT_FORBIDDEN,
            "根账号不可删除",
        )
    require_manage(context, principal)
    result = _kernel_call(
        get_identity_manager().set_account_status,
        context.principal,
        principal,
        payload.status,
        payload.reason,
    )
    return result


@router.post("/{principal}/password")
def reset_password(
    principal: str,
    payload: PasswordResetRequest,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """管理者重置成员密码：写凭据 + 立即吊销旧令牌 + 哈希链审计。"""
    require_manage(context, principal)
    if not rotate_credential(principal, payload.password):
        raise deny(500, "ACCOUNT_CREDENTIAL_WRITE_FAILED", "密码重置失败")
    manager = get_identity_manager()
    _kernel_call(manager.bump_account_epoch, principal, "password-reset")
    _kernel_call(
        manager.record_event,
        action="account.password.reset",
        actor=context.principal,
        target=principal,
        outcome="allowed",
        details={"credential": "rotated"},
    )
    return {"ok": True}


@router.get("/{principal}/usage")
def account_usage(
    principal: str, context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    """用量按 Key 归属记账、按实际调用者展示；成员只见自己。"""
    require_visible(context, principal)
    from .account_usage import usage_summary

    manager = get_identity_manager()
    if context.is_root:
        # 根账号：可见目标账号的整棵子树（查询自己时就是全树）。
        owners = tuple(manager.subtree_principals(principal)) or (principal,)
        return usage_summary(
            principal, include_callers=True, owners=owners
        )
    # 成员：只看自己 Key 的额度消耗；响应里没有 by_actor（不暴露调用者）。
    return usage_summary(
        principal, include_callers=False, owners=(context.principal,)
    )


@router.get("/{principal}/usage/events")
def account_usage_events(
    principal: str,
    correlation_id: Optional[str] = None,
    actor: Optional[str] = None,
    key_owner: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    context: AccountContext = Depends(require_account_principal),
) -> Dict[str, Any]:
    """逐笔用量明细：按 ``correlation_id`` / 时间 / actor / key_owner 过滤并分页。

    权限与 ``/{principal}/usage`` 完全一致：根可看目标子树（可按 actor 与
    key_owner 过滤），成员只能看到 ``actor=本人`` 的行。
    """
    require_visible(context, principal)
    from .account_usage import usage_events

    manager = get_identity_manager()
    if context.is_root:
        owners = tuple(manager.subtree_principals(principal)) or (principal,)
        try:
            return usage_events(
                principal,
                include_callers=True,
                owners=owners,
                actor=actor,
                key_owner=key_owner,
                correlation_id=correlation_id,
                since=since,
                until=until,
                limit=limit,
                offset=offset,
            )
        except ValueError:
            raise deny(
                400,
                "USAGE_TIME_INVALID",
                "时间格式无效，请使用 ISO 8601（例如 2026-11-08T00:00:00Z）",
            ) from None
    try:
        return usage_events(
            principal,
            include_callers=False,
            owners=(context.principal,),
            actor=actor,
            key_owner=key_owner,
            correlation_id=correlation_id,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    except ValueError:
        raise deny(
            400,
            "USAGE_TIME_INVALID",
            "时间格式无效，请使用 ISO 8601（例如 2026-11-08T00:00:00Z）",
        ) from None


# --------------------------------------------------------------------------- #
# 迁移（运维）
# --------------------------------------------------------------------------- #

@router.post("/migrate")
def migrate(context: AccountContext = Depends(require_account_principal)) -> Dict[str, Any]:
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return _kernel_call(get_identity_manager().migrate_account_tree)


@router.post("/migrate/rollback")
def migrate_rollback(
    context: AccountContext = Depends(require_account_principal)
) -> Dict[str, Any]:
    if not context.is_root or context.standalone:
        raise deny(404, _accounts.CODE_SUBTREE_DENIED, _accounts.NEUTRAL_NOT_FOUND_MESSAGE)
    return _kernel_call(get_identity_manager().rollback_account_tree)
