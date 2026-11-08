#!/usr/bin/env python
"""删除覆盖升级后残留的「新版 payload 已剪掉」的旧文件。

覆盖升级（Inno ``[Files]``）只复制新清单里的文件，**不会删除**旧版留下的文件。
剪枝把 2,000 多个文件从新版 payload 里拿掉之后，老安装目录里的这些文件就成了
「清单外残留」：既占体积，也让运行期守卫与实际磁盘不一致。安装器在文件复制
完成后调用本脚本，按新包自带的 ``prune-manifest.json`` 逐条删除一次。

安全边界
--------
* 只认 **新包** 的清单（安装时随 ``[Files]`` 覆盖到 ``{app}``），因此
  「新版本又决定要回来」的文件不会被误删；``enabled=false`` 时脚本空转。
* 只删 ``runtime/`` 下的相对路径；拒绝绝对路径、``..``、盘符与符号链接。
* 只 ``unlink`` 文件 + ``rmdir`` 因剪枝才变空的目录；**从不递归删除目录**，
  所以不可能把用户数据或同名残目录一起带走。
* 失败不阻断安装（安装器 ``[Run]`` 忽略退出码），但逐条报错并以退出码 1 结束，
  便于离线验收复跑。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

MANIFEST_NAME = "prune-manifest.json"
#: 清单里允许出现的前缀（与 ``packaging/stage_payload.PRUNE_RULES`` 一致）。
ALLOWED_PREFIX = "runtime/"


def _load_manifest(root: Path) -> Dict[str, Any] | None:
    path = root / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[prune-purge] 清单不可读：{path}（{exc}）", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def _safe_relative(root: Path, rel: str) -> Path | None:
    """把清单里的相对路径收敛到 ``root/runtime/**`` 内，任何越界都拒绝。"""
    text = str(rel).replace("\\", "/").strip()
    if not text or text.startswith("/") or ".." in text.split("/"):
        return None
    if ":" in text.split("/")[0]:
        return None
    if not text.lower().startswith(ALLOWED_PREFIX):
        return None
    candidate = (root / text).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def purge(root: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    manifest = _load_manifest(root)
    result: Dict[str, Any] = {
        "manifest": manifest is not None,
        "enabled": bool(manifest and manifest.get("enabled")),
        "deleted": 0,
        "bytes": 0,
        "missing": 0,
        "skipped": 0,
        "dirs_removed": 0,
        "errors": [],
    }
    if not manifest or not result["enabled"]:
        return result

    parents: List[Path] = []
    for rel in manifest.get("paths") or []:
        target = _safe_relative(root, rel)
        if target is None:
            result["skipped"] += 1
            result["errors"].append(f"拒绝越界路径：{rel}")
            continue
        if not target.exists():
            result["missing"] += 1
            continue
        if target.is_symlink() or not target.is_file():
            result["skipped"] += 1
            continue
        try:
            size = target.stat().st_size
            if dry_run:
                result["bytes"] += size
                result["deleted"] += 1
                continue
            target.unlink()
        except OSError as exc:
            result["errors"].append(f"{rel}: {exc}")
            continue
        result["deleted"] += 1
        result["bytes"] += size
        parent = target.parent
        while parent != root and root in parent.parents:
            parents.append(parent)
            parent = parent.parent

    if not dry_run:
        for directory in sorted(set(parents), key=lambda item: len(item.parts), reverse=True):
            try:
                directory.rmdir()
                result["dirs_removed"] += 1
            except OSError:
                continue
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="删除升级后残留的已剪枝旧文件")
    parser.add_argument("--root", required=True, help="安装目录（{app}）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不删除")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not ((root / "runtime").is_dir() or (root / "LiuHao.bat").is_file()):
        print(f"[prune-purge] 跳过：{root} 不像安装目录", file=sys.stderr)
        return 2

    result = purge(root, dry_run=args.dry_run)
    if not result["manifest"]:
        print(f"[prune-purge] 无 {MANIFEST_NAME}（旧版本安装），无需清理")
        return 0
    if not result["enabled"]:
        print("[prune-purge] 新版未启用剪枝（enabled=false），保留全部文件")
        return 0

    verb = "将删除" if args.dry_run else "已删除"
    print(
        f"[prune-purge] {verb} {result['deleted']} 个旧文件 / "
        f"{result['bytes'] / (1024 * 1024):.2f} MiB；"
        f"已是新版（不存在）{result['missing']} 个，跳过 {result['skipped']} 个，"
        f"清空目录 {result['dirs_removed']} 个"
    )
    for line in result["errors"][:10]:
        print(f"[prune-purge] {line}", file=sys.stderr)
    if result["errors"]:
        print(f"[prune-purge] 失败 {len(result['errors'])} 条（安装继续，可重跑本脚本）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
