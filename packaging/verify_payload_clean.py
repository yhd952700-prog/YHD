"""交付树卫生硬门禁：安装树 / 升级包里不许有构建机运行残留与真实密钥。

为什么必须是硬失败而不是警告：Inno Setup 的 ``[Files]`` 是整目录复制，
``stage_payload`` 之后只要跑过一次冒烟或升级自检，``__pycache__``、
``upgrade-state``、``*.db``、``*.log`` 就会留在 ``payload/app`` 里，随后
原样进入安装包。人工清理靠“记得”，门禁把“记得”变成“不干净就构建失败”。

判定口径（与 ``docs/self-upgrade.md`` §2.2.5 的禁止清单一致）：

  * 任何层级的 ``__pycache__`` / ``upgrade-state`` / ``upgrade-backups``；
  * 安装根下的 ``data`` / ``logs`` 运行目录；
  * ``*.pyc``、``*.pyo``、``*.db``（含 WAL/SHM）、``*.sqlite3``、``*.log``；
  * 凭据文件名（``auth_secrets.json`` 等）与 ``*.key``／``*.pem``；
  * 形如 ``sk-<20 位以上字母数字>`` 的真实密钥文本。

用法：
    python packaging/verify_payload_clean.py --app-root dist/payload/app
退出码 0 = 干净；1 = 命中禁用项（逐条打印，便于直接修）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

#: 只应在运行期出现的目录名（任何层级命中都算脏）。
RUNTIME_DIR_NAMES = {"__pycache__", "upgrade-state", "upgrade-backups"}

#: 只应在运行期出现在安装根下的目录（不允许出现在交付树根）。
RUNTIME_ROOT_DIRS = {"data", "logs", "upgrade-state", "upgrade-backups"}

#: 运行期产物后缀。
RUNTIME_SUFFIXES = {
    ".pyc", ".pyo",
    ".db", ".db-wal", ".db-shm",
    ".sqlite3", ".sqlite3-wal", ".sqlite3-shm",
    ".log",
}

#: 凭据类文件名（精确匹配）。
CREDENTIAL_NAMES = {
    "auth_secrets.json", "human_identities.json", "docker.env", ".env",
}

#: 凭据类后缀。注意不能一刀切拦 ``.pem``：certifi 等依赖自带 CA 证书包。
CREDENTIAL_SUFFIXES = {".key"}

#: 真实密钥的形态。测试占位符（如 ``sk-e2e-test-key``）不足 20 位，不会误伤。
#: 前置词边界是必需的：SPDX 许可证 ID ``…sk-linking-protocols-exception`` 这类
#: 字符串本身含 ``sk-``，不加边界会把随包分发的第三方文件全部判成泄漏。
SECRET_PATTERN = re.compile(rb"(?<![A-Za-z0-9_\-])sk-[A-Za-z0-9_\-]{20,}")

#: 密钥扫描的排除前缀。``runtime/`` 是 CPython 标准库与第三方发行包的原样副本，
#: 其中的字面串来自上游（如 SPDX 标识、psutil 的示例），不是本项目产物，
#: 也不可能被本项目写入密钥；对它们做密钥扫描只会制造误报。
SECRET_SCAN_SKIP_PREFIXES = ("runtime/",)

#: 可选的外部精确针（把本轮用过、已作废的真实 Key 直接钉死），避免把它
#: 写进仓库本身。设置 ``LIUHAO_KEY_NEEDLE`` 后额外按字面匹配。
NEEDLE_ENV = "LIUHAO_KEY_NEEDLE"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _scan_secret(path: Path, needle: bytes | None) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    if SECRET_PATTERN.search(data):
        return True
    return bool(needle and needle in data)


def check(app_root: Path) -> List[Tuple[str, str]]:
    """返回 ``(类别, 相对路径)`` 列表；空列表表示干净。"""
    problems: List[Tuple[str, str]] = []
    if not app_root.is_dir():
        return [("路径不存在", str(app_root))]

    needle_raw = os.environ.get(NEEDLE_ENV, "")
    needle = needle_raw.encode("utf-8") if needle_raw else None

    for entry in app_root.rglob("*"):
        rel = _relative(entry, app_root)
        if entry.is_dir():
            if entry.name in RUNTIME_DIR_NAMES:
                problems.append(("运行目录", rel))
            continue
        if not entry.is_file():
            continue
        if rel.split("/", 1)[0] in RUNTIME_ROOT_DIRS:
            problems.append(("运行根目录内的文件", rel))
            continue
        if entry.name in CREDENTIAL_NAMES or entry.suffix.lower() in CREDENTIAL_SUFFIXES:
            problems.append(("凭据文件", rel))
            continue
        if entry.suffix.lower() in RUNTIME_SUFFIXES:
            problems.append(("运行产物", rel))
            continue
        if rel.startswith(SECRET_SCAN_SKIP_PREFIXES):
            continue
        if _scan_secret(entry, needle):
            problems.append(("疑似明文密钥", rel))
    return problems


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交付树卫生硬门禁")
    parser.add_argument("--app-root", required=True, help="payload/app 或安装树根")
    parser.add_argument("--label", default="payload", help="输出前缀（便于区分 payload / 安装树）")
    args = parser.parse_args(argv)

    root = Path(args.app_root).resolve()
    problems = check(root)
    total = sum(1 for item in root.rglob("*") if item.is_file()) if root.is_dir() else 0

    if not problems:
        print(f"  交付树卫生检查通过（{args.label}，{total} 个文件，0 处命中）")
        return 0

    print(f"  交付树卫生检查未通过（{args.label}，{total} 个文件，{len(problems)} 处命中）：",
          file=sys.stderr)
    for category, rel in problems[:40]:
        print(f"    !! [{category}] {rel}", file=sys.stderr)
    if len(problems) > 40:
        print(f"    …… 其余 {len(problems) - 40} 条同类，已省略", file=sys.stderr)
    print("  构建中止：清理后再打包（这些路径只应存在于运行期，不属于交付树）。",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
