#!/usr/bin/env python3
"""日志/诊断包/审计库的明文凭据扫描（可重复验收，非单测）。

为什么单独有这个脚本
--------------------
2026-11-08 的泄漏审计里，"跑测试全绿"并不能证明落盘证据干净：真正要回答的是
"用户把 logs 目录和诊断包转发出去时，里面还有没有完整 API Key"。这个脚本就是
把那一次人工 grep 固化成可重复验收，并且自带**负向对照**——避免"永远输出 0"
的假绿。

扫描对象
--------
* ``liuhao.log`` / ``error.log`` / ``desktop-gateway.log``（含 ``.1`` 等轮转备份）；
* 诊断包 ``*.zip``（逐个成员读取，白名单外也一样扫）；
* ``audit_store.db`` 的 ``audit_events.details``（审计库是证据库，不能藏明文）。

判定方式
--------
正向匹配"明文凭据特征"；已经脱敏成 ``***REDACTED***`` 的占位符不算命中。
与 ``src.common.redaction.leak_violations`` 刻意不同源：这里是一份独立实现，
用来给"自检与脱敏共漏"提供第二意见。

用法
----
    .venv\\Scripts\\python.exe scripts\\acceptance\\log_leak_scan.py
    .venv\\Scripts\\python.exe scripts\\acceptance\\log_leak_scan.py \\
        --data-dir D:\\_audit\\log-leak-fix --log-dir D:\\_audit\\log-leak-fix\\logs

    # 负向对照：必须报命中并给出 EXIT=1
    .venv\\Scripts\\python.exe scripts\\acceptance\\log_leak_scan.py --self-test

退出码
------
* 0：扫到了至少一个目标，且没有明文命中；
* 1：存在明文命中；
* 2：没有可扫描目标（拿不到证据时不能假装干净）或参数/环境错误。

约束：只读；不修改 ``src/``，不碰 ``dist\\``，不运行安装包。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

# 允许从仓库任意目录直接运行：脚本目录不是仓库根，``src`` 不在 sys.path 上。
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 规则表与诊断包导出闸共用同一份实现（src/common/leak_scan.py），避免"验收
# 是一套、导出是另一套"。脚本只保留文件/zip/sqlite 的遍历与 CLI。
from src.common.leak_scan import format_hits, scan_text  # noqa: E402

#: 只扫这三类应用日志（与用户实际会转发的文件一致）。
LOG_NAMES = ("liuhao.log", "error.log", "desktop-gateway.log")

#: 轮转备份命名：``liuhao.log.1`` / ``error.log.3``。
_BACKUP_SUFFIX = re.compile(r"^\.\d+$")


@dataclass
class Target:
    """一个待扫描对象。"""

    path: Path
    kind: str  # log | bundle | sqlite | extra


@dataclass
class Result:
    target: Target
    hits: dict[str, int] = field(default_factory=dict)
    note: str = ""
    error: str = ""

    @property
    def total(self) -> int:
        return sum(self.hits.values())


# --------------------------------------------------------------------------- #
# 目标发现
# --------------------------------------------------------------------------- #


def _default_data_dir() -> Path:
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return (Path(base) / "LiuHao" / "data").resolve()


def _diagnostics_dirs() -> list[Path]:
    dirs: list[Path] = []
    override = (os.environ.get("LIUHAO_DIAGNOSTICS_DIR") or "").strip()
    if override:
        dirs.append(Path(override).expanduser())
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        dirs.append(Path(local) / "LiuHao" / "diagnostics")
    dirs.append(Path.home() / "LiuHao" / "diagnostics")
    return dirs


def _log_files_in(directory: Path) -> Iterable[Path]:
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return []
    result: list[Path] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        for name in LOG_NAMES:
            if entry.name == name or (
                entry.name.startswith(name)
                and _BACKUP_SUFFIX.match(entry.name[len(name):])
            ):
                result.append(entry)
                break
    return result


def _dedupe(targets: Iterable[Target]) -> list[Target]:
    seen: set[str] = set()
    ordered: list[Target] = []
    for target in targets:
        key = str(target.path.resolve()) if target.path.exists() else str(target.path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(target)
    return ordered


def collect_targets(
    *,
    data_dir: Path | None,
    log_dirs: list[Path],
    bundle_paths: list[Path],
    bundle_dirs: list[Path],
    extras: list[Path],
    include_repo_logs: bool,
) -> list[Target]:
    targets: list[Target] = []

    for directory in log_dirs:
        targets.extend(Target(path, "log") for path in _log_files_in(directory))

    if include_repo_logs:
        repo_logs = Path(__file__).resolve().parents[2] / "logs"
        targets.extend(Target(path, "log") for path in _log_files_in(repo_logs))

    if data_dir is not None:
        targets.extend(
            Target(path, "log") for path in _log_files_in(data_dir / "logs")
        )
        audit_db = data_dir / "audit_store.db"
        if audit_db.is_file():
            targets.append(Target(audit_db, "sqlite"))
        # 数据目录根下的诊断包（部分构建直接落在 data/）。
        targets.extend(
            Target(path, "bundle") for path in sorted(data_dir.glob("*.zip"))
        )

    for directory in bundle_dirs:
        targets.extend(
            Target(path, "bundle") for path in sorted(directory.glob("*.zip"))
        )

    targets.extend(Target(path, "bundle") for path in bundle_paths)

    for extra in extras:
        if extra.is_dir():
            targets.extend(Target(path, "log") for path in _log_files_in(extra))
            targets.extend(
                Target(path, "bundle") for path in sorted(extra.glob("*.zip"))
            )
            audit_db = extra / "audit_store.db"
            if audit_db.is_file():
                targets.append(Target(audit_db, "sqlite"))
        elif extra.is_file():
            suffix = extra.suffix.lower()
            if suffix == ".zip":
                kind = "bundle"
            elif suffix in (".db", ".sqlite", ".sqlite3"):
                kind = "sqlite"
            else:
                kind = "extra"
            targets.append(Target(extra, kind))

    return _dedupe(targets)


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def _zip_text(path: Path) -> tuple[str, list[str], str]:
    chunks: list[str] = []
    members: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            members.append(name)
            with archive.open(name) as handle:
                chunks.append(handle.read().decode("utf-8", errors="replace"))
    return "\n".join(chunks), members, ""


def _sqlite_text(path: Path) -> tuple[str, str]:
    """读审计库的 ``details`` 列；返回 (文本, 备注/错误)。"""
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return "", f"打开失败：{exc}"
    try:
        rows = connection.execute("SELECT details FROM audit_events").fetchall()
    except sqlite3.Error as exc:
        return "", f"无 audit_events.details：{exc}"
    finally:
        connection.close()
    text = "\n".join("" if row[0] is None else str(row[0]) for row in rows)
    return text, f"events={len(rows)}"


def _match(text: str) -> dict[str, int]:
    """委托给共享规则表；脚本不再维护第二份特征。"""
    return scan_text(text)


def scan_target(target: Target) -> Result:
    result = Result(target=target)
    try:
        if target.kind == "bundle":
            text, members, note = _zip_text(target.path)
            result.note = note or f"members={len(members)}"
        elif target.kind == "sqlite":
            text, note = _sqlite_text(target.path)
            result.note = note
        else:
            text = _read_text(target.path)
    except (OSError, zipfile.BadZipFile, ValueError) as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.hits = _match(text)
    return result


def scan_targets(targets: list[Target]) -> list[Result]:
    return [scan_target(target) for target in targets]


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #


def _print_report(results: list[Result], *, as_json: bool) -> int:
    total = sum(result.total for result in results)
    if as_json:
        payload = {
            "files": [
                {
                    "path": str(result.target.path),
                    "kind": result.target.kind,
                    "hits": result.total,
                    "by_pattern": result.hits,
                    "note": result.note,
                    "error": result.error,
                }
                for result in results
            ],
            "total_plaintext_hits": total,
            "exit_code": 1 if total else 0,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if total else 0

    print("-" * 72)
    for result in results:
        if result.error:
            print(f"[ERR ] {result.target.path}  {result.error}")
            continue
        status = "LEAK" if result.total else "OK  "
        note = f"  ({result.note})" if result.note else ""
        print(f"[{status}] {result.target.path}  hits={result.total}{note}")
        for label, count in sorted(result.hits.items(), key=lambda item: -item[1]):
            print(f"         - {label}: {count}")
    print("-" * 72)
    print(f"files_scanned={len(results)}")
    print(f"TOTAL_PLAINTEXT_HITS {total}")
    exit_code = 1 if total else 0
    print(f"EXIT={exit_code}")
    return exit_code


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def _self_test(as_json: bool = False) -> int:
    """两条对照，证明扫描既不会永远报 0，也不会被占位符骗过。

    1. 负向对照：一份含明文 ``?key=`` / ``Bearer sk-`` 的假日志，必须报命中且
       ``EXIT=1``——否则规则表坏掉时验收会假绿；
    2. 半截残留：``Bearer sk-***REDACTED***abc123`` 这种"占位符后面还挂着
       尾巴"的样本，必须报命中——否则"脱敏不彻底"会被 ``***`` 骗过去。
    """
    with tempfile.TemporaryDirectory(prefix="log-leak-control-") as tmp:
        control = Path(tmp) / "negative-control.log"
        control.write_text(
            "2026-11-08 12:00:00 | INFO | uvicorn.access | "
            '"GET /v1/health?key=ABCDEF HTTP/1.1" 200\n'
            "openai.AuthenticationError: Incorrect API key provided: "
            "Authorization: Bearer sk-xxx1234567890abcdef\n",
            encoding="utf-8",
        )
        partial = Path(tmp) / "half-redacted.log"
        partial.write_text(
            "2026-11-08 12:00:01 | WARNING | upstream | "
            "Authorization: Bearer sk-***REDACTED***abc123\n"
            '"key": "***REDACTED***tail456"\n',
            encoding="utf-8",
        )

        control_results = scan_targets([Target(control, "extra")])
        partial_results = scan_targets([Target(partial, "extra")])
        control_code = _print_report(control_results, as_json=as_json)
        partial_code = _print_report(partial_results, as_json=as_json)

        control_hits = sum(result.total for result in control_results)
        partial_hits = sum(result.total for result in partial_results)
        ok = control_hits >= 2 and control_code == 1 and partial_hits >= 1 and partial_code == 1
        if not as_json:
            print(
                f"NEGATIVE_CONTROL hits={control_hits} "
                f"detail={format_hits(control_results[0].hits)} EXIT={control_code} "
                f"-> {'PASS' if control_code == 1 else 'FAIL'}"
            )
            print(
                f"PARTIAL_RESIDUAL hits={partial_hits} "
                f"detail={format_hits(partial_results[0].hits)} EXIT={partial_code} "
                f"-> {'PASS' if partial_code == 1 else 'FAIL'}"
            )
            print("SELF-TEST", "PASS" if ok else "FAIL")
        return 0 if ok else 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描日志 / 诊断包 / 审计库里的明文凭据（只读）。"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="数据目录（默认 LIUHAO_DATA_DIR 或 %%LOCALAPPDATA%%\\LiuHao\\data）。",
    )
    parser.add_argument(
        "--no-data-dir",
        action="store_true",
        help="不扫默认数据目录（发布闸用：只扫仓库 logs\\ 与显式 --log-dir/--extra）。",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        action="append",
        default=[],
        help="额外日志目录，可重复；默认还会扫 <data-dir>\\logs 与仓库 logs\\。",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        action="append",
        default=[],
        help="显式诊断包 zip，可重复。",
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        action="append",
        default=[],
        help="诊断包所在目录（默认 %%LOCALAPPDATA%%\\LiuHao\\diagnostics）。",
    )
    parser.add_argument(
        "--extra",
        type=Path,
        action="append",
        default=[],
        help="额外文件/目录（负向对照也用它指定假日志）。",
    )
    parser.add_argument(
        "--no-repo-logs",
        action="store_true",
        help="不扫仓库 logs\\（默认扫描，便于发现本地启动器留下的抓取文件）。",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON。")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="跑负向对照：造明文日志，要求报命中且 EXIT=1。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.self_test:
        return _self_test(as_json=args.json)

    data_dir = None
    if not args.no_data_dir:
        data_dir = (args.data_dir or _default_data_dir()).expanduser()
    log_dirs = [path.expanduser() for path in args.log_dir]
    bundle_dirs = [path.expanduser() for path in args.bundle_dir]
    if not bundle_dirs and not args.no_data_dir:
        bundle_dirs = [path for path in _diagnostics_dirs() if path.is_dir()]

    targets = collect_targets(
        data_dir=data_dir,
        log_dirs=log_dirs,
        bundle_paths=[path.expanduser() for path in args.bundle],
        bundle_dirs=bundle_dirs,
        extras=[path.expanduser() for path in args.extra],
        include_repo_logs=not args.no_repo_logs,
    )

    if not args.json:
        if data_dir is not None:
            print(f"[log-leak-scan] data_dir={data_dir}")
        for directory in log_dirs:
            print(f"[log-leak-scan] log_dir={directory}")
        for directory in bundle_dirs:
            print(f"[log-leak-scan] bundle_dir={directory}")

    if not targets:
        print("NO_TARGETS_SCANNED -- 没找到任何日志/诊断包/审计库，不能据此判定干净")
        print("EXIT=2")
        return 2

    results = scan_targets(targets)
    return _print_report(results, as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
