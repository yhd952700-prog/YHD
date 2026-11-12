"""Command-line surface for the self-upgrade module.

Examples::

    python -m src.upgrade check --app-root . --release-dir D:\\releases
    python -m src.upgrade check --app-root . --manifest D:\\releases\\LIUHAO_RELEASE.json
    python -m src.upgrade upgrade --app-root . --manifest-url https://example/release-manifest.json
    python -m src.upgrade status --app-root .
    python -m src.upgrade history --app-root . --limit 20
    python -m src.upgrade rollback --app-root .            # 一键回滚到上一版本
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .compat import CompatLevel, assess_rollback
from .dataguard import capture_table_inventory
from .dataversion import target_data_schema
from .downloader import cleanup_download
from .errors import UpgradeError
from .manager import UpgradeConfig, UpgradeManager
from .models import CheckStatus
from .selfcheck import SelfCheckConfig, run_self_check
from .sources import build_source
from .startup_probe import StartupCheckConfig


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.upgrade",
        description="贾维斯自我升级：检查、下载校验、备份应用、一键回滚",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--app-root", default=".", help="应用根目录（默认当前目录）")
    common.add_argument(
        "--data-dir",
        default=None,
        help=(
            "用户数据目录（默认按安装器规则解析：安装版 %%LOCALAPPDATA%%\\LiuHao\\data，"
            "开发/便携版 <app-root>/data；LIUHAO_DATA_DIR 可覆盖）"
        ),
    )
    common.add_argument(
        "--backup-root",
        default=None,
        help=(
            "备份目录（默认放在数据目录之外：优先另一块固定盘 <X>:\\LiuHaoBackups，"
            "其次 %%LOCALAPPDATA%%\\LiuHao\\backups；便携/开发形态为 "
            "<数据目录>/upgrade-backups）"
        ),
    )
    common.add_argument(
        "--state-dir",
        default=None,
        help="状态目录（默认 <数据目录>/upgrade-state，不写程序目录）",
    )
    common.add_argument(
        "--extra-state",
        action="append",
        default=[],
        metavar="REL_PATH",
        help="额外需要备份/预检的状态文件（相对 app-root，可重复）",
    )
    common.add_argument("--json", action="store_true", help="以 JSON 输出")

    source = argparse.ArgumentParser(add_help=False)
    group = source.add_mutually_exclusive_group()
    group.add_argument(
        "--release-dir",
        default=None,
        help="本地发布目录（含 LIUHAO_RELEASE.json / release-manifest.json 与发布包）",
    )
    group.add_argument(
        "--manifest",
        dest="manifest_path",
        default=None,
        metavar="PATH",
        help="本地发布清单文件路径（发布包与清单同目录）",
    )
    group.add_argument("--manifest-url", default=None, help="HTTP(S) 发布清单地址")
    source.add_argument("--manifest-sha256", default=None, help="固定发布清单的 SHA256（可选）")
    source.add_argument("--timeout", type=float, default=5.0, help="网络超时秒数")
    source.add_argument("--public-key", default=None, help="Ed25519 公钥（PEM 文件路径或 base64）")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", parents=[common, source], help="检查更新")
    p_upgrade = sub.add_parser("upgrade", parents=[common, source], help="检查→下载→校验→应用")
    p_upgrade.add_argument(
        "--allow-same-or-downgrade",
        action="store_true",
        help="允许安装相同或更低版本（用于重新下载旧版本安装包后恢复）",
    )
    p_upgrade.add_argument(
        "--allow-data-downgrade",
        action="store_true",
        help=(
            "数据已由更高 schema 版本写入时仍继续安装旧版本。"
            "默认拒绝，避免旧代码静默读取新数据；使用前请先备份数据目录。"
        ),
    )
    sub.add_parser("status", parents=[common], help="查看当前升级状态")
    p_history = sub.add_parser("history", parents=[common], help="查看升级记录")
    p_history.add_argument("--limit", type=int, default=20)
    p_rollback = sub.add_parser("rollback", parents=[common], help="回滚到升级前版本")
    p_rollback.add_argument("--backup-id", default=None, help="指定备份 ID")
    p_rollback.add_argument("--record-id", default=None, help="指定升级记录 ID")
    p_rollback.add_argument(
        "--dry-run",
        action="store_true",
        help="只输出数据形态兼容判定（direct/downgrade/refuse），不改动任何文件",
    )
    p_snapshots = sub.add_parser("snapshots", parents=[common], help="列出升级前数据快照")
    p_snapshots.add_argument("--limit", type=int, default=20)
    p_export = sub.add_parser("export-snapshot", parents=[common], help="单独取出升级前数据快照")
    p_export.add_argument("--dest", required=True, help="导出目标目录（必须不存在）")
    p_export.add_argument("--backup-id", default=None, help="指定备份 ID（默认最近一次成功升级）")
    p_export.add_argument("--record-id", default=None, help="指定升级记录 ID")
    p_selfcheck = sub.add_parser("selfcheck", parents=[common], help="对当前安装执行自检")
    p_selfcheck.add_argument("--service-module", default="src.gateway.main")
    p_selfcheck.add_argument("--service-attr", default="app")
    p_selfcheck.add_argument("--selfcheck-timeout", type=float, default=90.0, help="启动就绪超时秒数")
    p_selfcheck.add_argument(
        "--health-path",
        action="append",
        default=[],
        help=(
            "健康检查路径（可重复；默认依次重试 /v1/health、/health、/healthz；"
            "仅当这些路径明确 404 时才以 /openapi.json 兜底并标记为 fallback）"
        ),
    )
    p_selfcheck.add_argument(
        "--startup-port",
        type=int,
        default=None,
        help="诊断用：固定启动端口（默认自动挑选空闲临时端口）",
    )
    p_selfcheck.add_argument("--no-service", action="store_true", help="只检查数据库，不启动服务")
    sub.add_parser("last-check", parents=[common], help="查看上次检查更新的时间与结果")
    sub.add_parser(
        "data-version", parents=[common], help="查看数据 schema 版本与当前代码是否兼容"
    )
    p_keycompat = sub.add_parser(
        "key-compat",
        parents=[common],
        help="检查身份签名密钥格式；回滚到旧版前可用 --downgrade 降级",
    )
    p_keycompat.add_argument(
        "--downgrade",
        action="store_true",
        help="把机器绑定包裹格式降级为旧版可读的明文（会以明文落盘，事后请尽快重新升级）",
    )
    p_keycompat.add_argument(
        "--target-version",
        default=None,
        help="回滚目标版本（默认当前安装版本；低于 0.1.2 才需要降级）",
    )
    return parser


def _load_public_key(value: Optional[str]) -> Optional[str | bytes]:
    if not value:
        return None
    path = Path(value)
    if path.is_file():
        return path.read_bytes()
    return value


def _manager(args: argparse.Namespace) -> UpgradeManager:
    source = build_source(
        release_dir=getattr(args, "release_dir", None),
        manifest_path=getattr(args, "manifest_path", None),
        manifest_url=getattr(args, "manifest_url", None),
        timeout=getattr(args, "timeout", 5.0),
        manifest_sha256=getattr(args, "manifest_sha256", None),
    )
    config = UpgradeConfig(
        app_root=args.app_root,
        data_dir=args.data_dir,
        backup_root=args.backup_root,
        state_dir=args.state_dir,
        extra_state_files=tuple(args.extra_state or ()),
        source=source,
        public_key=_load_public_key(getattr(args, "public_key", None)),
        allow_same_or_downgrade=bool(
            getattr(args, "allow_same_or_downgrade", False)
        ),
        allow_data_downgrade=bool(
            getattr(args, "allow_data_downgrade", False)
        ),
    )
    return UpgradeManager(config)


def _emit(args: argparse.Namespace, payload: Dict[str, Any]) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for key, value in payload.items():
        if isinstance(value, (dict, list)):
            print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        else:
            print(f"{key}: {value}")


def _print_progress(message: str) -> None:
    print(f"[升级状态] {message}", flush=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    # 升级命令是低频写盘入口：顺手清理上次进程被强杀留下的自产临时产物
    # （与网关启动共用同一套有界清扫，不复制判据）。
    from src.common.temp_sweep import run_startup_temp_sweep

    run_startup_temp_sweep()
    try:
        if args.command == "status":
            manager = _manager(args)
            _emit(args, manager.status())
            return 0

        if args.command == "history":
            manager = _manager(args)
            _emit(args, {"records": manager.history(limit=args.limit)})
            return 0

        if args.command == "snapshots":
            manager = _manager(args)
            _emit(args, {"backups": manager.list_backups(limit=args.limit)})
            return 0

        if args.command == "export-snapshot":
            manager = _manager(args)
            exported = manager.export_snapshot(
                args.dest, backup_id=args.backup_id, record_id=args.record_id
            )
            _emit(args, exported)
            return 0

        if args.command == "selfcheck":
            manager = _manager(args)
            paths = manager.data_paths()
            expected = capture_table_inventory(paths)
            startup_config = StartupCheckConfig(
                port=args.startup_port,
                startup_timeout_seconds=args.selfcheck_timeout,
            )
            if args.health_path:
                startup_config.health_paths = tuple(args.health_path)
            report = run_self_check(
                manager.config.app_root,
                paths,
                expected_tables=expected,
                config=SelfCheckConfig(
                    service_module=args.service_module,
                    service_attr=args.service_attr,
                    check_service=not args.no_service,
                    startup=startup_config,
                ),
            )
            _emit(args, report.to_dict())
            return 0 if report.ok else 1

        if args.command == "last-check":
            manager = _manager(args)
            state = manager.last_check()
            if not state:
                state = {"status": "never", "message": "尚未执行过检查更新"}
            _emit(args, state)
            return 0

        if args.command == "data-version":
            manager = _manager(args)
            payload = manager.data_schema_status()
            _emit(args, payload)
            return 0 if payload["compatible"] else 3

        if args.command == "key-compat":
            from .keyformat import (
                assess_key_visibility,
                downgrade_key_for_legacy,
                key_format_needs_downgrade,
            )

            manager = _manager(args)
            data_dir = Path(manager.config.data_dir)
            target = args.target_version
            if target is None:
                try:
                    target, _ = manager.current_version()
                except Exception:  # noqa: BLE001 - 读不到版本时按"最保守"处理
                    target = ""
            before = assess_key_visibility(data_dir)
            result: Dict[str, Any] = {"before": before, "target_version": target}
            if args.downgrade and key_format_needs_downgrade(before, target):
                result["downgrade"] = downgrade_key_for_legacy(
                    data_dir, app_version=str(target or "")
                )
                result["after"] = assess_key_visibility(data_dir)
            elif args.downgrade:
                result["downgrade"] = {
                    "changed": False,
                    "reason": "当前格式对目标版本可读，无需降级",
                }
            _emit(args, result)
            effective = result.get("after", before)
            lock_out = bool(effective.get("legacy_would_lock_out"))
            return 4 if lock_out else 0

        if args.command == "rollback":
            manager = _manager(args)
            if getattr(args, "dry_run", False):
                backup_id = manager._resolve_backup_id(
                    args.backup_id, args.record_id
                )
                meta = manager._read_backup(backup_id)
                current, _ = manager.current_version()
                target = str(meta.get("from_version") or "") or current
                report = assess_rollback(
                    manager.config.data_dir,
                    target_version=target,
                    target_schema=target_data_schema(
                        None, release_version=target, current_version=current
                    ),
                    backup_dir=manager.config.backup_root / backup_id,
                )
                _emit(args, report.to_dict())
                if not args.json:
                    print(report.to_text())
                return 5 if report.level is CompatLevel.REFUSE else 0
            result = manager.rollback(
                backup_id=args.backup_id, record_id=args.record_id
            )
            _emit(args, result.to_dict())
            return 0

        manager = _manager(args)
        check = manager.check()
        if not args.json:
            _print_progress(check.message)
        if args.command == "check":
            _emit(args, check.to_dict())
            return 0 if check.status in (
                CheckStatus.UPDATE_AVAILABLE,
                CheckStatus.UP_TO_DATE,
            ) else 2
        if check.status is not CheckStatus.UPDATE_AVAILABLE or not check.release:
            _emit(args, check.to_dict())
            return 0 if check.status is CheckStatus.UP_TO_DATE else 2
        download = manager.download(check.release)
        if not args.json:
            _print_progress(download.signature_note)
        try:
            result = manager.apply(download.path, check.release)
        finally:
            cleanup_download(download)
        _emit(args, result.to_dict())
        return 0
    except UpgradeError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
