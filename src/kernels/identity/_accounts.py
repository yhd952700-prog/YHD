"""账号树：主账号 / 成员账号的层级、作用域与判定。

Why this module exists
----------------------
身份内核此前只有"人类主体"一个维度：登记了就是 L0，没登记就不是人。产品需要
一个**单机单根**的账号树（主账号 + 成员），并且把"能不能操作某个账号"从令牌
里的静态 claim 变成**基于树位置的判定**：

* 主账号（根）管整棵树；成员只能管自己的直接下级；
* 越级/横向操作一律拒绝，且拒绝文案中性（不暴露层级结构）；
* Key 只能向下借用，且必须目标账号显式授权（``allow_superior_key_use``）；
* 权限变更、停用、改 Key 归属都要让**已签发凭据在下一个请求即失效**（
  账号上的 ``security_epoch`` 与令牌里的 epoch 比对）。

本模块只做**纯逻辑**：节点模型、树算法、作用域白名单、一次性码。落盘与凭据
读写分别在 ``_persistence.py`` / ``gateway/auth.py``，审计走哈希链
``kernels.audit.log_event``。

设计取舍
--------
* 层级上限出厂 ``max_depth = 2``（即 3 层：根 -> 成员 -> 成员的下级），可用
  ``LIUHAO_ACCOUNT_MAX_DEPTH`` 调整（允许 0-7，调整需写审计）。
* 根账号**隐式持有**全部可授予作用域（否则一个"没有任何 permission"的老账号
  无法管理成员）。成员默认只拿一组非敏感作用域（看子树、建下级、读审计/用量、
  用自己的 Key）。
* ``account:key_borrow`` 是**结构性特权**，任何账号都不能把它授予别人；它只
  属于"拥有该账号祖先关系"的账号，规则见 :func:`can_borrow_key`。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from typing import Dict, Iterable, List, Optional, Set, Tuple

from src._time import utc_now

# --------------------------------------------------------------------------- #
# 作用域白名单（账号层面的 permission，与既有 RBAC/ABAC 词表共存）
# --------------------------------------------------------------------------- #

SCOPE_READ = "account:read"  # 查看自己子树内的账号
SCOPE_MANAGE_CHILDREN = "account:manage_children"  # 创建/管理直接下级
SCOPE_GRANT = "account:grant"  # 调整直接下级的权限
SCOPE_AUDIT_READ = "account:audit_read"  # 读自己子树内的审计
SCOPE_USAGE_READ = "account:usage_read"  # 读自己子树内的用量
SCOPE_MODEL_MANAGE = "model:manage"  # 配置模型 / 绑定 Key
SCOPE_MODEL_USE = "model:use"  # 使用模型
#: 结构性特权，不可授予。主账号（目标账号的祖先）天然具备。
SCOPE_KEY_BORROW = "account:key_borrow"

#: 成员账号创建时的默认作用域：够用，但不含"授权"与"Key 借用"。
#: ``model:manage`` 只允许其绑定/维护**自己**的 Key（归属由网关二次校验），
#: 否则成员无法配置自己的模型，也就无法满足"只能使用自己的 Key"。
DEFAULT_MEMBER_SCOPES: Tuple[str, ...] = (
    SCOPE_READ,
    SCOPE_MANAGE_CHILDREN,
    SCOPE_AUDIT_READ,
    SCOPE_USAGE_READ,
    SCOPE_MODEL_USE,
    SCOPE_MODEL_MANAGE,
)

#: 自助注册者的默认作用域：能用自己的 Key、看自己的审计/用量，但**不能建下级、
#: 不能授权、不能借 Key**。这是"最小权限"落点，主账号可事后按需授予。
SELF_SERVICE_SCOPES: Tuple[str, ...] = (
    SCOPE_READ,
    SCOPE_AUDIT_READ,
    SCOPE_USAGE_READ,
    SCOPE_MODEL_USE,
    SCOPE_MODEL_MANAGE,
)

#: 可被授予的作用域（``account:key_borrow`` 故意不在其中）。
#:
#: ``SCOPE_MODEL_MANAGE`` 已在 ``DEFAULT_MEMBER_SCOPES`` 里；再次追加会让
#: ``/v1/accounts/roles`` 返回重复项，前端权限矩阵因此出现两列同名"配置模型"。
#: 统一用 ``dict.fromkeys`` 保序去重，避免任何一个预设产生重复作用域。
GRANTABLE_SCOPES: Tuple[str, ...] = tuple(
    dict.fromkeys(DEFAULT_MEMBER_SCOPES + (SCOPE_GRANT, SCOPE_MODEL_MANAGE))
)

#: 角色预设（2-3 个即可覆盖常见装机场景）。
ROLE_PRESETS: Dict[str, Tuple[str, ...]] = {
    "viewer": (SCOPE_READ, SCOPE_AUDIT_READ, SCOPE_USAGE_READ, SCOPE_MODEL_USE),
    "operator": DEFAULT_MEMBER_SCOPES,
    "manager": tuple(
        dict.fromkeys(DEFAULT_MEMBER_SCOPES + (SCOPE_GRANT, SCOPE_MODEL_MANAGE))
    ),
}

#: 根账号隐式持有的全部可授予作用域。
ROOT_IMPLICIT_SCOPES: Tuple[str, ...] = GRANTABLE_SCOPES

ACCOUNT_STATUS_ACTIVE = "active"
ACCOUNT_STATUS_SUSPENDED = "suspended"
ACCOUNT_STATUS_DELETED = "deleted"

ACCOUNT_MAX_DEPTH_ENV = "LIUHAO_ACCOUNT_MAX_DEPTH"
#: 出厂值 2 = 3 层（根 0 层、成员 1 层、成员的下级 2 层）。可配置 0-7。
DEFAULT_MAX_DEPTH = 2
MAX_CONFIGURABLE_DEPTH = 7

#: 激活码 / 恢复码的有效期与尝试次数。
ACTIVATION_TTL_ENV = "LIUHAO_ACCOUNT_ACTIVATION_TTL_SECONDS"
DEFAULT_ACTIVATION_TTL_SECONDS = 24 * 3600
MAX_ACTIVATION_ATTEMPTS = 5
MAX_RECOVERY_ATTEMPTS = 5
RECOVERY_ATTEMPT_WINDOW_SECONDS = 15 * 60
MAX_IMPORT_ATTEMPTS = 5
IMPORT_ATTEMPT_WINDOW_SECONDS = 15 * 60


# --------------------------------------------------------------------------- #
# 错误：状态码 + 稳定错误码 + 中性文案
# --------------------------------------------------------------------------- #

#: 中性文案不得包含"上级/子账号/层级/depth/root"等结构词。集中在此，避免
#: 各处散落的字符串把结构泄露出去。
NEUTRAL_FORBIDDEN_MESSAGE = "无权执行该操作"
NEUTRAL_NOT_FOUND_MESSAGE = "资源不存在或无权访问"

#: 定稿错误码（与 docs/account-hierarchy.md §4.3 一一对应）。
CODE_SUBTREE_DENIED = "ACCOUNT_SUBTREE_DENIED"
CODE_UPWARD_DENIED = "ACCOUNT_UPWARD_DENIED"
CODE_LEAPFROG_DENIED = "ACCOUNT_LEAPFROG_DENIED"
CODE_PRIVILEGE_ESCALATION = "ACCOUNT_PRIVILEGE_ESCALATION"
CODE_CROSS_SUBTREE_DENIED = "ACCOUNT_CROSS_SUBTREE_DENIED"
CODE_KEY_UPWARD_DENIED = "ACCOUNT_KEY_UPWARD_DENIED"
CODE_KEY_NOT_OWNED = "ACCOUNT_KEY_NOT_OWNED"
CODE_KEY_CONSENT_REQUIRED = "ACCOUNT_KEY_CONSENT_REQUIRED"
CODE_KEY_PLAINTEXT_FORBIDDEN = "ACCOUNT_KEY_PLAINTEXT_FORBIDDEN"
CODE_MEMORY_FOREIGN_DENIED = "ACCOUNT_MEMORY_FOREIGN_DENIED"
CODE_MEMORY_WRITE_FOREIGN = "ACCOUNT_MEMORY_WRITE_FOREIGN"
CODE_DEPTH_EXCEEDED = "ACCOUNT_DEPTH_EXCEEDED"
CODE_DELETE_HAS_CHILDREN = "ACCOUNT_DELETE_HAS_CHILDREN"
CODE_DELETE_ROOT_FORBIDDEN = "ACCOUNT_DELETE_ROOT_FORBIDDEN"
CODE_ROOT_REGISTRATION_CLOSED = "ACCOUNT_ROOT_REGISTRATION_CLOSED"
CODE_SELF_REGISTER_DISABLED = "ACCOUNT_SELF_REGISTER_DISABLED"
CODE_SELF_REGISTER_UNAVAILABLE = "ACCOUNT_SELF_REGISTER_UNAVAILABLE"
CODE_ACTIVATION_REQUIRED = "ACCOUNT_ACTIVATION_REQUIRED"
CODE_ACTIVATION_INVALID = "ACCOUNT_ACTIVATION_CODE_INVALID"
CODE_ACTIVATION_LOCKED = "ACCOUNT_ACTIVATION_LOCKED"
CODE_PASSWORD_CHANGE_REQUIRED = "ACCOUNT_PASSWORD_CHANGE_REQUIRED"
CODE_ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
CODE_VIEW_AS_WRITE_FORBIDDEN = "ACCOUNT_VIEW_AS_WRITE_FORBIDDEN"
CODE_ACTOR_FROM_BODY = "ACTOR_FROM_BODY_FORBIDDEN"
CODE_RECOVERY_CODE_INVALID = "ACCOUNT_RECOVERY_CODE_INVALID"
CODE_RECOVERY_LOCKED = "ACCOUNT_RECOVERY_LOCKED"
CODE_RECOVERY_UNAVAILABLE = "ACCOUNT_RECOVERY_UNAVAILABLE"
CODE_MIGRATION_PACKAGE_INVALID = "ACCOUNT_MIGRATION_PACKAGE_INVALID"
CODE_MIGRATION_PASSPHRASE_REQUIRED = "ACCOUNT_MIGRATION_PASSPHRASE_REQUIRED"
CODE_MIGRATION_CONFLICT = "ACCOUNT_MIGRATION_CONFLICT"
CODE_MIGRATION_REPLAY_DENIED = "ACCOUNT_MIGRATION_REPLAY_DENIED"
CODE_MULTI_ROOT_DETECTED = "ACCOUNT_MULTI_ROOT_DETECTED"
CODE_AMBIGUOUS = "ACCOUNT_AMBIGUOUS"
CODE_CORE_CAPABILITY_DENIED = "CORE_CAPABILITY_DENIED"


class AccountError(Exception):
    """带稳定错误码的账号树拒绝。

    ``status_code`` 是 HTTP 语义（403/401/409/429/400/404），``code`` 供客户端分支，
    ``message`` 必须中性。调用方（gateway）原样输出，不得再拼接层级信息。
    """

    def __init__(self, status_code: int, code: str, message: str, **extra):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra


def subtree_denied(code: str = CODE_SUBTREE_DENIED) -> AccountError:
    """不可见即 404（防枚举）：不泄露目标是否存在。"""
    return AccountError(404, code, NEUTRAL_NOT_FOUND_MESSAGE)


def neutral_denied(**extra) -> AccountError:
    """可见但无权：403 + 中性文案（真实原因只进审计）。"""
    return AccountError(403, CODE_LEAPFROG_DENIED, NEUTRAL_FORBIDDEN_MESSAGE, **extra)


def depth_exceeded() -> AccountError:
    return AccountError(409, CODE_DEPTH_EXCEEDED, "当前工作区暂不能再添加成员")


# --------------------------------------------------------------------------- #
# 节点模型
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AccountNode:
    """一个账号在树中的位置与授权属性（不可变；改写用 :func:`replace`）。"""

    principal: str
    parent_id: Optional[str]
    depth: int
    root_id: str
    permissions: frozenset = frozenset()
    allow_superior_key_use: bool = False
    key_auth_source: str = "not_granted"
    status: str = ACCOUNT_STATUS_ACTIVE
    security_epoch: int = 0
    activation_hash: str = ""
    activation_expires_at: Optional[str] = None
    activation_attempts: int = 0
    recovery_hash: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: str = "runtime"

    # ---- 便捷判定 -----------------------------------------------------
    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def is_active(self) -> bool:
        return self.status == ACCOUNT_STATUS_ACTIVE

    def held_scopes(self) -> frozenset:
        """实际持有的作用域：根账号隐式持有全部可授予作用域。"""
        if self.is_root:
            return frozenset(ROOT_IMPLICIT_SCOPES) | set(self.permissions)
        return frozenset(self.permissions)

    def has(self, scope: str) -> bool:
        return scope in self.held_scopes()

    def with_changes(self, **changes) -> "AccountNode":
        changes.setdefault("updated_at", utc_now().isoformat())
        return replace(self, **changes)

    def bump(self, reason: str = "") -> "AccountNode":
        return self.with_changes(
            security_epoch=int(self.security_epoch) + 1,
            updated_at=utc_now().isoformat(),
        )

    # ---- 序列化 -------------------------------------------------------

    def to_record(self) -> Dict[str, object]:
        return {
            "principal": self.principal,
            "parent_id": self.parent_id,
            "depth": int(self.depth),
            "root_id": self.root_id,
            "permissions": sorted(self.permissions),
            "allow_superior_key_use": bool(self.allow_superior_key_use),
            "key_auth_source": self.key_auth_source,
            "status": self.status,
            "security_epoch": int(self.security_epoch),
            "activation_hash": self.activation_hash,
            "activation_expires_at": self.activation_expires_at,
            "activation_attempts": int(self.activation_attempts),
            "recovery_hash": self.recovery_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source": self.source,
        }

    @classmethod
    def from_record(cls, record: Dict[str, object]) -> "AccountNode":
        principal = str(record.get("principal") or "").strip()
        if not principal:
            raise ValueError("account record has no principal")
        try:
            depth = int(record.get("depth") or 0)
        except (TypeError, ValueError):
            depth = 0
        parent = record.get("parent_id")
        parent_id = str(parent).strip() if parent else None
        root_id = str(record.get("root_id") or principal).strip() or principal
        try:
            permissions = frozenset(
                str(p) for p in (record.get("permissions") or []) if str(p).strip()
            )
        except TypeError:
            permissions = frozenset()
        try:
            epoch = int(record.get("security_epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0
        try:
            attempts = int(record.get("activation_attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        return cls(
            principal=principal,
            parent_id=parent_id,
            depth=depth,
            root_id=root_id,
            permissions=permissions,
            allow_superior_key_use=bool(record.get("allow_superior_key_use", False)),
            key_auth_source=str(record.get("key_auth_source") or "not_granted"),
            status=str(record.get("status") or ACCOUNT_STATUS_ACTIVE),
            security_epoch=epoch,
            activation_hash=str(record.get("activation_hash") or ""),
            activation_expires_at=(
                str(record.get("activation_expires_at"))
                if record.get("activation_expires_at")
                else None
            ),
            activation_attempts=attempts,
            recovery_hash=str(record.get("recovery_hash") or ""),
            created_at=str(record.get("created_at") or ""),
            updated_at=str(record.get("updated_at") or ""),
            source=str(record.get("source") or "runtime"),
        )


# --------------------------------------------------------------------------- #
# 枚举与树算法
# --------------------------------------------------------------------------- #


def subtree_of(nodes: Dict[str, AccountNode], principal: str) -> List[str]:
    """返回 ``principal`` 的整棵子树（含自己）；未知主体返回空。"""
    node = nodes.get(principal)
    if node is None:
        return []
    root = node.root_id
    out: List[str] = []
    for candidate in nodes.values():
        if candidate.root_id != root:
            continue
        # 用 parent 链证明后代关系：不信任 depth（depth 只是索引，不决定真伪）。
        cursor: Optional[AccountNode] = candidate
        guard = 0
        while cursor is not None and guard <= len(nodes) + 1:
            if cursor.principal == principal:
                out.append(candidate.principal)
                break
            cursor = nodes.get(cursor.parent_id) if cursor.parent_id else None
            guard += 1
    return out


def account_path(nodes: Dict[str, AccountNode], principal: str) -> str:
    """返回 ``principal`` 的物化路径（``/root/sub/grand``）；未知主体返回空串。

    路径只由 parent 链推导，**不信任存库的 depth**（depth 只是索引）。调用方
    只能把"本人的 path"返回给本人；整树结构只允许根账号视图返回。
    """
    node = nodes.get(principal)
    if node is None:
        return ""
    parts: List[str] = []
    cursor: Optional[AccountNode] = node
    guard = 0
    while cursor is not None and guard <= len(nodes) + 1:
        parts.append(cursor.principal)
        cursor = nodes.get(cursor.parent_id) if cursor.parent_id else None
        guard += 1
    return "/" + "/".join(reversed(parts))


def is_within_subtree(nodes: Dict[str, AccountNode], viewer: str, target: str) -> bool:
    return target in subtree_of(nodes, viewer)


def is_direct_child(nodes: Dict[str, AccountNode], actor: str, target: str) -> bool:
    node = nodes.get(target)
    return bool(node and node.parent_id == actor)


def can_manage(nodes: Dict[str, AccountNode], actor: str, target: str) -> bool:
    """谁能对 ``target`` 做写操作。

    * 根账号：整棵树（产品的"主账号能控制所有子账号的权限"）；
    * 普通账号：**只**能对自己的直接下级（"只能控制自己的子账号"）。
    """
    actor_node = nodes.get(actor)
    target_node = nodes.get(target)
    if actor_node is None or target_node is None:
        return False
    if actor_node.principal == target_node.principal:
        return False  # 改自己的权限属于另一个流程，不允许自助提权
    if actor_node.root_id != target_node.root_id:
        return False
    if actor_node.is_root:
        return True
    return target_node.parent_id == actor_node.principal


def can_read(nodes: Dict[str, AccountNode], viewer: str, target: str) -> bool:
    """读类操作（列表/用量/审计）：自己的整棵子树。"""
    return is_within_subtree(nodes, viewer, target)


def can_borrow_key(nodes: Dict[str, AccountNode], actor: str, owner: str) -> bool:
    """谁能使用 ``owner`` 的 Key。

    * 主账号（根）：子树内任意账号，但还需目标账号的知情授权（见
      :func:`key_borrow_authorized`）；
    * 普通账号：**只能**自己的 Key。
    """
    actor_node = nodes.get(actor)
    if actor_node is None:
        return False
    if actor == owner:
        return True
    if not actor_node.is_root:
        return False
    return is_within_subtree(nodes, actor, owner)


def key_borrow_authorized(nodes: Dict[str, AccountNode], owner: str) -> bool:
    node = nodes.get(owner)
    return bool(node and node.is_active and node.allow_superior_key_use)


def grant_decision(
    nodes: Dict[str, AccountNode], actor: str, target: str, grant: Iterable[str]
) -> Tuple[str, Optional[str]]:
    """权限授予判定。返回 ``(决策, 拒绝原因码)``。

    防提权：授予者不能授出自己没有的 scope；``account:key_borrow`` 永不可授。
    """
    actor_node = nodes.get(actor)
    if actor_node is None:
        return "deny", "ACCOUNT_SCOPE_DENIED"
    for scope in grant:
        if scope == SCOPE_KEY_BORROW:
            return "deny", CODE_PRIVILEGE_ESCALATION
        if scope not in GRANTABLE_SCOPES:
            return "deny", CODE_PRIVILEGE_ESCALATION
        if not actor_node.has(scope):
            return "deny", CODE_PRIVILEGE_ESCALATION
    return "allow", None


def revoke_decision(
    nodes: Dict[str, AccountNode], actor: str, target: str, scopes: Iterable[str]
) -> Tuple[str, Optional[str]]:
    actor_node = nodes.get(actor)
    if actor_node is None:
        return "deny", "ACCOUNT_SCOPE_DENIED"
    for scope in scopes:
        # 只能收回自己有权授予的作用域，避免下级"删除"上级的治理能力。
        if scope not in GRANTABLE_SCOPES or not actor_node.has(scope):
            return "deny", CODE_PRIVILEGE_ESCALATION
    return "allow", None


def max_depth() -> int:
    """出厂 2（3 层）；配置允许 0-7，越界按边界截断而不是报错。"""
    raw = (os.environ.get(ACCOUNT_MAX_DEPTH_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_DEPTH
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_DEPTH
    return max(0, min(MAX_CONFIGURABLE_DEPTH, value))


def activation_ttl_seconds() -> int:
    raw = (os.environ.get(ACTIVATION_TTL_ENV) or "").strip()
    if not raw:
        return DEFAULT_ACTIVATION_TTL_SECONDS
    try:
        return max(60, min(30 * 24 * 3600, int(float(raw))))
    except (TypeError, ValueError):
        return DEFAULT_ACTIVATION_TTL_SECONDS


def resolve_role(role: Optional[str], permissions: Optional[Iterable[str]]) -> frozenset:
    """把角色预设 + 显式权限合并成一个作用域集合。"""
    merged: Set[str] = set()
    if role:
        preset = ROLE_PRESETS.get(str(role).strip().lower())
        if preset is None:
            raise AccountError(400, "ACCOUNT_ROLE_UNKNOWN", "未知的角色预设")
        merged.update(preset)
    if permissions is not None:
        merged.update(str(p).strip() for p in permissions if str(p).strip())
    return frozenset(merged)


# --------------------------------------------------------------------------- #
# 一次性码：激活码 / 恢复码
# --------------------------------------------------------------------------- #

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉了易混的 0/1/I/O


def generate_code(prefix: str = "LH") -> str:
    """生成高熵一次性码（约 100 bit）。"""
    groups = ["".join(secrets.choice(_CODE_ALPHABET) for _ in range(4)) for _ in range(6)]
    return f"{prefix}-" + "-".join(groups)


def hash_code(code: str, key: str = "") -> str:
    """对一次性码做带密钥哈希。

    ``key`` 为空时退化为 sha256（仍然只存哈希，明文永不落盘）。高熵码不需要
    KDF；加一层 HMAC 只是让"有文件写权限的人"无法离线枚举。
    """
    normalized = str(code or "").strip().upper().encode("utf-8")
    if key:
        digest = hmac.new(key.encode("utf-8"), normalized, hashlib.sha256).hexdigest()
    else:
        digest = hashlib.sha256(normalized).hexdigest()
    return f"sha256:{digest}"


def code_matches(code: str, stored: str, key: str = "") -> bool:
    if not code or not stored:
        return False
    return hmac.compare_digest(hash_code(code, key), str(stored))


def expires_at(seconds: Optional[int] = None) -> str:
    ttl = activation_ttl_seconds() if seconds is None else int(seconds)
    return (utc_now() + timedelta(seconds=ttl)).isoformat()


def is_expired(stamp: Optional[str]) -> bool:
    if not stamp:
        return True
    # 这里的 return True 是**fail-closed**，不是「谎报成功」：时间戳解析不出来
    # 就无法证明它还新鲜，只能按已过期处理（要求重新激活），调用方不会因此拿到
    # 一个本不该有效的码。Semgrep 的 liuhao-swallow-exception-return-success 只能
    # 看到「except 分支 return True」这个形状，故显式结辩。
    try:  # nosemgrep: liuhao-swallow-exception-return-success
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    # ``utc_now`` 是 naive UTC（代码库的持久化约定），因此把 aware 值折回 naive，
    # 避免 Python 3 里 aware/naive 比较直接抛 TypeError。
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed <= utc_now()


# --------------------------------------------------------------------------- #
# 尝试次数限制（激活 / 恢复 / 导入）
# --------------------------------------------------------------------------- #


class AttemptLimiter:
    """进程内滑动窗口限次器。

    只防"在进程生命周期内的暴力尝试"，不承诺跨重启。跨重启的防重放由
    凭据包 id 的持久化回执负责（见 ``_persistence`` 的 import receipts）。
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._events: Dict[str, List[float]] = {}

    def check(self, key: str, *, limit: int, window: int) -> None:
        now = time.monotonic()
        with self._lock:
            events = [t for t in self._events.get(key, []) if now - t < window]
            self._events[key] = events
            if len(events) >= limit:
                raise AccountError(
                    429, "ACCOUNT_RATE_LIMITED", "尝试次数过多，请稍后重试"
                )

    def register(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._events.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


#: 进程内共享的限次器（激活、恢复、导入各自使用不同的 key 前缀）。
limiter = AttemptLimiter()


# --------------------------------------------------------------------------- #
# 展示与中性化
# --------------------------------------------------------------------------- #


def public_member(node: AccountNode, *, show_key_authorization: bool) -> Dict[str, object]:
    """成员列表条目：**不含** parent/depth/root 等结构字段。

    ``show_key_authorization`` 只有根账号视图为 True；普通成员看不到
    "别人的 Key 可能被借用"这一事实（产品要求：不让成员感知层级）。
    """
    payload: Dict[str, object] = {
        "principal": node.principal,
        "permissions": sorted(node.permissions),
        "status": node.status,
        "created_at": node.created_at,
    }
    if show_key_authorization:
        payload["allow_superior_key_use"] = bool(node.allow_superior_key_use)
        payload["key_auth_source"] = node.key_auth_source
    return payload


def tree_view(
    nodes: Dict[str, AccountNode], root_principal: str
) -> Dict[str, object]:
    """根账号视图的嵌套成员树（唯一会返回结构信息的地方）。"""

    def build(principal: str) -> Dict[str, object]:
        node = nodes[principal]
        children = sorted(
            (
                build(child.principal)
                for child in nodes.values()
                if child.parent_id == principal
            ),
            key=lambda item: str(item["principal"]),
        )
        return {
            "principal": node.principal,
            "permissions": sorted(node.permissions),
            "status": node.status,
            "created_at": node.created_at,
            "key_auth_source": node.key_auth_source,
            "allow_superior_key_use": bool(node.allow_superior_key_use),
            "children": children,
        }

    node = nodes.get(root_principal)
    if node is None:
        return {"principal": root_principal, "permissions": [], "children": []}
    return build(root_principal)


def roots(nodes: Dict[str, AccountNode]) -> List[AccountNode]:
    return [n for n in nodes.values() if n.is_root]


def detect_cycles(nodes: Dict[str, AccountNode]) -> List[str]:
    """返回形成环的 principal 列表；正常树为空。

    载入不可信存储时必须先查环，否则 :func:`subtree_of` 的父链遍历会退化成
    "整张图"，把别人的子树算进自己的可见范围。
    """
    bad: List[str] = []
    for principal in nodes:
        seen: Set[str] = set()
        cursor: Optional[AccountNode] = nodes.get(principal)
        while cursor is not None:
            if cursor.principal in seen:
                bad.append(principal)
                break
            seen.add(cursor.principal)
            cursor = nodes.get(cursor.parent_id) if cursor.parent_id else None
    return bad


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
