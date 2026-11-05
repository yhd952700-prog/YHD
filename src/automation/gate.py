"""三重闸门：装配闸 / 后端闸 / 主体闸。

任一闸未打开都视为**关闭**，调用方不得注册任何自动化工具（fail-closed）。
默认状态全是关闭：未显式配置时工具列表必须为空，而不是"注册了但拒绝执行"。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Tuple

from .contracts import BackendKind

ASSEMBLY_ENV = "LIUHAO_AUTOMATION_ENABLED"
BACKEND_ENV = "LIUHAO_AUTOMATION_BACKEND"
BACKEND_KIND_ENV = "LIUHAO_AUTOMATION_BACKEND_KIND"

#: 本轮只允许 fake；真实后端（native / browser / android）下一轮再接入。
SUPPORTED_BACKENDS: Tuple[str, ...] = (BackendKind.FAKE.value,)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(value: Optional[str]) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class GateDecision:
    """闸门判决。``allowed`` 仅在三个闸同时打开时为 True。"""

    allowed: bool
    reason: str
    assembly_open: bool
    backend_open: bool
    principal_open: bool
    backend_kind: Optional[BackendKind] = None
    principal_kind: str = "unknown"

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "assembly_open": self.assembly_open,
            "backend_open": self.backend_open,
            "principal_open": self.principal_open,
            "backend_kind": self.backend_kind.value if self.backend_kind else None,
            "principal_kind": self.principal_kind,
        }


class AutomationGate:
    """读取配置并判定是否允许自动化能力上线。

    Args:
        env: 可注入的环境映射（测试用）；None 时读真实进程环境。
        backend_kinds: 允许的后端名；默认只有 ``fake``。
        principal_kind_resolver: 主体类型解析器；默认查身份内核，查不到即
            ``unknown``（因此主体闸关闭）。
    """

    def __init__(
        self,
        *,
        env: Optional[Mapping[str, str]] = None,
        backend_kinds: Sequence[str] = SUPPORTED_BACKENDS,
        principal_kind_resolver: Optional[Callable[[str], Optional[str]]] = None,
    ) -> None:
        self._env = env
        self._backend_kinds = tuple(str(k).strip().lower() for k in backend_kinds)
        self._resolver = principal_kind_resolver

    def _get(self, key: str) -> Optional[str]:
        if self._env is not None:
            return self._env.get(key)
        return os.environ.get(key)

    def evaluate(
        self,
        principal: str,
        *,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> GateDecision:
        assembly_open = _truthy(self._get(ASSEMBLY_ENV))

        raw_backend = str(self._get(BACKEND_ENV) or self._get(BACKEND_KIND_ENV) or "")
        raw_backend = raw_backend.strip().lower()
        backend_open = raw_backend in self._backend_kinds
        backend_kind: Optional[BackendKind] = None
        if backend_open:
            try:
                backend_kind = BackendKind(raw_backend)
            except ValueError:
                backend_open = False

        principal_kind = self._principal_kind(principal, metadata)
        principal_open = bool(str(principal or "").strip()) and principal_kind == "human"

        reasons = []
        if not assembly_open:
            reasons.append("assembly_closed")
        if not backend_open:
            reasons.append("backend_closed")
        if not principal_open:
            reasons.append("principal_closed")

        return GateDecision(
            allowed=assembly_open and backend_open and principal_open,
            reason=";".join(reasons) or "open",
            assembly_open=assembly_open,
            backend_open=backend_open,
            principal_open=principal_open,
            backend_kind=backend_kind,
            principal_kind=principal_kind,
        )

    def _principal_kind(
        self,
        principal: str,
        metadata: Optional[Mapping[str, object]],
    ) -> str:
        if metadata is not None:
            kind = str(metadata.get("kind") or "").strip().lower()
            return kind or "unknown"
        if self._resolver is not None:
            try:
                kind = self._resolver(principal)
            except Exception:  # noqa: BLE001 - 解析失败即关闭，绝不失败开放
                return "unknown"
            return str(kind or "").strip().lower() or "unknown"
        return self._kernel_principal_kind(principal)

    @staticmethod
    def _kernel_principal_kind(principal: str) -> str:
        """从身份内核解析 metadata.kind；任何异常都回落到 unknown（关闭）。"""

        try:
            from ..kernels.identity import get_identity_manager

            manager = get_identity_manager()
            identity = manager.get_identity(principal) or manager.get_identity_by_principal(
                principal
            )
            if identity is None:
                return "unknown"
            return str((identity.metadata or {}).get("kind") or "").strip().lower() or "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"
