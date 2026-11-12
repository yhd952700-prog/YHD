#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Delete smoke/backup test copies left under ``%LOCALAPPDATA%\\LiuHao``.

Why this exists
---------------
验收/冒烟测试会整目录复制一份数据（``data.after-smoke-*`` / ``data.bak-*``），
复制品里可能同时含 ``auth_secrets.json``、``human_identities.json`` 与
``model_config.key``。这些副本不在产品数据布局内，也不受备份/清理功能管理：
谁拿到磁盘，谁就能拿到凭据。审计（docs/audit-data-layout-20261108.md §5）要求
一次性清掉现存残留，并把「测试后自动清理」变成兜底（见 tests/conftest.py）。

Safety rules (deliberately conservative)
----------------------------------------
* Only directories whose name starts with ``data.after-smoke-`` or ``data.bak-``
  are candidates. The real data dir (``data``), ``logs``, ``diagnostics`` and
  anything else are never touched.
* Nothing is deleted without an explicit confirmation: the script prints every
  path (plus which sensitive files it contains) and requires typing ``YES``.
  ``--yes`` skips the prompt for unattended use, but the listing is still
  printed and the flag is explicit.
* ``--dry-run`` only lists. It never deletes.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Sequence

CANDIDATE_PREFIXES = ("data.after-smoke-", "data.bak-")
SENSITIVE_NAMES = {
    "app.env",
    "auth_secrets.json",
    "human_identities.json",
    "integrity.key",
    "model_config.key",
    "model_config_key.bin",
}


def _default_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise SystemExit("LOCALAPPDATA is not set; pass --root explicitly")
    return Path(base) / "LiuHao"


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _sensitive_files(path: Path) -> List[str]:
    found: List[str] = []
    for item in path.rglob("*"):
        try:
            if item.is_file() and item.name.lower() in SENSITIVE_NAMES:
                found.append(item.relative_to(path).as_posix())
        except OSError:
            continue
    return sorted(found)


def find_candidates(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    candidates = [
        entry
        for entry in root.iterdir()
        if entry.is_dir() and entry.name.startswith(CANDIDATE_PREFIXES)
    ]
    return sorted(candidates, key=lambda p: p.name)


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _describe(candidate: Path) -> str:
    sensitive = _sensitive_files(candidate)
    marker = "SENSITIVE" if sensitive else "no credentials"
    detail = ", ".join(sensitive) if sensitive else "-"
    return (
        f"  {candidate}\n"
        f"      {_human_size(_dir_size(candidate))}  [{marker}]  {detail}"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Delete smoke/backup test copies under LOCALAPPDATA\\LiuHao"
    )
    parser.add_argument("--root", default=None, help="Override the LiuHao directory")
    parser.add_argument("--dry-run", action="store_true", help="Only list, never delete")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive prompt (still prints every path first)",
    )
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else _default_root()
    candidates = find_candidates(root)

    print(f"Root: {root}")
    if not candidates:
        print("No leftover test data directories found. Nothing to do.")
        return 0

    print(
        f"Found {len(candidates)} leftover test data director"
        f"{'y' if len(candidates) == 1 else 'ies'}:"
    )
    for candidate in candidates:
        print(_describe(candidate))
    print(
        "Only these prefixes are candidates; product data ('data'), 'logs' and "
        "'diagnostics' are never touched."
    )

    if args.dry_run:
        print("Dry run: nothing deleted.")
        return 0

    if not args.yes:
        answer = input(
            f"Deletion is permanent. Type YES to delete these {len(candidates)} "
            "directories: "
        )
        if answer.strip() != "YES":
            print("Aborted. Nothing was deleted.")
            return 1

    failures = 0
    for candidate in candidates:
        try:
            shutil.rmtree(candidate)
            print(f"Deleted: {candidate}")
        except OSError as exc:
            failures += 1
            print(f"FAILED to delete {candidate}: {exc}", file=sys.stderr)
    if failures:
        print(f"{failures} director(y/ies) could not be removed.", file=sys.stderr)
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
