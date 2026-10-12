"""Interactive shell / REPL CLI commands for LiuHao AI OS.

Provides an interactive shell with:
- Shell commands for exploring the system
- Command history
- Help system
- Tab completion (when readline is available)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ShellCommand:
    """A command available in the interactive shell."""
    name: str
    description: str
    handler: Callable[[List[str]], Dict[str, Any]]
    aliases: List[str] = field(default_factory=list)
    category: str = "general"


class ShellSession:
    """Interactive shell session for LiuHao AI OS.

    Provides a REPL environment with commands for exploring the system.
    """

    def __init__(self) -> None:
        self._history: List[str] = []
        self._commands: Dict[str, ShellCommand] = {}
        self._running: bool = False
        self._context: Dict[str, Any] = {
            "started_at": datetime.utcnow().isoformat(),
            "commands_executed": 0,
            "variables": {},
        }
        self._register_builtin_commands()

    def _register_builtin_commands(self) -> None:
        """Register all built-in shell commands."""
        commands = [
            ShellCommand(
                name="help",
                description="Show available commands or help for a specific command",
                handler=self._cmd_help,
                aliases=["?", "h"],
                category="general",
            ),
            ShellCommand(
                name="exit",
                description="Exit the shell",
                handler=self._cmd_exit,
                aliases=["quit", "q"],
                category="general",
            ),
            ShellCommand(
                name="history",
                description="Show command history",
                handler=self._cmd_history,
                aliases=["hist"],
                category="general",
            ),
            ShellCommand(
                name="status",
                description="Show shell session status",
                handler=self._cmd_status,
                aliases=["stat"],
                category="general",
            ),
            ShellCommand(
                name="echo",
                description="Echo back arguments",
                handler=self._cmd_echo,
                category="general",
            ),
            ShellCommand(
                name="set",
                description="Set a shell variable",
                handler=self._cmd_set,
                category="general",
            ),
            ShellCommand(
                name="vars",
                description="List all shell variables",
                handler=self._cmd_vars,
                category="general",
            ),
            ShellCommand(
                name="clear",
                description="Clear the screen",
                handler=self._cmd_clear,
                aliases=["cls"],
                category="general",
            ),
            ShellCommand(
                name="time",
                description="Show current time",
                handler=self._cmd_time,
                category="system",
            ),
            ShellCommand(
                name="info",
                description="Show system information",
                handler=self._cmd_info,
                aliases=["about"],
                category="system",
            ),
            ShellCommand(
                name="whoami",
                description="Show current user/session info",
                handler=self._cmd_whoami,
                category="system",
            ),
            ShellCommand(
                name="version",
                description="Show LiuHao AI OS version",
                handler=self._cmd_version,
                aliases=["ver"],
                category="system",
            ),
            ShellCommand(
                name="agent",
                description="Agent management commands (use 'agent help' for subcommands)",
                handler=self._cmd_agent,
                category="management",
            ),
            ShellCommand(
                name="memory",
                description="Memory system commands (use 'memory help' for subcommands)",
                handler=self._cmd_memory,
                category="management",
            ),
            ShellCommand(
                name="health",
                description="Health check commands (use 'health help' for subcommands)",
                handler=self._cmd_health,
                category="system",
            ),
        ]
        for cmd in commands:
            self._commands[cmd.name] = cmd
            for alias in cmd.aliases:
                self._commands[alias] = cmd

    # =================================================================
    # Built-in command handlers
    # =================================================================

    def _cmd_help(self, args: List[str]) -> Dict[str, Any]:
        """Help command handler."""
        if args:
            target = args[0]
            cmd = self._commands.get(target)
            if cmd:
                return {
                    "command": cmd.name,
                    "description": cmd.description,
                    "aliases": cmd.aliases,
                    "category": cmd.category,
                    "usage": f"{cmd.name} [arguments...]",
                }
            return {"error": f"Unknown command: {target}"}

        # Group by category
        by_category: Dict[str, List[str]] = {}
        seen = set()
        for cmd in self._commands.values():
            if cmd.name in seen:
                continue
            seen.add(cmd.name)
            by_category.setdefault(cmd.category, []).append(cmd.name)

        return {
            "available_commands": {
                cat: sorted(cmds) for cat, cmds in sorted(by_category.items())
            },
            "total": len(seen),
            "hint": "Type 'help <command>' for detailed help on a command",
        }

    def _cmd_exit(self, args: List[str]) -> Dict[str, Any]:
        """Exit command handler."""
        self._running = False
        return {"status": "exiting", "commands_executed": self._context["commands_executed"]}

    def _cmd_history(self, args: List[str]) -> Dict[str, Any]:
        """History command handler."""
        limit = int(args[0]) if args else 20
        history = self._history[-limit:]
        return {
            "count": len(history),
            "total": len(self._history),
            "history": [
                {"index": len(self._history) - len(history) + i, "command": cmd}
                for i, cmd in enumerate(history)
            ],
        }

    def _cmd_status(self, args: List[str]) -> Dict[str, Any]:
        """Status command handler."""
        return {
            "session_started": self._context["started_at"],
            "commands_executed": self._context["commands_executed"],
            "history_size": len(self._history),
            "variables_count": len(self._context["variables"]),
            "running": self._running,
        }

    def _cmd_echo(self, args: List[str]) -> Dict[str, Any]:
        """Echo command handler."""
        return {"echo": " ".join(args)}

    def _cmd_set(self, args: List[str]) -> Dict[str, Any]:
        """Set variable command handler."""
        if len(args) < 2:
            return {"error": "Usage: set <name> <value>"}
        name, value = args[0], " ".join(args[1:])
        self._context["variables"][name] = value
        return {"variable": name, "value": value, "status": "set"}

    def _cmd_vars(self, args: List[str]) -> Dict[str, Any]:
        """List variables command handler."""
        return {
            "count": len(self._context["variables"]),
            "variables": self._context["variables"],
        }

    def _cmd_clear(self, args: List[str]) -> Dict[str, Any]:
        """Clear screen command handler."""
        return {"action": "clear_screen"}

    def _cmd_time(self, args: List[str]) -> Dict[str, Any]:
        """Time command handler."""
        now = datetime.utcnow()
        return {
            "utc": now.isoformat(),
            "timestamp": now.timestamp(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
        }

    def _cmd_info(self, args: List[str]) -> Dict[str, Any]:
        """Info command handler."""
        return {
            "system": "LiuHao AI OS",
            "description": "Enterprise-grade AI operating system",
            "version": "1.0.0",
            "architecture": "multi-agent",
            "components": [
                "Agent Runtime",
                "Memory System",
                "Knowledge Base",
                "Audit System",
                "Gateway",
                "Health Monitor",
            ],
        }

    def _cmd_whoami(self, args: List[str]) -> Dict[str, Any]:
        """Whoami command handler."""
        return {
            "user": "shell-user",
            "session_id": f"shell-{id(self)}",
            "role": "interactive",
            "started_at": self._context["started_at"],
        }

    def _cmd_version(self, args: List[str]) -> Dict[str, Any]:
        """Version command handler."""
        return {"product": "LiuHao AI OS", "version": "1.0.0"}

    def _cmd_agent(self, args: List[str]) -> Dict[str, Any]:
        """Agent subcommand handler (placeholder)."""
        if not args or args[0] == "help":
            return {
                "subcommands": ["list", "create", "get", "delete", "run", "status", "stop", "metrics", "logs"],
                "usage": "agent <subcommand> [options...]",
                "note": "Full agent management available via CLI: liuhao-cli agent ...",
            }
        return {"agent_command": args[0], "args": args[1:]}

    def _cmd_memory(self, args: List[str]) -> Dict[str, Any]:
        """Memory subcommand handler (placeholder)."""
        if not args or args[0] == "help":
            return {
                "subcommands": ["add", "get", "search", "delete", "list", "stats", "clear", "export", "import"],
                "usage": "memory <subcommand> [options...]",
                "note": "Full memory management available via CLI: liuhao-cli memory ...",
            }
        return {"memory_command": args[0], "args": args[1:]}

    def _cmd_health(self, args: List[str]) -> Dict[str, Any]:
        """Health subcommand handler (placeholder)."""
        if not args or args[0] == "help":
            return {
                "subcommands": ["check", "status", "components", "history", "liveness", "readiness"],
                "usage": "health <subcommand> [options...]",
                "note": "Full health checks available via CLI: liuhao-cli health ...",
            }
        return {"health_command": args[0], "args": args[1:]}

    # =================================================================
    # Shell execution
    # =================================================================

    def execute(self, command_line: str) -> Dict[str, Any]:
        """Execute a single shell command.

        Args:
            command_line: The full command line string.

        Returns:
            Dict with command result.
        """
        command_line = command_line.strip()
        if not command_line:
            return {}

        self._history.append(command_line)
        self._context["commands_executed"] += 1

        parts = command_line.split()
        cmd_name = parts[0].lower()
        args = parts[1:]

        cmd = self._commands.get(cmd_name)
        if not cmd:
            return {"error": f"Unknown command: {cmd_name}", "hint": "Type 'help' for available commands"}

        try:
            result = cmd.handler(args)
            return result
        except Exception as exc:
            return {"error": f"Command error: {exc}"}

    def run_interactive(self) -> int:
        """Run the interactive shell.

        Returns:
            Exit code.
        """
        self._running = True
        print("LiuHao AI OS Shell")
        print("Type 'help' for available commands, 'exit' to quit.")
        print()

        while self._running:
            try:
                line = input("liuhao> ")
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line.strip():
                continue

            result = self.execute(line)
            if result:
                if "error" in result:
                    print(f"Error: {result['error']}")
                    if "hint" in result:
                        print(f"  {result['hint']}")
                elif result.get("action") == "clear_screen":
                    # Print ANSI clear code
                    print("\033[2J\033[H", end="")
                else:
                    print(json.dumps(result, indent=2, ensure_ascii=False))

        print("Goodbye!")
        return 0


# =====================================================================
# CLI Parser
# =====================================================================


def register_shell_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register shell subcommands under the given argparse subparser.

    Args:
        subparsers: The parent subparsers action to attach to.
    """
    p_shell = subparsers.add_parser("shell", help="Interactive shell / REPL")
    shell_sub = p_shell.add_subparsers(dest="shell_command")

    # shell start (default)
    shell_sub.add_parser("start", help="Start interactive shell")

    # shell exec
    p_exec = shell_sub.add_parser("exec", help="Execute a single shell command")
    p_exec.add_argument("command", help="Command to execute")

    # shell history
    p_hist = shell_sub.add_parser("history", help="Show shell command history")
    p_hist.add_argument("--limit", type=int, default=20, help="History limit")

    # shell help
    p_help = shell_sub.add_parser("help", help="Show shell help")
    p_help.add_argument("command", nargs="?", help="Specific command to get help for")


# =====================================================================
# Command Handler
# =====================================================================


def handle_shell_command(
    args: argparse.Namespace, session: ShellSession
) -> int:
    """Dispatch a shell CLI command to the appropriate handler.

    Args:
        args: Parsed argparse namespace.
        session: ShellSession instance.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    if not args.shell_command or args.shell_command == "start":
        return session.run_interactive()

    try:
        if args.shell_command == "exec":
            result = session.execute(args.command)
        elif args.shell_command == "history":
            result = session._cmd_history([str(args.limit)])
        elif args.shell_command == "help":
            result = session._cmd_help([args.command] if args.command else [])
        else:
            print(f"Unknown shell command: {args.shell_command}")
            return 1

        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, ensure_ascii=False))
        return 1
