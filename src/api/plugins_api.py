"""Plugins API endpoints for LiuHao AI OS.

RESTful API endpoints for plugin management:
- Plugin listing (installed and registry)
- Plugin management (install, uninstall, upgrade)
- Plugin enable/disable
- Plugin configuration
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field


router = APIRouter(prefix="/plugins", tags=["plugins"])


# =====================================================================
# Models
# =====================================================================


class PluginInstallRequest(BaseModel):
    """Plugin installation request."""
    plugin_id: str = Field(..., description="Plugin identifier")
    version: Optional[str] = Field(default=None, description="Specific version")


class PluginConfigUpdate(BaseModel):
    """Plugin configuration update request."""
    config: Dict[str, Any] = Field(..., description="Configuration key-value pairs")


class PluginResponse(BaseModel):
    """Plugin response model."""
    id: str
    name: str
    version: str
    description: str
    status: str
    author: str
    category: str
    tags: List[str]
    installed_at: str
    enabled_at: Optional[str] = None
    config: Dict[str, Any]


class PluginListResponse(BaseModel):
    """Paginated plugin list response."""
    total: int
    limit: int
    offset: int
    items: List[PluginResponse]


class RegistryPluginResponse(BaseModel):
    """Registry plugin entry response."""
    id: str
    name: str
    version: str
    description: str
    author: str
    category: str
    tags: List[str]
    downloads: int
    rating: float
    installed: bool


# =====================================================================
# In-memory stores (scaffold)
# =====================================================================


_installed: Dict[str, Dict[str, Any]] = {}
_registry: Dict[str, Dict[str, Any]] = {}


def _seed_sample_data() -> None:
    """Seed with sample plugin data (once)."""
    if _installed:
        return

    installed_plugins = [
        ("plugin-slack", "Slack Integration", "1.2.0", "Integrate with Slack for notifications",
         "liuhao", "notification", ["slack", "messaging"], "enabled"),
        ("plugin-github", "GitHub Integration", "2.0.1", "GitHub repository and PR management",
         "liuhao", "devops", ["github", "git", "devops"], "enabled"),
        ("plugin-jira", "JIRA Integration", "1.5.0", "JIRA issue tracking integration",
         "liuhao", "project", ["jira", "issues", "tickets"], "disabled"),
        ("plugin-notion", "Notion Integration", "0.9.3", "Notion workspace integration",
         "community", "productivity", ["notion", "docs"], "installed"),
    ]
    now = datetime.utcnow().isoformat()
    for pid, name, ver, desc, author, cat, tags, status in installed_plugins:
        _installed[pid] = {
            "id": pid,
            "name": name,
            "version": ver,
            "description": desc,
            "status": status,
            "author": author,
            "category": cat,
            "tags": tags,
            "installed_at": now,
            "enabled_at": now if status == "enabled" else None,
            "config": {},
        }

    registry_plugins = [
        ("plugin-trello", "Trello Integration", "1.0.0", "Trello board integration",
         "community", "productivity", ["trello", "kanban"], 3200, 4.2),
        ("plugin-discord", "Discord Integration", "0.8.0", "Discord bot integration",
         "community", "notification", ["discord", "chat"], 5100, 4.5),
        ("plugin-sentry", "Sentry Integration", "1.1.0", "Error tracking with Sentry",
         "liuhao", "monitoring", ["sentry", "errors", "monitoring"], 2800, 4.7),
        ("plugin-grafana", "Grafana Integration", "2.0.0", "Grafana dashboards integration",
         "liuhao", "monitoring", ["grafana", "dashboards", "metrics"], 4500, 4.8),
    ]
    for pid, name, ver, desc, author, cat, tags, downloads, rating in registry_plugins:
        _registry[pid] = {
            "id": pid,
            "name": name,
            "version": ver,
            "description": desc,
            "author": author,
            "category": cat,
            "tags": tags,
            "downloads": downloads,
            "rating": rating,
        }


_seed_sample_data()


def _get_plugin_or_404(plugin_id: str) -> Dict[str, Any]:
    """Get installed plugin by ID or raise 404."""
    plugin = _installed.get(plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
    return plugin


# =====================================================================
# Plugin Listing Endpoints
# =====================================================================


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    status: Optional[str] = Query(None, description="Filter by status"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """List installed plugins with pagination and filtering.

    Args:
        status: Filter by status (enabled, disabled, installed).
        category: Filter by category.
        limit: Result limit.
        offset: Pagination offset.

    Returns:
        Paginated plugin list.
    """
    plugins = list(_installed.values())
    if status:
        plugins = [p for p in plugins if p["status"] == status]
    if category:
        plugins = [p for p in plugins if p["category"] == category]
    plugins.sort(key=lambda p: p["name"].lower())
    total = len(plugins)
    items = plugins[offset : offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin(plugin_id: str) -> Dict[str, Any]:
    """Get details of an installed plugin.

    Args:
        plugin_id: Plugin identifier.

    Returns:
        Plugin details.

    Raises:
        HTTPException: 404 if not found.
    """
    return _get_plugin_or_404(plugin_id)


# =====================================================================
# Plugin Management Endpoints
# =====================================================================


@router.post("/install", status_code=201)
async def install_plugin(body: PluginInstallRequest) -> Dict[str, Any]:
    """Install a plugin from the registry.

    Args:
        body: Installation request.

    Returns:
        Installed plugin details.

    Raises:
        HTTPException: 409 if already installed, 404 if not in registry.
    """
    if body.plugin_id in _installed:
        raise HTTPException(status_code=409, detail=f"Plugin already installed: {body.plugin_id}")
    if body.plugin_id not in _registry:
        raise HTTPException(status_code=404, detail=f"Plugin not found in registry: {body.plugin_id}")

    reg = _registry[body.plugin_id]
    now = datetime.utcnow().isoformat()
    plugin = {
        "id": body.plugin_id,
        "name": reg["name"],
        "version": body.version or reg["version"],
        "description": reg["description"],
        "status": "installed",
        "author": reg["author"],
        "category": reg["category"],
        "tags": reg["tags"],
        "installed_at": now,
        "enabled_at": None,
        "config": {},
    }
    _installed[body.plugin_id] = plugin
    return plugin


@router.delete("/{plugin_id}", status_code=204)
async def uninstall_plugin(plugin_id: str) -> None:
    """Uninstall a plugin.

    Args:
        plugin_id: Plugin identifier.

    Raises:
        HTTPException: 404 if not found.
    """
    if plugin_id not in _installed:
        raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
    del _installed[plugin_id]


@router.post("/{plugin_id}/enable")
async def enable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Enable an installed plugin.

    Args:
        plugin_id: Plugin identifier.

    Returns:
        Enable confirmation.

    Raises:
        HTTPException: 404 if not found.
    """
    plugin = _get_plugin_or_404(plugin_id)
    plugin["status"] = "enabled"
    plugin["enabled_at"] = datetime.utcnow().isoformat()
    return {
        "id": plugin_id,
        "status": "enabled",
        "enabled_at": plugin["enabled_at"],
    }


