"""
Role-Based Access Control (RBAC) for LiuHao AI OS

Provides:
- Role/permission hierarchy (roles inherit from other roles)
- Permission checking with scopes and conditions
- Resource-based access control
- Dynamic permission evaluation
- Audit logging of access decisions
"""

import hashlib
import json
import logging
import os
import secrets
import threading
import time
from typing import Optional, Dict, Any, Set, List
from dataclasses import dataclass, field
from pathlib import Path
from enum import Enum

from src.common.atomic_write import (
    atomic_write_text,
    exclusive_file_lock,
    read_text_with_retry,
)
from src.common.data_paths import resolve_store_path

#: 同进程内多个 RBAC manager 实例（以及多 worker 进程）的写入串行锁。
_PROCESS_WRITE_LOCK = threading.Lock()

logger = logging.getLogger(__name__)


def _platform_default_store_path(filename: str) -> Path:
    """所有覆盖变量都未配置时的默认落盘位置。

    与安装版启动器、身份库
    （``src.common.integrity_key.default_data_dir``）保持同一口径：
    Windows 为 ``%LOCALAPPDATA%\\LiuHao\\data``，其它平台为
    ``$XDG_DATA_HOME/liuhao``（缺省 ``~/.local/share/liuhao``）。

    这里刻意**不再**回落到 ``~/.liuhao``：那是 QA-006 记录的真实用户目录
    污染位置，未配置数据根时也不该把权限库写进用户主目录的隐藏目录。
    """
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return (base / "LiuHao" / "data" / filename).resolve()
    base = Path(
        os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    )
    return (base / "liuhao" / filename).resolve()


def _legacy_store_path() -> Path:
    """历史默认落点（0.1.0 及更早）：``~/.liuhao/rbac.json``。

    只作为**迁移来源**和**迁移失败时的回退读取位置**存在；默认写入位置不再
    回落到这里。单独抽成函数是为了让测试能把旧位置指到 tmp，而不碰真实用户目录。
    """
    return Path.home() / ".liuhao" / "rbac.json"


def resolve_rbac_store_path(store_path: Optional[str] = None) -> str:
    """解析权限库落盘路径，必要时把旧位置的库迁移过来。

    顺序：显式 ``store_path``（调用方注入，完全信任、不做迁移）→ 统一数据根
    （``LIUHAO_DATA_DIR``）或平台默认数据目录 → 旧 ``~/.liuhao/rbac.json``。

    迁移规则（与主密钥/队列存储一致，不能以空库启动）：
    * 新位置已有 → 用新位置，旧文件不删不改；
    * 新位置缺失、旧位置存在 → 复制到同目录临时文件、校验角色条数与内容摘要，
      ``os.replace`` 原子落位；旧文件保留，记 ``INFO``；
    * 迁移失败 → 记 ``ERROR`` 并继续使用旧位置（绝不以空库启动，也不留半截文件）；
    * 两边都没有 → 返回新位置，由首次 ``_save`` 建库。
    """
    if store_path:
        return store_path
    target = Path(
        resolve_store_path("rbac.json", _platform_default_store_path("rbac.json"))
    )
    return str(_migrate_if_needed(target, _legacy_store_path()))


def _migrate_if_needed(target: Path, legacy: Path) -> Path:
    if target == legacy:
        return target
    if target.exists():
        _log_both_locations(legacy, target)
        return target
    if not legacy.exists():
        # 两边都没有：正常初始化，由调用方首次保存时建库。
        return target

    # check-then-act 必须整段进锁：否则两个进程会各自看到"新库不存在"，
    # 都去迁移；旧实现共用同一个 ``.migrating`` 名，Windows 上后到者的
    # ``os.replace`` 会因文件被占用而失败并退回旧位置，最终一个进程写新库、
    # 一个进程写旧库。锁与被 ``_save`` 用的是同一把（``<store>.lock``），
    # 迁移与写事务也天然串行。
    try:
        with exclusive_file_lock(
            f"{target}.lock", description="RBAC 权限库迁移"
        ):
            # 锁内双重检查：等锁期间别的进程可能已经迁移完成。
            if target.exists():
                _log_both_locations(legacy, target)
                return target
            if not legacy.exists():
                return target
            _cleanup_orphan_staging(target)
            _copy_and_verify(legacy, target)
    except Exception as exc:  # noqa: BLE001 - 任何失败都要退回旧库，不能空库启动
        logger.error(
            "RBAC 权限库迁移失败（%s → %s）：%s: %s；继续使用旧位置 %s，"
            "不会以空库启动。请检查磁盘空间/权限后重试。",
            legacy,
            target,
            type(exc).__name__,
            exc,
            legacy,
        )
        return legacy
    logger.info("RBAC 权限库已迁移到 %s；旧文件 %s 已保留未删。", target, legacy)
    return target


