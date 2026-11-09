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
    result = registry.activate_plugin(plugin_id)
    return _serialize(result)


@router.post("/{plugin_id}/execute")
async def execute_plugin(
    plugin_id: str,
    payload: Dict[str, Any] | None = None,
    principal: str = Depends(require_admin_principal),
) -> Dict[str, Any]:
    """Execute a capability on an activated plugin and return the real result.

    The endpoint exists so "已启用" is verifiable: it lazily runs the plugin's
    ``initialize`` / ``start`` lifecycle (if needed) and then calls the real
    ``PluginInterface.execute`` implementation.
    """
    registry = get_plugin_registry()
    info = registry.get_plugin(plugin_id)
    if info is None:
        raise HTTPException(
            status_code=404,
            detail=f"没有找到插件「{plugin_id}」。",
        )
    if not getattr(info, "activatable", False):
        raise HTTPException(
            status_code=409,
            detail="该条目没有可加载的模块入口，无法执行能力。",
        )

    instance = registry.get_loaded_plugin(plugin_id)
    if instance is None:
        result = registry.activate_plugin(plugin_id)
        if result is None or result.status != PluginStatus.ACTIVE:
            raise HTTPException(
                status_code=409,
                detail=(result.error if result else None) or "插件尚未激活，无法执行能力。",
            )
        instance = registry.get_loaded_plugin(plugin_id)
    if instance is None:
        raise HTTPException(status_code=409, detail="插件尚未激活，无法执行能力。")

    try:
        if not instance.can_execute():
            await instance.initialize()
            await instance.start()
        output = await instance.execute(payload or {})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"插件能力执行失败：{exc}") from exc

    return {
        "plugin_id": plugin_id,
        "capabilities": list(info.capabilities),
        "result": output,
    }
