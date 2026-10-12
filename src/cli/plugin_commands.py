"""Plugin management CLI commands for LiuHao AI OS.

Provides command-line interface for plugin management:
- plugin list, plugin info, plugin install
- plugin enable, plugin disable
- plugin upgrade, plugin uninstall
- plugin search
"""

from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class PluginStatus(str, Enum):
    """Plugin installation status."""
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"
    NOT_INSTALLED = "not_installed"


@dataclass
class PluginRecord:
    """A plugin record."""
    plugin_id: str
    name: str
    version: str
    description: str
    status: PluginStatus = PluginStatus.INSTALLED
    author: str = ""
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    installed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    enabled_at: Optional[str] = None
    config: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)


class PluginManager:
    """In-memory plugin manager for CLI operations.

    Provides plugin lifecycle management and search.
    """

    def __init__(self) -> None:
        self._plugins: Dict[str, PluginRecord] = {}
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._seed_sample_data()

    def _seed_sample_data(self) -> None:
        """Seed with sample plugins and registry data."""
        sample_plugins = [
            ("plugin-slack", "Slack Integration", "1.2.0", "Integrate with Slack for notifications",
             "liuhao", "notification", ["slack", "messaging"], PluginStatus.ENABLED),
            ("plugin-github", "GitHub Integration", "2.0.1", "GitHub repository and PR management",
             "liuhao", "devops", ["github", "git", "devops"], PluginStatus.ENABLED),
            ("plugin-jira", "JIRA Integration", "1.5.0", "JIRA issue tracking integration",
             "liuhao", "project", ["jira", "issues", "tickets"], PluginStatus.DISABLED),
            ("plugin-notion", "Notion Integration", "0.9.3", "Notion workspace integration",
             "community", "productivity", ["notion", "docs"], PluginStatus.INSTALLED),
        ]
        for pid, name, ver, desc, author, cat, tags, status in sample_plugins:
            self._plugins[pid] = PluginRecord(
                plugin_id=pid,
                name=name,
                version=ver,
                description=desc,
                status=status,
                author=author,
                category=cat,
                tags=tags,
                enabled_at=datetime.utcnow().isoformat() if status == PluginStatus.ENABLED else None,
            )

        # Registry entries (available for install)
        registry_plugins = [
            ("plugin-trello", "Trello Integration", "1.0.0", "Trello board integration",
             "community", "productivity", ["trello", "kanban"]),
            ("plugin-discord", "Discord Integration", "0.8.0", "Discord bot integration",
             "community", "notification", ["discord", "chat"]),
            ("plugin-sentry", "Sentry Integration", "1.1.0", "Error tracking with Sentry",
             "liuhao", "monitoring", ["sentry", "errors", "monitoring"]),
            ("plugin-grafana", "Grafana Integration", "2.0.0", "Grafana dashboards integration",
             "liuhao", "monitoring", ["grafana", "dashboards", "metrics"]),
        ]
        for pid, name, ver, desc, author, cat, tags in registry_plugins:
            self._registry[pid] = {
                "plugin_id": pid,
                "name": name,
                "version": ver,
                "description": desc,
                "author": author,
                "category": cat,
                "tags": tags,
                "downloads": 1000 + hash(pid) % 5000,
                "rating": 3.5 + hash(pid) % 15 / 10,
            }

    def list_plugins(
        self,
        status: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """List installed plugins.

        Args:
            status: Filter by status.
            category: Filter by category.
            limit: Result limit.
            offset: Pagination offset.

        Returns:
            Dict with paginated plugin list.
        """
        plugins = list(self._plugins.values())
        if status:
            plugins = [p for p in plugins if p.status.value == status]
        if category:
            plugins = [p for p in plugins if p.category == category]
        plugins.sort(key=lambda p: p.name.lower())
        total = len(plugins)
        plugins = plugins[offset : offset + limit]
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [
                {
                    "id": p.plugin_id,
                    "name": p.name,
                    "version": p.version,
                    "status": p.status.value,
                    "category": p.category,
                    "author": p.author,
                    "description": p.description,
                }
                for p in plugins
            ],
        }

    def get_plugin_info(self, plugin_id: str) -> Dict[str, Any]:
        """Get detailed info about a plugin.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Dict with plugin details.

        Raises:
            ValueError: If plugin not found.
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_id}")
        return {
            "id": plugin.plugin_id,
            "name": plugin.name,
            "version": plugin.version,
            "description": plugin.description,
            "status": plugin.status.value,
            "author": plugin.author,
            "category": plugin.category,
            "tags": plugin.tags,
            "installed_at": plugin.installed_at,
            "enabled_at": plugin.enabled_at,
            "dependencies": plugin.dependencies,
            "config": plugin.config,
        }

    def install_plugin(self, plugin_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Install a plugin from the registry.

        Args:
            plugin_id: Plugin identifier.
            version: Optional specific version.

        Returns:
            Dict with installation result.

        Raises:
            ValueError: If plugin not found in registry or already installed.
        """
        if plugin_id in self._plugins:
            raise ValueError(f"Plugin already installed: {plugin_id}")
        if plugin_id not in self._registry:
            raise ValueError(f"Plugin not found in registry: {plugin_id}")

        reg = self._registry[plugin_id]
        plugin = PluginRecord(
            plugin_id=plugin_id,
            name=reg["name"],
            version=version or reg["version"],
            description=reg["description"],
            status=PluginStatus.INSTALLED,
            author=reg["author"],
            category=reg["category"],
            tags=reg["tags"],
        )
        self._plugins[plugin_id] = plugin
        return {
            "id": plugin.plugin_id,
            "name": plugin.name,
            "version": plugin.version,
            "status": "installed",
            "installed_at": plugin.installed_at,
        }

    def enable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Enable an installed plugin.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Dict with enable result.

        Raises:
            ValueError: If plugin not found.
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_id}")
        plugin.status = PluginStatus.ENABLED
        plugin.enabled_at = datetime.utcnow().isoformat()
        return {
            "id": plugin_id,
            "status": "enabled",
            "enabled_at": plugin.enabled_at,
        }

    def disable_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Disable an enabled plugin.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Dict with disable result.

        Raises:
            ValueError: If plugin not found.
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_id}")
        plugin.status = PluginStatus.DISABLED
        return {
            "id": plugin_id,
            "status": "disabled",
            "disabled_at": datetime.utcnow().isoformat(),
        }

    def upgrade_plugin(self, plugin_id: str, version: Optional[str] = None) -> Dict[str, Any]:
        """Upgrade a plugin to latest or specified version.

        Args:
            plugin_id: Plugin identifier.
            version: Optional target version.

        Returns:
            Dict with upgrade result.

        Raises:
            ValueError: If plugin not found.
        """
        plugin = self._plugins.get(plugin_id)
        if not plugin:
            raise ValueError(f"Plugin not found: {plugin_id}")
        old_version = plugin.version
        new_version = version or f"{int(plugin.version.split('.')[0]) + 1}.0.0"
        plugin.version = new_version
        return {
            "id": plugin_id,
            "old_version": old_version,
            "new_version": new_version,
            "status": "upgraded",
        }

    def uninstall_plugin(self, plugin_id: str) -> Dict[str, Any]:
        """Uninstall a plugin.

        Args:
            plugin_id: Plugin identifier.

        Returns:
            Dict with uninstall result.

        Raises:
            ValueError: If plugin not found.
        """
        if plugin_id not in self._plugins:
            raise ValueError(f"Plugin not found: {plugin_id}")
        del self._plugins[plugin_id]
        return {
            "id": plugin_id,
            "status": "uninstalled",
        }

    def search_plugins(
        self,
        query: str,
        category: Optional[str] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Search the plugin registry.

        Args:
            query: Search query.
            category: Optional category filter.
            limit: Maximum results.

        Returns:
            Dict with search results.
        """
        query_lower = query.lower()
        results = []
        for pid, reg in self._registry.items():
            if category and reg["category"] != category:
                continue
            if (
                query_lower in reg["name"].lower()
                or query_lower in reg["description"].lower()
                or any(query_lower in t.lower() for t in reg["tags"])
            ):
                results.append(reg)
        results = results[:limit]
        return {
            "query": query,
            "total": len(results),
            "limit": limit,
            "items": [
                {
                    "id": r["plugin_id"],
                    "name": r["name"],
                    "version": r["version"],
                    "description": r["description"],
                    "author": r["author"],
                    "category": r["category"],
                    "tags": r["tags"],
                    "downloads": r["downloads"],
                    "rating": r["rating"],
                    "installed": r["plugin_id"] in self._plugins,
                }
                for r in results
            ],
        }


# =====================================================================
# CLI Parser
# =====================================================================


def register_plugin_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register plugin subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_plugin = subparsers.add_parser("plugin", help="Plugin management")
    plugin_sub = p_plugin.add_subparsers(dest="plugin_command", required=True)

    # plugin list
    p_list = plugin_sub.add_parser("list", help="List installed plugins")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--category", help="Filter by category")
    p_list.add_argument("--limit", type=int, default=20, help="Result limit")
    p_list.add_argument("--offset", type=int, default=0, help="Pagination offset")

    # plugin info
    p_info = plugin_sub.add_parser("info", help="Get plugin details")
    p_info.add_argument("plugin_id", help="Plugin identifier")

    # plugin install
    p_install = plugin_sub.add_parser("install", help="Install a plugin")
    p_install.add_argument("plugin_id", help="Plugin identifier")
    p_install.add_argument("--version", help="Specific version to install")

    # plugin enable
    p_enable = plugin_sub.add_parser("enable", help="Enable a plugin")
    p_enable.add_argument("plugin_id", help="Plugin identifier")

    # plugin disable
    p_disable = plugin_sub.add_parser("disable", help="Disable a plugin")
    p_disable.add_argument("plugin_id", help="Plugin identifier")

    # plugin upgrade
    p_upgrade = plugin_sub.add_parser("upgrade", help="Upgrade a plugin")
    p_upgrade.add_argument("plugin_id", help="Plugin identifier")
    p_upgrade.add_argument("--version", help="Target version (default: latest)")

    # plugin uninstall
    p_uninstall = plugin_sub.add_parser("uninstall", help="Uninstall a plugin")
    p_uninstall.add_argument("plugin_id", help="Plugin identifier")
    p_uninstall.add_argument("--force", action="store_true", help="Force uninstall")

    # plugin search
    p_search = plugin_sub.add_parser("search", help="Search plugin registry")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--category", help="Filter by category")
    p_search.add_argument("--limit", type=int, default=10, help="Result limit")


# =====================================================================
# Command Handler
# =====================================================================


def handle_plugin_command(
    args: argparse.Namespace, manager: PluginManager
) -> int:
    """Dispatch a plugin CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        manager: PluginManager instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    try:
        if args.plugin_command == "list":
            result = manager.list_plugins(
                status=args.status,
                category=args.category,
                limit=args.limit,
                offset=args.offset,
            )
        elif args.plugin_command == "info":
            result = manager.get_plugin_info(args.plugin_id)
        elif args.plugin_command == "install":
            result = manager.install_plugin(args.plugin_id, version=args.version)
        elif args.plugin_command == "enable":
            result = manager.enable_plugin(args.plugin_id)
        elif args.plugin_command == "disable":
            result = manager.disable_plugin(args.plugin_id)
        elif args.plugin_command == "upgrade":
            result = manager.upgrade_plugin(args.plugin_id, version=args.version)
        elif args.plugin_command == "uninstall":
            result = manager.uninstall_plugin(args.plugin_id)
        elif args.plugin_command == "search":
            result = manager.search_plugins(
                query=args.query,
                category=args.category,
                limit=args.limit,
            )
        else:
            print(f"Unknown plugin command: {args.plugin_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
