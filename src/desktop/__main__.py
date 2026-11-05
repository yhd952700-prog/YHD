"""桌面层命令行入口。

    python -m src.desktop                      # 默认：状态窗口 + 托盘 + 启动网关
    python -m src.desktop app --hidden         # 只驻留托盘（自启时使用）
    python -m src.desktop about [--json]       # 关于页数据源
    python -m src.desktop autostart status|enable|disable
    python -m src.desktop diagnose [--out DIR] [--verify]
    python -m src.desktop firewall [--port 8080]
    python -m src.desktop status [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import REPO_ROOT
from .probe import discover_service, http_json


def _add_app_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="0.0.0.0", help="网关绑定地址（默认 0.0.0.0，手机可访问）")
    parser.add_argument("--port", type=int, default=None, help="网关端口（默认 8080，占用时自动顺延）")
    parser.add_argument("--open-browser", action="store_true", help="启动后打开浏览器（自启场景不要加）")
    parser.add_argument("--hidden", action="store_true", help="启动后只驻留托盘，不显示窗口")
    parser.add_argument("--autostart", action="store_true", help="自启模式：等价于 --hidden 且不打开浏览器")


def build_parser() -> argparse.ArgumentParser:
    app_args = argparse.ArgumentParser(add_help=False)
    _add_app_args(app_args)

    parser = argparse.ArgumentParser(
        prog="python -m src.desktop",
        description="鎏灏 Windows 桌面集成（托盘 / 自启 / 防火墙提示 / 诊断包 / 关于页数据源）",
        parents=[app_args],
    )
    sub = parser.add_subparsers(dest="command")

    p_app = sub.add_parser("app", help="启动状态窗口 + 系统托盘 + 网关子进程", parents=[app_args])
    p_app.set_defaults(command="app")

    p_about = sub.add_parser("about", help="输出版本 / 构建时间 / 数据目录（关于页数据源）")
    p_about.add_argument("--json", action="store_true", help="输出机器可读 JSON（默认即 JSON，保留兼容）")

    p_auto = sub.add_parser("autostart", help="开机自启登记（仅 HKCU，默认关闭）")
    p_auto.add_argument("action", choices=("status", "enable", "disable"))
    p_auto.add_argument("--open-browser", action="store_true", help="自启时也自动打开浏览器（默认不打开）")
    p_auto.add_argument("--value-name", default=None, help="登记值名（测试或自定义用，默认 LiuHaoAIOS）")

    p_diag = sub.add_parser("diagnose", help="生成脱敏诊断包 zip")
    p_diag.add_argument("--out", default=None, help="输出目录或 zip 路径（默认 %%LOCALAPPDATA%%\\LiuHao\\diagnostics）")
    p_diag.add_argument("--base-url", default=None, help="网关地址；省略时自动探测 8080-8089")
    p_diag.add_argument("--verify", action="store_true", help="生成后打印自检结果")

    p_fw = sub.add_parser("firewall", help="只读检测防火墙 / 局域网可达性")
    p_fw.add_argument("--port", type=int, default=None, help="要检查的端口（通常是网关端口）")

    p_status = sub.add_parser("status", help="读取 /v1/health 与 /v1/ready 快照")
    p_status.add_argument("--base-url", default=None, help="网关地址；省略时自动探测")

    p_help = sub.add_parser("help", help="显示帮助")
    p_help.set_defaults(command="help")
    return parser


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _cmd_autostart(args: argparse.Namespace) -> int:
    from . import autostart

    value_name = args.value_name or autostart.VALUE_NAME
    if not autostart.supported():
        _emit({"supported": False, "error": "开机自启仅支持 Windows（HKCU Run）"})
        return 2
    if args.action == "enable":
        result = autostart.enable(REPO_ROOT, open_browser=args.open_browser, value_name=value_name)
    elif args.action == "disable":
        result = autostart.disable(value_name)
    else:
        result = autostart.read(value_name)
    _emit(result)
    return 0


def _cmd_about(_args: argparse.Namespace) -> int:
    from .about import collect_about

    _emit(collect_about())
    return 0


def _cmd_diagnose(args: argparse.Namespace) -> int:
    from .diagnostics import create_bundle, verify_bundle

    try:
        bundle = create_bundle(args.out, base_url=args.base_url)
    except RuntimeError as exc:
        print(f"诊断包生成失败：{exc}", file=sys.stderr)
        return 1
    print(str(bundle))
    if args.verify:
        violations = verify_bundle(bundle)
        _emit({"bundle": str(bundle), "verified": not violations, "violations": violations})
    return 0


def _cmd_firewall(args: argparse.Namespace) -> int:
    from .firewall import inspect_firewall

    _emit(inspect_firewall(args.port))
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    base = args.base_url or discover_service()
    if not base:
        _emit({"reachable": False, "note": "未发现正在运行的网关（已尝试 8080-8089）"})
        return 1
    _emit(
        {
            "base_url": base,
            "health": http_json(f"{base}/v1/health"),
            "ready": http_json(f"{base}/v1/ready"),
        }
    )
    return 0


def _cmd_app(args: argparse.Namespace) -> int:
    from .app import DesktopApp

    hidden = bool(args.hidden or args.autostart)
    app = DesktopApp(
        host=args.host,
        port=args.port,
        open_browser=bool(args.open_browser and not args.autostart),
        hidden=hidden,
    )
    return app.run()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "app"
    if command == "help":
        parser.print_help()
        return 0
    if command == "app":
        return _cmd_app(args)
    if command == "about":
        return _cmd_about(args)
    if command == "autostart":
        return _cmd_autostart(args)
    if command == "diagnose":
        return _cmd_diagnose(args)
    if command == "firewall":
        return _cmd_firewall(args)
    if command == "status":
        return _cmd_status(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
