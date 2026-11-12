"""仓库内置员工角色 —— 员工定义属于产品，不属于某次沙箱里的临时数据。

验收项三要求「AI 全栈工程师」在仓库里 grep 得到，并且它的能力清单与
``set_agent_resolver`` 的 agent→执行器映射都来自这份定义。之前这条员工只在
沙箱 ``liuhao_employees.json`` 里临时建过，仓库里没有出处，任务因此可能记成
“完成但没有归属”（``COMPLETED_UNATTRIBUTED``）。

这里提供四件事：

* :data:`BUILTIN_EMPLOYEE_SPECS` —— 内置角色名 / 职责 / 能力清单；
* :func:`ensure_builtin_employees` —— 幂等地把内置角色写进 ``EmployeeStore``；
* :func:`employee_roster` —— 给模型拆解器的花名册行；
* :func:`build_agent_executor_resolver` —— agent id → 真实执行器 callable 的
  映射，供 ``ActionExecutor.set_agent_resolver`` 使用。

安全边界：内置角色只拿“写文件 / 纯计算 / 受限预览”这些本地能力，没有 shell、
没有任意命令、没有出网凭据。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from .employee import Employee

logger = logging.getLogger("liuhao.ai.builtin_employees")

#: 内置「AI 全栈工程师」的能力清单。执行器必须真的存在，否则任务会在
#: 计划校验阶段被拒，而不是跑一半再失败。
FULLSTACK_CAPABILITIES: tuple = (
    "file_write",
    "python_compute",
    "preview_start",
    "preview_stop",
    "execution_pipeline",
)

#: 产品内置员工定义（目前一位）。新增角色只需在这里追加。
BUILTIN_EMPLOYEE_SPECS: tuple = (
    {
        "name": "AI 全栈工程师",
        "role_description": (
            "端到端交付网页与小工具：拆解需求、完整写盘、起本地静态预览并"
            "自检页面可访问；只读工作区，不执行任意命令、不出网。"
        ),
        "agent_count": 2,
        "agent_types": ["general", "general"],
        "enable_observability": False,
        "default_capabilities": list(FULLSTACK_CAPABILITIES),
    },
)


def builtin_employee_specs() -> List[Dict[str, Any]]:
    """返回内置角色定义的副本（调用方修改不会污染全局定义）。"""
    return [
        dict(spec, default_capabilities=list(spec.get("default_capabilities") or ()))
        for spec in BUILTIN_EMPLOYEE_SPECS
    ]


def ensure_builtin_employees(store: Any, owner_account_id: str = "") -> List[str]:
    """幂等地把内置角色写进员工库，返回本次**新建**的角色名。

    ``store`` 需提供 ``ensure_builtin_employees``（见
    :meth:`src.ai.employee_store.EmployeeStore.ensure_builtin_employees`）。
    """
    ensure = getattr(store, "ensure_builtin_employees", None)
    if not callable(ensure):
        raise TypeError("employee store does not support ensure_builtin_employees")
    return list(ensure(owner_account_id=owner_account_id) or [])


def _active_employees(
    store: Any, owner_account_id: Optional[str] = None
) -> List[Employee]:
    load_many = getattr(store, "load_many", None)
    if callable(load_many):
        employees = list(load_many())
    else:
        employees = [
            store.load(name) for name in store.list_employees()
        ]
    result = []
    for employee in employees:
        if employee is None:
            continue
        if getattr(employee, "employment_status", Employee.ACTIVE) != Employee.ACTIVE:
            continue
        if (
            owner_account_id is not None
            and str(getattr(employee, "owner_account_id", "") or "")
            != str(owner_account_id)
        ):
            continue
        result.append(employee)
    return result


def employee_roster(
    store: Any, owner_account_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """给 LLM 拆解器的花名册（只含真实在职员工与其 agent id）。"""
    rows: List[Dict[str, Any]] = []
    for employee in _active_employees(store, owner_account_id):
        rows.append({
            "name": employee.name,
            "role": getattr(employee, "role_description", "") or "",
            "capabilities": list(getattr(employee, "default_capabilities", []) or []),
            # 与 LLMTaskDecomposer 的花名册契约一致：必须叫 ``agent_ids``。
            "agent_ids": list(employee.agents.keys()),
        })
    return rows


def build_agent_executor_resolver(
    store: Any,
    shared_executor: Callable[[str, Dict[str, Any]], Any],
    owner_account_id: Optional[str] = None,
) -> Callable[[str], Optional[Callable[[str, Dict[str, Any]], Any]]]:
    """构造 ``set_agent_resolver`` 需要的 agent→执行器映射。

    ``set_agent_resolver`` 的契约是：输入 agent id，返回 ``(capability_id,
    inputs) -> output`` 的**可调用执行器**。返回员工名字字符串会直接触发
    ``'str' object is not callable`` —— 这正是验收项三要消灭的失败形态。
    这里每个已知 agent id 都绑定到同一个真实执行器（``LCore`` 的
    ``capability_executor``），未知 id 返回 ``None``（引擎会回退到默认路径）。
    """
    agent_to_employee: Dict[str, str] = {}
    for employee in _active_employees(store, owner_account_id):
        for agent_id in employee.agents.keys():
            agent_to_employee[str(agent_id)] = employee.name

    def resolve(agent_id: str) -> Optional[Callable[[str, Dict[str, Any]], Any]]:
        who = agent_to_employee.get(str(agent_id))
        if who is None:
            return None

        def _execute(capability_id: str, inputs: Dict[str, Any]) -> Any:
            logger.debug(
                "evt=agent_executor_dispatch agent_id=%s employee=%s capability=%s",
                agent_id, who, capability_id,
            )
            return shared_executor(capability_id, inputs)

        return _execute

    return resolve


def build_agent_name_resolver(store: Any) -> Callable[[str], Optional[str]]:
    """agent id → 员工名，供汇总/归属展示使用。"""
    mapping: Dict[str, str] = {}
    for employee in _active_employees(store):
        for agent_id in employee.agents.keys():
            mapping[str(agent_id)] = employee.name
    return lambda agent_id: mapping.get(str(agent_id))


__all__ = [
    "BUILTIN_EMPLOYEE_SPECS",
    "FULLSTACK_CAPABILITIES",
    "build_agent_executor_resolver",
    "build_agent_name_resolver",
    "builtin_employee_specs",
    "employee_roster",
    "ensure_builtin_employees",
]