def _log_both_locations(legacy: Path, target: Path) -> None:
    if legacy.exists():
        logger.info(
            "RBAC 权限库同时存在于新旧位置，使用新位置 %s（旧文件 %s 保留未删）",
            target,
            legacy,
        )


def _cleanup_orphan_staging(target: Path) -> None:
    """清理上次迁移被强杀留下的临时文件（只能在持有迁移锁时调用）。

    staging 名带 pid 与随机后缀，正常路径的 ``finally`` 只清自己的；持锁后
    这里清掉的都是崩溃遗留——此时不可能有别的进程正在写这些文件，删除安全。
    锁本身是操作系统级锁，进程死亡由内核释放，**不需要人工删 ``.lock``**。
    """
    for orphan in target.parent.glob(target.name + ".migrating.*"):
        try:
            orphan.unlink()
            logger.info("已清理上次中断留下的权限库迁移临时文件：%s", orphan)
        except OSError:
            logger.warning(
                "无法清理权限库迁移临时文件 %s，下次迁移时会再试：%s",
                orphan,
                "文件仍被占用或权限不足",
            )


def _fsync_directory(directory: Path) -> None:
    """让 rename 本身尽量落盘（POSIX 有效；Windows 打不开目录，忽略）。"""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _copy_and_verify(source: Path, target: Path) -> None:
    """复制旧库到新位置，校验角色条数与 SHA256 后再原子替换。

    调用方必须已持有 ``<target>.lock``。临时文件带 pid + 随机后缀（避免并发
    进程共用同一个名字），与目标同目录；成功后 fsync 内容再 ``os.replace``
    原子落位。任何失败都在 ``finally`` 清掉**自己的**临时文件。
    """
    payload = source.read_bytes()
    data = json.loads(payload.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"旧权限库不是 JSON 对象：{source}")
    expected_roles = len(data.get("roles", []))
    expected_digest = hashlib.sha256(payload).hexdigest()

    staging = target.with_name(
        f"{target.name}.migrating.{os.getpid()}.{secrets.token_hex(4)}"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(staging, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        copied = staging.read_bytes()
        if hashlib.sha256(copied).hexdigest() != expected_digest:
            raise OSError(f"迁移副本内容摘要不一致：{target}")
        staged = json.loads(copied.decode("utf-8"))
        if len(staged.get("roles", [])) != expected_roles:
            raise OSError(
                f"迁移副本角色条数不一致：期望 {expected_roles}，"
                f"实际 {len(staged.get('roles', []))}"
            )
        # 锁内再读一次旧文件：等锁期间旧版本进程（不认这把锁）可能改写过它。
        if source.read_bytes() != payload:
            raise OSError(f"旧权限库在迁移期间被修改，放弃本次迁移：{source}")
        os.replace(staging, target)
        _fsync_directory(target.parent)
    finally:
        staging.unlink(missing_ok=True)


class PermissionAction(Enum):
    """Standard permission actions"""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    CREATE = "create"
    DELETE = "delete"
    ADMIN = "admin"
    CONFIGURE = "configure"
    MONITOR = "monitor"
    INVOKE = "invoke"
    BROWSE = "browse"


class ResourceType(Enum):
    """Standard resource types"""
    MEMORY = "memory"
    AGENT = "agent"
    PROVIDER = "provider"
    WORKFLOW = "workflow"
    TOOL = "tool"
    CONFIG = "config"
    METRICS = "metrics"
    LOG = "log"
    API = "api"
    USER = "user"
    SESSION = "session"
    SYSTEM = "system"


@dataclass(eq=True)
class Permission:
    """
    Permission data model.

    Format: {resource_type}:{action}[:{conditions?}]
    Example: memory:read:agent-123
             provider:invoke:gpt-4
             system:admin:*
    """
    resource_type: ResourceType
    action: PermissionAction
    target: Optional[str] = None  # Specific target (agent ID, provider name, etc.)

    # Conditions for dynamic evaluation
    conditions: Dict[str, Any] = field(default_factory=dict)

    # Metadata
    description: Optional[str] = None
    granted_at: Optional[float] = None
    granted_by: Optional[str] = None

    def __hash__(self) -> int:
        """Hash based on immutable identity fields only."""
        return hash((self.resource_type, self.action, self.target))

    def matches(
        self,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if this permission matches a request"""
        # Resource type must match
        if self.resource_type != resource_type:
            return False

        # Action must match
        if self.action != action:
            return False

        # Target matching
        if self.target is not None and self.target != target:
            # Check for wildcard
            if self.target == "*":
                pass  # Any target matches
            else:
                return False

        # Condition matching
        if extra_conditions and self.conditions:
            for key, value in self.conditions.items():
                if key not in extra_conditions:
                    return False
                if extra_conditions[key] != value:
                    return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_type": self.resource_type.value,
            "action": self.action.value,
            "target": self.target,
            "conditions": self.conditions,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Permission":
        return cls(
            resource_type=ResourceType(data["resource_type"]),
            action=PermissionAction(data["action"]),
            target=data.get("target"),
            conditions=data.get("conditions", {}),
            description=data.get("description"),
        )


class RoleStatus(Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


@dataclass
class Role:
    """
    Role data model with inheritance.

    A role can inherit permissions from parent roles.
    Supports recursive inheritance with cycle detection.
    """
    id: str
    name: str
    description: Optional[str] = None
    status: RoleStatus = RoleStatus.ACTIVE
    parent_ids: List[str] = field(default_factory=list)  # Parent role IDs
    child_ids: List[str] = field(default_factory=list)   # Child role IDs
    inherited_scopes: Dict[str, Any] = field(default_factory=dict)

    # Cached permission set (computed from role + all parents)
    _cached_permissions: Optional[Set[Permission]] = None
    _cached_evaluated_at: float = 0

    def add_parent(self, parent_id: str) -> None:
        """Add parent role (with cycle detection)"""
        if parent_id == self.id:
            return  # Don't self-reference
        if parent_id in self.parent_ids:
            return  # Already parent

        # Check for cycles
        if self._would_create_cycle(parent_id):
            return

        self.parent_ids.append(parent_id)

        # Update child's parent reference
        # This would need to update the parent's child_ids list

    def _would_create_cycle(self, potential_parent_id: str) -> bool:
        """Check if adding a parent would create a cycle"""
        visited = set()
        to_visit = [potential_parent_id]

        while to_visit:
            current = to_visit.pop()
            if current == self.id:
                return True  # Would create cycle
            if current in visited:
                continue
            visited.add(current)

            # Look up this role's parents via the owning manager
            parent_role = self._get_role(current)
            if parent_role:
                to_visit.extend(parent_role.parent_ids)

        return False

    def has_permission(
        self,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if this role has a permission (including inherited)"""
        # Check cached permissions (refresh after 30 seconds)
        now = time.time()
        if (self._cached_permissions is not None
                and now - self._cached_evaluated_at < 30):
            perm_set = self._cached_permissions
        else:
            perm_set = self._compute_permissions()
            self._cached_permissions = perm_set
            self._cached_evaluated_at = now

        # Check if any permission matches
        for perm in perm_set:
            if perm.matches(
                resource_type, action, target, extra_conditions
            ):
                return True
        return False

    # Custom permissions assigned via RBACManager.assign_permission
    custom_permissions: Dict[str, Permission] = field(default_factory=dict)
    # Back-reference to owning RBACManager for parent lookups
    _rbac_manager: Optional["RBACManager"] = None

    def _get_role(self, role_id: str) -> Optional["Role"]:
        """Look up a role by ID from the owning manager, or global store."""
        if self._rbac_manager is not None:
            return self._rbac_manager.get_role(role_id)
        from .rbac_store import rbac_store
        return rbac_store.get_role(role_id)

    def _get_own_permissions(self) -> Set[Permission]:
        """Get permissions directly assigned to this role"""
        return set(self.custom_permissions.values())

    def _compute_permissions(self) -> Set[Permission]:
        """Compute all permissions from this role and parent roles"""
        perm_set: Set[Permission] = set()

        # Add own permissions
        perm_set.update(self._get_own_permissions())

        # Add parent permissions (recursive)
        for parent_id in self.parent_ids:
            parent_role = self._get_role(parent_id)
            if parent_role:
                perm_set.update(parent_role._compute_permissions())

        return perm_set

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "parent_ids": self.parent_ids,
            "child_ids": self.child_ids,
            "custom_permissions": {
                k: v.to_dict() for k, v in self.custom_permissions.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], rbac_store=None) -> "Role":
        role = cls(
            id=data["id"],
            name=data["name"],
            description=data.get("description"),
            status=RoleStatus(data.get("status", "active")),
            parent_ids=list(data.get("parent_ids", [])),
            child_ids=list(data.get("child_ids", [])),
        )
        # Load custom permissions
        for perm_key, perm_data in data.get("custom_permissions", {}).items():
            role.custom_permissions[perm_key] = Permission.from_dict(perm_data)
        # Rebuild child references if rbac_store provided
        if rbac_store:
            for parent_id in role.parent_ids:
                parent = rbac_store.get_role(parent_id)
                if parent:
                    parent.child_ids.append(role.id)
        return role


class RBACManager:
    """
    Role-Based Access Control Manager.

    Features:
    - Role hierarchy with inheritance
    - Permission evaluation with conditions
    - Resource-based access control
    - Dynamic permission granting/revocation
    - Audit trail of access decisions
    """

    def __init__(self, store_path: Optional[str] = None):
        """
        Initialize RBAC manager.

        Args:
            store_path: Path to JSON store for persistence
        """
        # 默认落盘位置走统一数据根：``LIUHAO_DATA_DIR`` 优先，未配置时用平台
        # 默认数据目录（安装版口径），不再写 ``~/.liuhao``。旧位置的库会在首次
        # 解析时迁移过来（失败则回退读旧位置，绝不以空库启动）。调用时求值，
        # 导入期不缓存路径；测试由 ``tests/conftest.py`` 的 autouse fixture 把
        # 数据根与旧位置指向 tmp，并复位进程级单例。
        self.store_path = resolve_rbac_store_path(store_path)
        self._roles: Dict[str, Role] = {}
        self._permissions_by_user: Dict[str, Set[Permission]] = {}  # user_id -> permissions
        self._audit_log: List[Dict[str, Any]] = []
        self._loaded = False

    def _load(self) -> None:
        """Load RBAC data from storage"""
        if self._loaded:
            return

        # 先置位再解析：``Role.from_dict`` 会为 ``parent_ids`` 回调
        # ``self.get_role()``，若此时 ``_loaded`` 仍是 False 就会递归重入
        # ``_load``。旧库里带父子关系的角色（甚至 self-role/环）会让重入一直
        # 到 RecursionError，表现为“加载失败”后角色残缺。置位在解析之前，
        # 重入直接返回当前已解析的部分；父角色引用随后按 id 懒查，不影响继承。
        self._loaded = True

        store = Path(self.store_path)
        if store.exists():
            try:
                data = json.loads(read_text_with_retry(self.store_path))
                for role_data in data.get("roles", []):
                    role = Role.from_dict(role_data, self)
                    role._rbac_manager = self
                    self._roles[role.id] = role
            except Exception as e:
                print(f"Warning: Failed to load RBAC data: {e}")

    def _save(self, deleted_ids: Set[str] = frozenset()) -> None:
        """Persist RBAC data to storage.

        读-改-写事务：进程锁 → OS 文件锁 → 锁内重读磁盘 → 按 role id 合并
        （同 id 以内存为准，磁盘上其它进程刚创建的角色保留）→ 原子替换。
        ``deleted_ids`` 显式排除已删除角色，避免被磁盘旧快照复活。
        """
        with _PROCESS_WRITE_LOCK:
            with exclusive_file_lock(
                f"{self.store_path}.lock", description="RBAC 存储"
            ):
                roles: Dict[str, Any] = {}
                store = Path(self.store_path)
                if store.exists():
                    disk = json.loads(read_text_with_retry(store))
                    if isinstance(disk, dict):
                        for item in disk.get("roles", []):
                            if isinstance(item, dict) and item.get("id"):
                                roles[item["id"]] = item
                for role_id in deleted_ids:
                    roles.pop(role_id, None)
                for role in self._roles.values():
                    roles[role.id] = role.to_dict()
                data = {
                    "version": 1,
                    "saved_at": time.time(),
                    "roles": list(roles.values()),
                }
                atomic_write_text(
                    str(self.store_path), json.dumps(data, indent=2)
                )

    # ==================== Role Management ====================

    def create_role(
        self,
        name: str,
        description: Optional[str] = None,
        status: RoleStatus = RoleStatus.ACTIVE,
        parent_ids: Optional[List[str]] = None,
    ) -> Role:
        """Create a new role"""
        self._load()

        role_id = f"role_{secrets.token_urlsafe(12)}"
        # Avoid collision with existing IDs
        while role_id in self._roles:
            role_id = f"role_{secrets.token_urlsafe(12)}"

        role = Role(
            id=role_id,
            name=name,
            description=description,
            status=status,
            parent_ids=parent_ids or [],
        )
        role._rbac_manager = self

        # Update parent-child references
        for parent_id in role.parent_ids:
            if parent_id in self._roles:
                self._roles[parent_id].child_ids.append(role_id)

        self._roles[role_id] = role
        self._save()
        return role

    def get_role(self, role_id: str) -> Optional[Role]:
        """Get role by ID"""
        self._load()
        return self._roles.get(role_id)

    def list_roles(
        self,
        status: Optional[RoleStatus] = None,
        recursive: bool = False,
    ) -> List[Role]:
        """List roles, optionally filtered by status"""
        self._load()

        roles = list(self._roles.values())
        if status:
            roles = [r for r in roles if r.status == status]

        if recursive:
            # Flatten with all descendants
            result = []
            for role in roles:
                result.append(role)
                result.extend(self._get_all_descendants(role))
            return result

        return sorted(roles, key=lambda r: r.name)

    def _get_all_descendants(self, role: Role) -> List[Role]:
        """Get all descendant roles"""
        result = []
        for child_id in role.child_ids:
            child = self.get_role(child_id)
            if child:
                result.append(child)
                result.extend(self._get_all_descendants(child))
        return result

    def delete_role(self, role_id: str) -> bool:
        """Delete a role"""
        self._load()

        if role_id not in self._roles:
            return False

        # Check if role has children (would orphan them)
        role = self._roles[role_id]
        if role.child_ids and len(role.child_ids) > 0:
            # Could reparent children, or prevent deletion
            # For now, prevent deletion if children exist
            return False

        # Remove from parents
        for r in self._roles.values():
            if role_id in r.child_ids:
                r.child_ids.remove(role_id)

        del self._roles[role_id]
        self._save(deleted_ids={role_id})
        return True

    # ==================== Permission Management ====================

    def assign_permission(
        self,
        role_id: str,
        permission: Permission,
    ) -> bool:
        """Assign a permission to a role"""
        self._load()

        if role_id not in self._roles:
            return False

        # Store permission in role's custom_permissions dict
        role = self._roles[role_id]
        target_str = permission.target if permission.target else ""
        perm_key = permission.resource_type.value + ":" + permission.action.value + ":" + target_str
        role.custom_permissions[perm_key] = permission

        # Invalidate cache so _compute_permissions picks up changes
        role._cached_permissions = None
        self._save()
        return True

    def revoke_permission(
        self,
        role_id: str,
        permission: Permission,
    ) -> bool:
        """Revoke a permission from a role"""
        self._load()

        if role_id not in self._roles:
            return False

        role = self._roles[role_id]
        if role.custom_permissions:
            target_str = permission.target if permission.target else ""
            perm_key = permission.resource_type.value + ":" + permission.action.value + ":" + target_str
            role.custom_permissions.pop(perm_key, None)

        # Invalidate cache
        role._cached_permissions = None
        self._save()
        return True

    def has_role_permission(
        self,
        role_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if a role has a specific permission"""
        self._load()

        role = self._roles.get(role_id)
        if not role:
            return False

        return role.has_permission(
            resource_type, action, target, extra_conditions
        )

    # ==================== User-Based Access ====================

    def assign_permissions_to_user(
        self,
        user_id: str,
        permissions: List[Permission],
    ) -> None:
        """Assign permissions directly to a user"""
        self._load()

        if user_id not in self._permissions_by_user:
            self._permissions_by_user[user_id] = set()

        for perm in permissions:
            self._permissions_by_user[user_id].add(perm)

        self._save()

    def revoke_permissions_from_user(
        self,
        user_id: str,
        permissions: Optional[List[Permission]] = None,
    ) -> int:
        """Revoke permissions from a user"""
        self._load()

        if user_id not in self._permissions_by_user:
            return 0

        if permissions:
            for perm in permissions:
                self._permissions_by_user[user_id].discard(perm)
        else:
            # Revoke all direct permissions
            len(self._permissions_by_user[user_id])
            self._permissions_by_user[user_id].clear()

        self._save()
        return len(self._permissions_by_user.get(user_id, set()))

    def user_has_permission(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check if a user has a permission (own + role-based)"""
        self._load()

        # Check direct user permissions
        user_perms = self._permissions_by_user.get(user_id, set())
        for perm in user_perms:
            if perm.matches(resource_type, action, target, extra_conditions):
                return True

        # Check role-based permissions
        # This would require knowing the user's roles
        # For now, check all roles
        for role in self._roles.values():
            if role.has_permission(resource_type, action, target, extra_conditions):
                return True

        return False

    # ==================== Access Decision ====================

    def check_access(
        self,
        user_id: str,
        resource_type: ResourceType,
        action: PermissionAction,
        target: Optional[str] = None,
        extra_conditions: Optional[Dict[str, Any]] = None,
        require_auth: bool = True,
    ) -> Dict[str, Any]:
        """
        Check access and return decision.

        Returns dict with:
        - allowed: bool
        - reason: str (if denied)
        - applicable_permissions: List[Permission]
        """
        self._load()

        # Check user permissions
        user_allowed = self.user_has_permission(
            user_id, resource_type, action, target, extra_conditions
        )

        # Check role permissions
        role_allowed = False
        applicable_perms: List[Permission] = []

        # Get user's roles (would need user-role mapping)
        # For now, check all roles
        for role in self._roles.values():
            if role.has_permission(resource_type, action, target, extra_conditions):
                role_allowed = True
                # Collect applicable permissions
                for perm in role._compute_permissions():
                    if perm.matches(resource_type, action, target, extra_conditions):
                        if perm not in applicable_perms:
                            applicable_perms.append(perm)

        # Determine decision
        allowed = user_allowed or role_allowed

        # Build reason
        reason = None
        if not allowed:
            reason = "Insufficient permissions"
            # Check if user has some permissions but not this one
            if user_allowed:
                reason = "Permission denied by policy"
            elif role_allowed:
                reason = "Role permission conflict"

        # Audit log
        self._audit_log.append({
            "timestamp": time.time(),
            "user_id": user_id,
            "resource_type": resource_type.value,
            "action": action.value,
            "target": target,
            "allowed": allowed,
            "applicable_permissions": [p.to_dict() for p in applicable_perms],
        })

        # Keep audit log bounded
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

        self._save()

        return {
            "allowed": allowed,
            "reason": reason,
            "applicable_permissions": applicable_perms,
            "user_id": user_id,
            "resource_type": resource_type.value,
            "action": action.value,
        }

    # ==================== Utility Methods ====================

    def export_policy(self) -> Dict[str, Any]:
        """Export RBAC policy for review"""
        self._load()
        return {
            "roles": {rid: role.to_dict() for rid, role in self._roles.items()},
            "user_permissions": {
                uid: [p.to_dict() for p in perms]
                for uid, perms in self._permissions_by_user.items()
            },
            "audit_log_count": len(self._audit_log),
        }

    def clear_audit_log(self) -> None:
        """Clear audit log"""
        self._audit_log = []
        self._save()


# Module-level convenience
_default_rbac: Optional[RBACManager] = None


def get_rbac_manager() -> RBACManager:
    """Get default RBAC manager"""
    global _default_rbac
    if _default_rbac is None:
        _default_rbac = RBACManager()
    return _default_rbac


def create_role(
    name: str,
    description: Optional[str] = None,
    **kwargs
) -> Role:
    """Convenience function to create role"""
    return get_rbac_manager().create_role(name, **kwargs)


def check_access(
    user_id: str,
    resource_type: ResourceType,
    action: PermissionAction,
    **kwargs
) -> Dict[str, Any]:
    """Convenience function to check access"""
    return get_rbac_manager().check_access(user_id, resource_type, action, **kwargs)
