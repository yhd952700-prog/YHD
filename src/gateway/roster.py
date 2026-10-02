"""AI 员工名册端点 —— 只透出仓库里可核实的真实注册表。

NO-FAKE 契约
------------
本端点**不构造**任何"员工"。名册的两个来源都是仓库中机器可读的真实资产：

1. ``capability-registry.yaml``（仓库根）
   - ``capabilities``        → 14 个 kernel（``LHX-C-*``，含 kernel/scope/status）
   - ``capability-layers``   → 14 个 ``src/ai/`` 能力层（``LHX-L-*``，含 module/phase/tests）
2. ``src.ai.providers.ProviderFactory._providers`` → 已实现的 7 个 LLM provider 类

唯一的一层"加工"是**展示分组**：把 14 个 kernel 按职责归入 4 个域、把 14 个能力层
按 Phase 归入 4 个阶段带。分组只影响呈现次序，不产生新事实 —— 每条的
``id`` / ``name`` / ``status`` / ``module`` / ``phase`` 一律从注册表原样透出，
前端不得由分组名反推未记录的语义。

**员工语义修正（见 P1 缺陷）**
------------------------------
旧版把 14 个 kernel + 14 个能力层模块当成"28 个 AI 员工"透出，实为误报
（``PRODUCT-READINESS.md`` P1 标记）。现在：

- ``real_employees``：来自真实持久化层 ``EmployeeStore`` 的**真实员工**
  （网关首次启动会由 ``AIStateManager._ensure_employee`` 写入
  ``liuhao-default``，因此真实员工数通常 ≥ 1）。
- ``totals.employees``：只数真实员工，不再把模块算作员工。
- ``kernels`` / ``layers`` 仍原样透出，但语义上明确是**注册表信息**，不是员工。

数据源不可用时返回 ``available: false`` + 空列表 + ``error``（与 ``dashboard.py``
同一降级约定），**绝不**用占位员工撑起界面。

纯数据构建逻辑位于 ``src/gateway/roster_payload.py``（刻意不依赖 fastapi），
本文件只负责把它挂到 ``/v1/dashboard/roster`` 路由上。
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter

from ..ai.roster_payload import build_roster_payload

router = APIRouter(prefix="/v1", tags=["roster"])

logger = logging.getLogger(__name__)


@router.get("/dashboard/roster")
def dashboard_roster() -> Dict[str, Any]:
    """AI 员工名册（真实注册表 + 真实持久化员工）+ provider 服务面。

    返回的每一条都能在仓库里定位到：
    - kernel/能力层 → ``capability-registry.yaml``；
    - ``real_employees`` → ``EmployeeStore``（``LIUHAO_WORKSPACE_ROOT`` 重定向）；
    - provider → ``src/ai/providers.py`` 的 ``ProviderFactory._providers``。
    """
    return build_roster_payload()
