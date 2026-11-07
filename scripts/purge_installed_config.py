#!/usr/bin/env python
"""删除安装目录里“曾经随包、新版已排除”的配置项。

覆盖升级只复制新清单里的文件，不会删除旧版遗留文件。``config/production.yaml``
在 0.1.1 的打包清单里已被排除，但它仍在旧版安装目录里——升级后不清理，就
等于「排除了又被带回来」。安装器在文件复制完成后调用本脚本，按与
``packaging/stage_payload.py`` 相同的排除清单清扫一次。

**不碰用户数据**：``auth_secrets.json`` / ``human_identities.json`` 是账号与
凭据，启动器负责把它们迁移进数据目录；安装器直接删会造成账号丢失，所以
明确列在 :data:`PRESERVED` 里（测试断言：本清单与打包清单的差集恰好是这两个）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import List, Sequence

#: 必须与 ``packaging/stage_payload.CONFIG_EXCLUDES`` 保持一致，
#: 去掉用户凭据后即为本清单（见 ``tests/test_purge_installed_config.py``）。
CONFIG_EXCLUDES: Sequence[str] = (
    "production",       # 目录：生产部署脚本/验收清单
    "production.yaml",  # 文件：0.1.0 及更早随包，升级后必须清掉
    "external_anchor",  # 目录：外部锚点记录与私钥
    "docker.env",
    "*.pem",
    "*.key",
    "*.lock",           # 运行时锁文件，如 auth_secrets.json.lock
)

#: 由启动器迁移进数据目录的用户数据，安装器不得删除。
PRESERVED = ("auth_secrets.json", "human_identities.json")


def _matches(name: str, pattern: str) -> bool:
    """与 ``stage_payload._copy_config`` 相同的匹配规则（Windows 不区分大小写）。"""
    if pattern.startswith("*."):
        return name.lower().endswith(pattern[1:].lower())
    return name.lower() == pattern.lower()


def _looks_like_install_root(root: Path) -> bool:
    return (root / "runtime").is_dir() or (root / "LiuHao.bat").is_file()


def purge(root: Path) -> List[str]:
    """删除 ``root/config`` 下命中的旧配置项，返回被删除的名字。"""
    config = root / "config"
    if not config.is_dir():
        return []
    removed: List[str] = []
    for entry in sorted(config.iterdir()):
        name = entry.name
        if name in PRESERVED:
            continue
        if not any(_matches(name, pattern) for pattern in CONFIG_EXCLUDES):
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            print(f"[purge] 无法删除 {entry}: {exc}", file=sys.stderr)
            continue
        removed.append(name)
    return removed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理安装目录里已不随包的旧配置项")
    parser.add_argument("--root", required=True, help="安装目录（{app}）")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not _looks_like_install_root(root):
        print(
            f"[purge] 跳过：{root} 不像安装目录（找不到 runtime\\ 或 LiuHao.bat）",
            file=sys.stderr,
        )
        return 2

    removed = purge(root)
    if removed:
        print(f"[purge] 已删除 {len(removed)} 个新版不再随包的配置项：{', '.join(removed)}")
    else:
        print("[purge] 无需要清理的旧配置项")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
