"""AI 员工名册的「纯数据构建」层（不依赖 fastapi）。

本模块刻意 **不** 引用 ``fastapi``，以便独立验证脚本（``scripts/verify_roster_real_employees.py``）
可以在未安装 fastapi 的 **系统 python** 下直接 ``from src.ai.roster_payload import build_roster_payload``
来跑真实代码。

职责（与 ``gateway/roster.py`` 的 NO-FAKE 契约一致）：
1. ``capability-registry.yaml`` 仍是内核/能力层的唯一真实来源 —— 它们是**注册表信息**，
   不是"员工"。
2. **真实持久化员工** 来自 ``EmployeeStore``（``LIUHAO_WORKSPACE_ROOT`` 可重定向），
   作为 ``real_employees`` 透出；``totals.employees`` 只数真实员工，不再把模块算作员工。
3. ``provider_status`` 仍取自 ``src.ai.providers`` 的真实类。

``gateway/roster.py`` 的 ``@router.get`` 薄包装仅调用 ``build_roster_payload()``。
"""

from __future__ import annotations

import logging
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 仓库根（本文件位于 ``<root>/src/ai/roster_payload.py``，向上两级到仓库根）。
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 权威能力注册表（14 kernel + 14 能力层）。
REGISTRY_PATH = _REPO_ROOT / "capability-registry.yaml"

# ---------------------------------------------------------------------------
# 展示分组（presentation grouping）—— 分组名是**呈现用标签**，不是注册表字段。
# 每条 kernel 的真实归属见其 ``kernel`` 字段；此处只决定它落在哪个展示域。
# ---------------------------------------------------------------------------
DOMAIN_BY_KERNEL: Dict[str, str] = {
    "context": "cognition",
    "memory": "cognition",
    "event": "cognition",
    "capability": "cognition",
    "execution": "execution",
    "resource": "execution",
    "plugin": "execution",
    "network": "execution",
    "policy": "governance",
    "security": "governance",
    "audit": "governance",
    "identity": "trust",
    "trust": "trust",
    "evaluation": "trust",
}

DOMAIN_LABELS: Dict[str, str] = {
    "cognition": "认知内核",
    "execution": "执行内核",
    "governance": "治理内核",
    "trust": "信任内核",
}

#: 能力层按 Phase 归入阶段带（同样是展示分组）。
BAND_BY_PHASE: Dict[int, str] = {
    9: "loop",
    10: "loop",
    11: "loop",
    12: "org",
    13: "org",
    14: "org",
    15: "world",
    16: "world",
    17: "world",
    18: "evo",
    19: "evo",
    20: "evo",
    21: "evo",
}

BAND_LABELS: Dict[str, str] = {
    "loop": "内核闭环",
    "org": "组织与任务",
    "world": "世界与治理",
    "evo": "验证与进化",
}

