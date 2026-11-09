"""Apps 产品面 —— 把"插件"真实呈现给人类并允许激活（Top-3 #2 缺口）。

后端插件内核 ``src.kernels/plugin/__init__.py`` 长期只有 ``register/unregister/
discover``，``load_plugin`` 标注 UNIMPLEMENTED，``activate_plugin`` 因找不到
``PluginInterface`` 永远失败。本模块把"已注册插件"与"激活"两个真实动作摊开给人看：
* ``GET /v1/plugins`` 返回真实注册表（id/name/version/status/active）；
* ``POST /v1/plugins/{plugin_id}/activate`` 真正加载插件并标记 ACTIVE。

诚实边界：本模块不发明任何插件，只渲染内核里**真实存在**的注册项；未知 id 返回 404，
激活失败会在响应里带真实 error，绝不假装成功。人类主权闸门（require_human_principal）
与 Projects 面一致。
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from .policy import require_admin_principal, require_human_principal
from src.kernels._crosscutting import bind_request_principal
from src.kernels.plugin import PluginStatus, get_plugin_registry

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])


def _serialize(info: Any) -> Dict[str, Any]:
    """Turn a PluginInfo into an honest, frontend-friendly dict."""
    status = info.status.value if isinstance(info.status, PluginStatus) else info.status
    return {
        "plugin_id": info.plugin_id,
        "name": info.name,
        "version": info.version,
        "kernel_type": info.kernel_type,
        "status": status,
        "active": status == PluginStatus.ACTIVE.value,
        "activatable": bool(getattr(info, "activatable", False)),
        "builtin": bool(getattr(info, "is_builtin", False)),
        "capabilities": list(info.capabilities),
        "scope": info.scope,
        "error": info.error,
    }


@router.get("")
def list_plugins(
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """List every **loadable** registered plugin with its real status.

    Entries without a module entry point are historical placeholders (the old
    V3.0 seed rows such as ``identity-impl`` / ``tenant-A-identity-plugin``).
    They can never be activated, so they are kept out of the enable list
    instead of being rendered as permanently failing buttons.
    """
    registry = get_plugin_registry()
    all_plugins = [_serialize(info) for info in registry.list_plugins()]
    # 内置能力（builtin）没有外部模块入口，但随包提供、已生效，必须出现在列表里；
    # 历史占位行（无入口且非内置）不可激活。直接隐藏会让前端与后端登记数对不上，
    # 因此一并返回被略过的条数，由前端用灰字如实说明。
    plugins = [
        p for p in all_plugins if p["activatable"] or p["active"] or p["builtin"]
    ]
    hidden = len(all_plugins) - len(plugins)
    active = [p for p in plugins if p["active"]]
    return {
        "total": len(plugins),
        "active": len(active),
        "plugins": plugins,
        "hidden": hidden,
    }


@router.post("/{plugin_id}/activate")
def activate_plugin(
    plugin_id: str,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """Activate a registered plugin (real import + instantiate)."""
    registry = get_plugin_registry()
    info = registry.get_plugin(plugin_id)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到插件「{plugin_id}」。请刷新列表后重试。",
        )
    if not getattr(info, "activatable", False):
        raise HTTPException(
            status_code=409,
            detail="该条目没有可加载的模块入口，属于历史登记占位，无需激活。",
        )
    # Privilege/lifecycle change initiated by a verified admin: attribute the
    # kernel audit to that human. Authority is still adjudicated exactly as
    # before (binding is attribution only).
    with bind_request_principal(principal, kind="human", source="http-request"):
        result = registry.activate_plugin(plugin_id)
    return _serialize(result)


# 这里**故意没有** ``POST /v1/plugins/{plugin_id}/execute``（设计决策
# §10.8.3-1：不做 HTTP 远程执行）。插件能力只在进程内调用；HTTP 面只负责把
# 真实注册表摊开（GET）和改变激活状态（activate）。0.1.2 的产品口径是"应用页
# 只有内置示例能力"，旧插件接口返回 404，因此也不存在"传能力名就替你跑"的
# 入口。tests/test_policy_approval_http.py 逐路由钉死这条约束。
