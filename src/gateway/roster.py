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

数据源不可用时返回 ``available: false`` + 空列表 + ``error``（与 ``dashboard.py``
同一降级约定），**绝不**用占位员工撑起界面。
"""

from __future__ import annotations

import logging
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter

router = APIRouter(prefix="/v1", tags=["roster"])

logger = logging.getLogger(__name__)

#: 仓库根（本文件位于 ``<root>/src/gateway/roster.py``）。
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
    from ..ai.providers import ProviderFactory, _provider_env, get_provider

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


@router.get("/dashboard/roster")
def dashboard_roster() -> Dict[str, Any]:
    """AI 员工名册（真实注册表）+ provider 服务面。

    返回的每一条都能在仓库里定位到：kernel/能力层 → ``capability-registry.yaml``；
    provider → ``src/ai/providers.py`` 的 ``ProviderFactory._providers``。
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
            "employees": len(kernels) + len(layers),
            "declared_test_cases": sum(int(item.get("tests") or 0) for item in layers),
        },
        "kernels": kernels,
        "layers": layers,
        "domains": registry.get("domains", []),
        "bands": registry.get("bands", []),
        "providers": providers,
    }