#: 每个 provider 需要的密钥环境变量（``AI_PROVIDER_KEY`` 是所有 provider 的通用兜底，
#: 见 ``BaseProvider.__init__``）。``mock``/``ollama`` 不需要云端密钥。
PROVIDER_KEY_ENVS: Dict[str, Tuple[str, ...]] = {
    "mock": (),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "google": ("GOOGLE_API_KEY",),
    "ollama": ("OLLAMA_BASE_URL",),
    "moonshot": ("MOONSHOT_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
}

#: provider 的展示名（仅用于界面标签；类型串本身来自注册表）。
PROVIDER_LABELS: Dict[str, str] = {
    "mock": "Mock（内置）",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "google": "Google Gemini",
    "ollama": "Ollama（本地）",
    "moonshot": "Moonshot Kimi",
    "deepseek": "DeepSeek",
}

# 注册表解析结果缓存：(mtime, payload)。文件小但每轮轮询都读没必要。
_cache: Optional[Tuple[float, Dict[str, Any]]] = None


def _phase_number(raw: Any) -> Optional[int]:
    """``"Phase 9"`` → ``9``；解析不出来时返回 None（不猜）。"""
    text = str(raw or "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def load_registry() -> Dict[str, Any]:
    """读取 ``capability-registry.yaml``。失败时返回带 ``error`` 的空壳。"""
    global _cache

    try:
        mtime = REGISTRY_PATH.stat().st_mtime
    except OSError as exc:
        return {"available": False, "error": f"registry not found: {exc}", "kernels": [], "layers": []}

    if _cache is not None and _cache[0] == mtime:
        return _cache[1]

    try:
        import yaml  # noqa: F401  (config_manager 已依赖，属既有第三方栈)

        raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
        block = raw["capability-registry"]
    except Exception as exc:  # 解析失败不编造名册
        logger.warning("capability registry parse failed: %s", exc, exc_info=True)
        return {
            "available": False,
            "error": f"registry parse failed: {exc}",
            "kernels": [],
            "layers": [],
        }

    kernels: List[Dict[str, Any]] = []
    for entry in block.get("capabilities") or []:
        kernel = str(entry.get("kernel") or "")
        domain = DOMAIN_BY_KERNEL.get(kernel, "other")
        kernels.append(
            {
                "id": entry.get("id"),
                "legacy_id": entry.get("legacy_id"),
                "name": entry.get("name"),
                "kernel": kernel,
                "kind": entry.get("kind"),
                "status": entry.get("status"),
                "scope": entry.get("scope"),
                "source": entry.get("source"),
                "domain": domain,
                "domain_label": DOMAIN_LABELS.get(domain, "其他"),
            }
        )

    layers: List[Dict[str, Any]] = []
    for entry in block.get("capability-layers") or []:
        phase_no = _phase_number(entry.get("phase"))
        band = BAND_BY_PHASE.get(phase_no, "other") if phase_no is not None else "other"
        layers.append(
            {
                "id": entry.get("id"),
                "name": entry.get("name"),
                "module": entry.get("module"),
                "phase": entry.get("phase"),
                "status": entry.get("status"),
                "tests": entry.get("test_count") or 0,
                "band": band,
                "band_label": BAND_LABELS.get(band, "其他"),
            }
        )

    payload: Dict[str, Any] = {
        "available": True,
        "version": block.get("version"),
        "declared_totals": {
            "capabilities": block.get("total_capabilities"),
            "layers": block.get("total_layers"),
        },
        "kernels": kernels,
        "layers": layers,
        "domains": [
            {"key": key, "label": label}
            for key, label in DOMAIN_LABELS.items()
        ],
        "bands": [
            {"key": key, "label": label}
            for key, label in BAND_LABELS.items()
        ],
    }
    _cache = (mtime, payload)
    return payload


def provider_status() -> Dict[str, Any]:
    """真实 provider 面：已注册的类 + 当前生效的 provider + 密钥是否就位。"""
    # 与 dashboard.py 同一解析口径：os.environ 优先，其次 .env（经 ConfigManager）。
    # 只读 os.environ 会把写在 .env 里的 AI_PROVIDER_* / *_API_KEY 误报为"未配置"。
    from .providers import ProviderFactory, _provider_env, get_provider

    registered = sorted(ProviderFactory._providers.keys())

    active_type = _provider_env("AI_PROVIDER_TYPE", "mock")
    active: Dict[str, Any] = {"type": active_type, "model": None, "name": None}
    try:
        instance = get_provider()
        active["model"] = getattr(instance, "model", None)
        active["name"] = getattr(instance, "name", None)
        active["type"] = active_type
    except Exception as exc:
        active["error"] = str(exc)

    items: List[Dict[str, Any]] = []
    for provider_type in registered:
        required = PROVIDER_KEY_ENVS.get(provider_type, ())
        keys_present = [name for name in required if _provider_env(name)]
        # mock / 本地 ollama 无需密钥即视为就绪；云端 provider 需密钥或通用兜底键。
        generic_key = bool(_provider_env("AI_PROVIDER_KEY"))
        if not required or provider_type == "ollama":
            configured = True
        else:
            configured = bool(keys_present) or generic_key
        items.append(
            {
                "type": provider_type,
                "label": PROVIDER_LABELS.get(provider_type, provider_type),
                "registered": True,
                "configured": configured,
                "active": provider_type == active.get("type"),
                "required_env": list(required),
            }
        )

    return {
        "registered_count": len(registered),
        "configured_count": sum(1 for item in items if item["configured"]),
        "active": active,
        "items": items,
    }


def _build_real_employees() -> List[Dict[str, Any]]:
    """从 ``EmployeeStore`` 重建真实持久化员工列表。

    返回对象形如::

        {"name", "agent_count", "agents":[{"id","agent_type","status"}],
         "total_tasks_submitted","total_tasks_completed","total_tasks_failed"}

    若 store 文件缺失、``employee_store`` 不可导入、或任意员工加载失败，
    一律返回 ``[]``（并打 warning）—— **持久化失败绝不能让名册端点崩溃**。
    """
    try:
        from .employee_store import EmployeeStore
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("employee_store import failed, real_employees=[]: %s", exc)
        return []

    try:
        store = EmployeeStore()
    except Exception as exc:
        logger.warning("EmployeeStore init failed, real_employees=[]: %s", exc)
        return []

    try:
        names = store.list_employees()
    except Exception as exc:
        logger.warning("list_employees failed, real_employees=[]: %s", exc)
        return []

    employees: List[Dict[str, Any]] = []
    for name in names:
        try:
            emp = store.load(name)
        except Exception as exc:
            logger.warning("load(%r) failed, skipping: %s", name, exc)
            continue
        if emp is None:
            continue
        agents = [
            {
                "id": agent.id,
                "agent_type": agent.agent_type,
                "status": agent.status.value,
            }
            for agent in emp.agents.values()
        ]
        employees.append(
            {
                "name": emp.name,
                "agent_count": emp.agent_count,
                "agents": agents,
                "total_tasks_submitted": emp.total_tasks_submitted,
                "total_tasks_completed": emp.total_tasks_completed,
                "total_tasks_failed": emp.total_tasks_failed,
            }
        )
    return employees


def build_roster_payload() -> Dict[str, Any]:
    """构建名册响应 dict（无 fastapi 依赖，供端点与验证脚本共用）。

    关键契约变化（相对旧版）：
    - ``real_employees``：真实持久化员工（来自 ``EmployeeStore``）。
    - ``totals.employees``：只数真实员工 == ``len(real_employees)``，
      **不再** 把 kernel/能力层模块算作员工。
    - ``totals.kernels`` / ``totals.layers``：仍是注册表计数（14/14）。
    - ``totals.registry_entries``：保留 ``kernels + layers`` 这一真实数字，
      但它**只是注册表条目数**，不再是"员工数"。
    - ``kernels`` / ``layers`` / ``domains`` / ``bands`` / ``providers`` 等
      原样保留，保证 UI 不被破坏（仅语义上从"员工"降级为"注册表信息"）。
    """
    registry = load_registry()
    try:
        providers = provider_status()
    except Exception as exc:
        logger.warning("provider_status failed: %s", exc, exc_info=True)
        providers = {
            "registered_count": 0,
            "configured_count": 0,
            "active": {},
            "items": [],
            "error": str(exc),
        }

    kernels = registry.get("kernels") or []
    layers = registry.get("layers") or []
    real_employees = _build_real_employees()

    return {
        "generated_at": time.time(),
        "source": str(REGISTRY_PATH.name),
        "available": bool(registry.get("available")),
        "error": registry.get("error"),
        "version": registry.get("version"),
        "declared_totals": registry.get("declared_totals", {}),
        "totals": {
            "kernels": len(kernels),
            "layers": len(layers),
            "registry_entries": len(kernels) + len(layers),
            "employees": len(real_employees),
        },
        "real_employees": real_employees,
        "kernels": kernels,
        "layers": layers,
        "domains": registry.get("domains", []),
        "bands": registry.get("bands", []),
        "providers": providers,
    }