@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str) -> Dict[str, Any]:
    """Disable an enabled plugin.

    Args:
        plugin_id: Plugin identifier.

    Returns:
        Disable confirmation.

    Raises:
        HTTPException: 404 if not found.
    """
    plugin = _get_plugin_or_404(plugin_id)
    plugin["status"] = "disabled"
    plugin["enabled_at"] = None
    return {
        "id": plugin_id,
        "status": "disabled",
        "disabled_at": datetime.utcnow().isoformat(),
    }


@router.post("/{plugin_id}/upgrade")
async def upgrade_plugin(
    plugin_id: str,
    body: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    """Upgrade a plugin to latest or specified version.

    Args:
        plugin_id: Plugin identifier.
        body: Dict with optional 'version' field.

    Returns:
        Upgrade result.

    Raises:
        HTTPException: 404 if not found.
    """
    plugin = _get_plugin_or_404(plugin_id)
    old_version = plugin["version"]
    new_version = body.get("version") or f"{int(plugin['version'].split('.')[0]) + 1}.0.0"
    plugin["version"] = new_version
    return {
        "id": plugin_id,
        "old_version": old_version,
        "new_version": new_version,
        "status": "upgraded",
    }


# =====================================================================
# Plugin Configuration Endpoints
# =====================================================================


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str) -> Dict[str, Any]:
    """Get plugin configuration.

    Args:
        plugin_id: Plugin identifier.

    Returns:
        Plugin configuration.

    Raises:
        HTTPException: 404 if not found.
    """
    plugin = _get_plugin_or_404(plugin_id)
    return {
        "plugin_id": plugin_id,
        "config": plugin["config"],
    }


@router.put("/{plugin_id}/config")
async def update_plugin_config(plugin_id: str, body: PluginConfigUpdate) -> Dict[str, Any]:
    """Update plugin configuration.

    Args:
        plugin_id: Plugin identifier.
        body: Configuration update.

    Returns:
        Updated configuration.

    Raises:
        HTTPException: 404 if not found.
    """
    plugin = _get_plugin_or_404(plugin_id)
    plugin["config"].update(body.config)
    return {
        "plugin_id": plugin_id,
        "config": plugin["config"],
        "updated_at": datetime.utcnow().isoformat(),
    }


# =====================================================================
# Plugin Registry Endpoints
# =====================================================================


@router.get("/registry/search")
async def search_registry(
    query: str = Query(..., min_length=1, description="Search query"),
    category: Optional[str] = Query(None, description="Filter by category"),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """Search the plugin registry.

    Args:
        query: Search query.
        category: Optional category filter.
        limit: Maximum results.

    Returns:
        Search results.
    """
    query_lower = query.lower()
    results = []
    for pid, reg in _registry.items():
        if category and reg["category"] != category:
            continue
        if (
            query_lower in reg["name"].lower()
            or query_lower in reg["description"].lower()
            or any(query_lower in t.lower() for t in reg["tags"])
        ):
            results.append({
                "id": reg["id"],
                "name": reg["name"],
                "version": reg["version"],
                "description": reg["description"],
                "author": reg["author"],
                "category": reg["category"],
                "tags": reg["tags"],
                "downloads": reg["downloads"],
                "rating": reg["rating"],
                "installed": pid in _installed,
            })
    results = results[:limit]
    return {"query": query, "total": len(results), "limit": limit, "items": results}


@router.get("/registry/{plugin_id}")
async def get_registry_plugin(plugin_id: str) -> Dict[str, Any]:
    """Get details of a plugin in the registry.

    Args:
        plugin_id: Plugin identifier.

    Returns:
        Registry plugin details.

    Raises:
        HTTPException: 404 if not found.
    """
    if plugin_id not in _registry:
        raise HTTPException(status_code=404, detail=f"Plugin not found in registry: {plugin_id}")
    reg = _registry[plugin_id]
    return {
        "id": reg["id"],
        "name": reg["name"],
        "version": reg["version"],
        "description": reg["description"],
        "author": reg["author"],
        "category": reg["category"],
        "tags": reg["tags"],
        "downloads": reg["downloads"],
        "rating": reg["rating"],
        "installed": plugin_id in _installed,
    }
