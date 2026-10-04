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

from .policy import require_human_principal
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
        "capabilities": list(info.capabilities),
        "scope": info.scope,
        "error": info.error,
    }


@router.get("")
def list_plugins(
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """List every registered plugin with its real status."""
    registry = get_plugin_registry()
    plugins = [_serialize(info) for info in registry.list_plugins()]
    active = [p for p in plugins if p["active"]]
    return {"total": len(plugins), "active": len(active), "plugins": plugins}


@router.post("/{plugin_id}/activate")
def activate_plugin(
    plugin_id: str,
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """Activate a registered plugin (real import + instantiate)."""
    registry = get_plugin_registry()
    if registry.get_plugin(plugin_id) is None:
        raise HTTPException(status_code=404, detail=f"Unknown plugin: {plugin_id}")
    info = registry.activate_plugin(plugin_id)
    return _serialize(info)
