"""Command-line surface for the self-upgrade module.

Examples::

    python -m src.upgrade check --app-root . --release-dir D:\\releases
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

from .downloader import cleanup_download
from .errors import UpgradeError
from .manager import UpgradeConfig, UpgradeManager
from .models import CheckStatus
from .sources import build_source


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.upgrade",
        description="鎏灏自我升级：检查、下载校验、备份应用、一键回滚",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--app-root", default=".", help="应用根目录（默认当前目录）")
    common.add_argument("--data-dir", default=None, help="用户数据目录（默认 <app-root>/data）")
    common.add_argument("--backup-root", default=None, help="备份目录（默认 <app-root>/upgrade-backups）")
    common.add_argument("--state-dir", default=None, help="状态目录（默认 <app-root>/upgrade-state）")
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
    group.add_argument("--release-dir", default=None, help="本地发布目录（含 release-manifest.json）")
    group.add_argument("--manifest-url", default=None, help="HTTP(S) 发布清单地址")
    source.add_argument("--manifest-sha256", default=None, help="固定发布清单的 SHA256（可选）")
    source.add_argument("--timeout", type=float, default=5.0, help="网络超时秒数")
    source.add_argument("--public-key", default=None, help="Ed25519 公钥（PEM 文件路径或 base64）")

    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", parents=[common, source], help="检查更新")
    p_upgrade = sub.add_parser("upgrade", parents=[common, source], help="检查→下载→校验→应用")
    sub.add_parser("status", parents=[common], help="查看当前升级状态")
    p_history = sub.add_parser("history", parents=[common], help="查看升级记录")
    p_history.add_argument("--limit", type=int, default=20)
    p_rollback = sub.add_parser("rollback", parents=[common], help="回滚到升级前版本")
    p_rollback.add_argument("--backup-id", default=None, help="指定备份 ID")
    p_rollback.add_argument("--record-id", default=None, help="指定升级记录 ID")
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
        release_dir=args.release_dir,
        manifest_url=args.manifest_url,
        timeout=args.timeout,
        manifest_sha256=args.manifest_sha256,
    )
    config = UpgradeConfig(
        app_root=args.app_root,
        data_dir=args.data_dir,
        backup_root=args.backup_root,
        state_dir=args.state_dir,
        extra_state_files=tuple(args.extra_state or ()),
        source=source,
        public_key=_load_public_key(args.public_key),
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
    try:
        if args.command == "status":
            manager = _manager(args)
            _emit(args, manager.status())
            return 0

        if args.command == "history":
            manager = _manager(args)
            _emit(args, {"records": manager.history(limit=args.limit)})
            return 0

        if args.command == "rollback":
            manager = _manager(args)
            result = manager.rollback(
                backup_id=args.backup_id, record_id=args.record_id
            )
            _emit(args, result.to_dict())
            return 0

        manager = _manager(args)
        check = manager.check()
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
