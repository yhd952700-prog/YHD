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

#: 开发/部署侧的配置模板：安装版一律不用。上一版交付把它们随包带了出去，
#: 用户照着 ``development.yaml`` 改配置只会改错文件。出现即构建失败。
#: **必须与 ``stage_payload.CONFIG_EXCLUDES`` 保持一致。**
FORBIDDEN_CONFIG_NAMES = {
    "development.yaml",
    "testing.yaml",
    "crontab",
    "supervisor.conf",
    "nginx.conf",
    "prometheus.yml",
    "grafana-dashboard.json",
    "alert-rules.yaml",
}

#: 第三方发行包里只服务于上游测试的路径（上一版交付被审计出 344 个）。
THIRD_PARTY_TEST_DIRS = {"test", "tests", "testing"}
THIRD_PARTY_TEST_FILE_NAMES = {"conftest.py", "tox.ini", "pytest.ini", "setup.cfg"}

#: 安装版必须随包的文件（0.1.1 交付口径，缺一即失败）。
#: 许可证文本按 ``packaging/licenses/`` 实际文件逐个核对，见 ``check``。
REQUIRED_INSTALL_FILES = (
    "scripts/first_run_gui.py",            # 图形化首启（默认入口）
    "scripts/purge_installed_config.py",   # 覆盖升级后清理旧配置
    "scripts/purge_pruned_payload.py",     # 覆盖升级后清理已剪枝旧文件
    "scripts/collect_diagnostics.bat",     # 离线诊断入口
    "THIRD-PARTY-NOTICES.txt",             # 第三方许可证索引
)

#: 剪枝清单与运行期守卫（``stage_payload._prune_payload`` 产出）。
PRUNE_MANIFEST_NAME = "prune-manifest.json"
PRUNE_GUARD_FILES = (
    "runtime/Lib/site-packages/_liuhao_prune_guard.py",
    "runtime/Lib/site-packages/liuhao_prune_guard.pth",
)


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

    # ---- 以下三类是「上一版交付被审计出来的真实缺陷」，不是可选建议 ----

    # 1) 开发/部署配置模板混进 config/
    config_dir = app_root / "config"
    if config_dir.is_dir():
        for entry in config_dir.iterdir():
            if entry.is_file() and entry.name in FORBIDDEN_CONFIG_NAMES:
                problems.append(("禁忌配置模板", _relative(entry, app_root)))

    # 2) 第三方发行包的上游测试文件/目录（只查 site-packages，标准库已按目录跳过）
    site_packages = app_root / "runtime" / "Lib" / "site-packages"
    if site_packages.is_dir():
        for entry in site_packages.rglob("*"):
            if entry.is_dir():
                if entry.name.lower() in THIRD_PARTY_TEST_DIRS:
                    problems.append(("第三方测试目录", _relative(entry, app_root)))
            elif entry.is_file():
                name = entry.name.lower()
                if (
                    name in THIRD_PARTY_TEST_FILE_NAMES
                    or entry.match("test_*.py")
                    or entry.match("*_test.py")
                ):
                    problems.append(("第三方测试文件", _relative(entry, app_root)))

    # 3) 必须随包的入口与许可证文本
    for rel in REQUIRED_INSTALL_FILES:
        if not (app_root / rel).is_file():
            problems.append(("缺少交付内容", rel))

    # 4) 剪枝清单与实际树必须自洽：清单说删掉的文件不许再出现，守卫必须随包。
    #    老版本安装树没有清单，只在其存在时校验，避免把历史安装判成脏。
    manifest_path = app_root / PRUNE_MANIFEST_NAME
    if manifest_path.is_file():
        import json

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            problems.append(("剪枝清单损坏", f"{PRUNE_MANIFEST_NAME}: {exc}"))
            manifest = {}
        if isinstance(manifest, dict) and manifest.get("enabled"):
            for rel in manifest.get("paths") or []:
                if (app_root / str(rel)).exists():
                    problems.append(("剪枝清单里的文件仍在树中", str(rel)))
            for rel in PRUNE_GUARD_FILES:
                if not (app_root / rel).is_file():
                    problems.append(("缺少剪枝守卫", rel))
            if not (manifest.get("modules") or {}):
                problems.append(("剪枝清单为空", PRUNE_MANIFEST_NAME))

    licenses_src = Path(__file__).resolve().parents[1] / "licenses"
    if licenses_src.is_dir():
        for source in sorted(licenses_src.iterdir()):
            if source.is_file() and not (app_root / "licenses" / source.name).is_file():
                problems.append(("缺少许可证文本", f"licenses/{source.name}"))
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
    categories = {category for category, _rel in problems}
    print("  构建中止：清理后再打包（这些路径只应存在于运行期，不属于交付树）。",
          file=sys.stderr)
    print("  按类别修复：", file=sys.stderr)
    if "禁忌配置模板" in categories:
        print("    · 禁忌配置模板 → 在 packaging/stage_payload.py 的 CONFIG_EXCLUDES 里"
              "补上对应文件名，再重跑 stage_payload（本门禁与它必须一致）。", file=sys.stderr)
    if {"第三方测试目录", "第三方测试文件"} & categories:
        print("    · 第三方测试文件 → 检查 packaging/stage_payload.py 的 _copy_distributions"
              " 是否走了 RECORD 分支；_is_test_artifact 过滤被绕过时先修它，不要手删 payload。",
              file=sys.stderr)
    if {"缺少交付内容", "缺少许可证文本"} & categories:
        print("    · 缺少交付内容/许可证 → 核对 stage_payload.PAYLOAD_SCRIPTS 与"
              " packaging/licenses/，补齐后重跑 python packaging/stage_payload.py。",
              file=sys.stderr)
    if {"剪枝清单里的文件仍在树中", "缺少剪枝守卫", "剪枝清单为空"} & categories:
        print("    · 剪枝不一致 → 用 python packaging/stage_payload.py 重新组装"
              "（默认开启剪枝），不要在 payload 上手工补删；若要回滚，加 --no-prune。",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
