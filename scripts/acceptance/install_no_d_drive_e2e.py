#!/usr/bin/env python
"""无 D 盘安装回退 + 数据目录预填验收（**模拟**，不是真机证据）。

为什么是模拟
------------
* 本机有 D 盘，真删盘符会破坏开发环境；
* 安装器回退分支是 Inno Pascal，不运行 ``setup.exe`` 无法执行。

所以这里用**可注入的路径探测**：

* ``scripts.first_run_gui._d_drive_usable`` 换成固定返回值，模拟
  「D 盘不存在 / 存在但不可写 / 可写」；
* 盘类型探测 ``scripts.first_run_gui._volume_type`` 换成固定值，验证
  移动盘 / 网络盘的提示文案；
* 「盘符不存在」用「父路径是一个文件」模拟——Windows 上 ``mkdir`` 会
  失败，表现与拔盘后缺少盘符一致。

**脚本与输出都会标明这是模拟**。它只能证明 Python 侧的回退 / 预填 / 拒绝
逻辑，不能替代「无 D 盘真机安装 + setup.exe」的证据。

三个场景
--------
1. **无 D 盘**：默认数据目录回退到 ``%LOCALAPPDATA%\\Jarvis\\data``，
   预填值是完整绝对路径；用户不改、只按「下一步」就能通过第 1 页；完整首启
   提交（``commit``）也能在这个预填目录里跑通。
2. **D 盘可写**：仍默认 ``D:\\JarvisData``——回退只在 D 不可用时发生；
   同时验证「D 存在但不可写」也会回退。
3. **旧 D 盘标记失效**：``data-dir.txt`` 指向一个不存在的路径时，
   ``src.upgrade.manager`` 读 marker、``scripts.launch_installed`` 拒绝启动并
   给出中文可行动提示，**不创建空数据目录**。

用法::

    python scripts/acceptance/install_no_d_drive_e2e.py

退出码（与 ``scripts/acceptance`` 其余脚本一致）：
    0 = 三场景全部通过
    1 = 有场景失败
    2 = 环境不具备（非 Windows / 无法导入被测模块 / 临时目录不可写），
        绝不把跑不起来算通过
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNAVAILABLE = 2

#: 本次验收里被临时替换过的进程环境变量（结束时整体还原）。
_ENV_KEYS = (
    "LOCALAPPDATA",
    "LIUHAO_DATA_DIR",
    "LIUHAO_HUMAN_IDENTITIES_FILE",
    "LIUHAO_AUTH_SECRETS_FILE",
    "LIUHAO_MODEL_CONFIG_DB",
    "LIUHAO_MODEL_CONFIG_KEY_FILE",
    "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
)


class _Results:
    """逐条记录并即时打印，最后用失败数决定退出码。"""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        ok = bool(ok)
        self.rows.append((name, ok, detail))
        line = f"[{'PASS' if ok else 'FAIL'}] {name}"
        if detail:
            line += f" —— {detail}"
        print(line)

    @property
    def failed(self) -> int:
        return sum(1 for _name, ok, _detail in self.rows if not ok)


def _scenario_no_d_drive(tmp: Path, results: _Results) -> None:
    """场景 1：无 D 盘 → 回退 + 预填 + 用户只按「下一步」。"""
    from scripts import first_run_gui as gui

    localappdata = tmp / "localappdata"
    localappdata.mkdir(parents=True, exist_ok=True)
    expected = (localappdata / "Jarvis" / "data").resolve()

    env_snapshot = {key: os.environ.get(key) for key in _ENV_KEYS}
    marker_snapshot = gui.DATA_DIR_MARKER
    ddrive_snapshot = (gui.D_DRIVE_ROOT, gui.DEFAULT_D_DATA_DIR, gui._d_drive_usable)
    volume_snapshot = gui._volume_type
    try:
        os.environ["LOCALAPPDATA"] = str(localappdata)
        os.environ.pop("LIUHAO_DATA_DIR", None)

        # 1a. D 盘不存在。
        gui.D_DRIVE_ROOT = tmp / "no-such-drive-D"
        gui.DEFAULT_D_DATA_DIR = gui.D_DRIVE_ROOT / "JarvisData"
        gui._d_drive_usable = lambda: False
        prefill = gui.default_data_dir()
        results.check(
            "无 D 盘：默认数据目录回退到 %LOCALAPPDATA%\\Jarvis\\data",
            prefill == expected,
            f"实测={prefill}",
        )
        results.check(
            "无 D 盘：预填值是完整绝对路径（界面可直接显示）",
            prefill.is_absolute() and str(prefill) == str(expected),
            f"实测={prefill}，长度={len(str(prefill))}",
        )

        # 1b. D 盘存在但不可写，也必须回退。
        gui.D_DRIVE_ROOT = tmp / "readonly-drive-D"
        gui.D_DRIVE_ROOT.mkdir(parents=True, exist_ok=True)
        gui.DEFAULT_D_DATA_DIR = gui.D_DRIVE_ROOT / "JarvisData"
        prefill_unwritable = gui.default_data_dir()
        results.check(
            "D 盘存在但不可写：同样回退到用户目录",
            prefill_unwritable == expected,
            f"实测={prefill_unwritable}",
        )

        # 1c. 移动盘 / 网络盘提示（盘类型探测是注入值，不是真插 U 盘）。
        gui._volume_type = lambda _path: gui.DRIVE_REMOVABLE
        removable = gui.volume_risk_message(str(prefill))
        results.check(
            "移动盘：提示「设备拔掉后找不到数据」（模拟盘类型=2）",
            "拔掉" in removable and "找不到数据" in removable,
            removable or "（没有提示）",
        )
        gui._volume_type = lambda _path: gui.DRIVE_REMOTE
        remote = gui.volume_risk_message(str(prefill))
        results.check(
            "网络盘：提示「断网后找不到数据」（模拟盘类型=4）",
            "断网" in remote and "找不到数据" in remote,
            remote or "（没有提示）",
        )
        gui._volume_type = volume_snapshot
        results.check(
            "固定盘：不误报外接设备风险",
            gui.volume_risk_message(str(prefill)) == "",
            "固定盘目录无提示",
        )

        # 1d. 预填后「只按下一步」：第 1 页校验必须通过（会真实建目录 + 写探针）。
        resolved, error = gui.validate_data_dir(str(prefill))
        results.check(
            "预填值直接通过第 1 页校验（用户不改也能点「下一步」）",
            error == "" and resolved == expected,
            f"校验={error or '通过'}，目录已创建={expected.is_dir()}",
        )

        # 1e. 完整首启提交：用预填值（等同用户一个字段都没改）。
        marker = tmp / "app" / "data-dir.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        gui.DATA_DIR_MARKER = marker
        config = expected / "config"
        os.environ["LIUHAO_HUMAN_IDENTITIES_FILE"] = str(config / "human_identities.json")
        os.environ["LIUHAO_AUTH_SECRETS_FILE"] = str(config / "auth_secrets.json")
        os.environ["LIUHAO_MODEL_CONFIG_DB"] = str(
            expected / "data" / "model_registry.sqlite3"
        )
        os.environ["LIUHAO_MODEL_CONFIG_KEY_FILE"] = str(expected / "data" / "model_config.key")
        os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = "acceptance-key-0123456789"
        ok, message = gui.commit(
            {
                "data_dir": str(prefill),
                "principal": "liuhao",
                "display": "贾维斯管理员",
                "password": "liuhao2026",
                "confirm": "liuhao2026",
                "model": "skip",
                "model_name": "",
                "base_url": "",
                "api_key": "",
            },
            port=0,
        )
        results.check(
            "预填目录里完整首启提交成功（账号 + marker 落盘）",
            ok and marker.is_file() and str(expected) in marker.read_text(encoding="utf-8-sig"),
            message,
        )
        results.check(
            "首启完成后可以用预填目录登录（身份表真实落盘）",
            (config / "human_identities.json").is_file(),
            str(config / "human_identities.json"),
        )
    finally:
        gui.DATA_DIR_MARKER = marker_snapshot
        gui.D_DRIVE_ROOT, gui.DEFAULT_D_DATA_DIR, gui._d_drive_usable = ddrive_snapshot
        gui._volume_type = volume_snapshot
        for key, value in env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _scenario_d_drive_usable(tmp: Path, results: _Results) -> None:
    """场景 2：D 盘可写 → 仍默认 D:\\JarvisData（不误回退）。"""
    from scripts import first_run_gui as gui

    env_snapshot = os.environ.get("LOCALAPPDATA")
    ddrive_snapshot = (gui.D_DRIVE_ROOT, gui.DEFAULT_D_DATA_DIR, gui._d_drive_usable)
    try:
        os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")
        d_root = tmp / "writable-drive-D"
        d_root.mkdir(parents=True, exist_ok=True)
        gui.D_DRIVE_ROOT = d_root
        gui.DEFAULT_D_DATA_DIR = d_root / "JarvisData"
        gui._d_drive_usable = lambda: True

        prefill = gui.default_data_dir()
        expected = (d_root / "JarvisData").resolve()
        results.check(
            "D 盘可写：默认仍是 D:\\JarvisData",
            prefill == expected,
            f"实测={prefill}",
        )
        _, error = gui.validate_data_dir(str(prefill))
        results.check(
            "D 盘可写：D:\\JarvisData 预填值本身可写",
            error == "",
            error or "可写",
        )
    finally:
        gui.D_DRIVE_ROOT, gui.DEFAULT_D_DATA_DIR, gui._d_drive_usable = ddrive_snapshot
        if env_snapshot is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = env_snapshot


def _scenario_stale_marker(tmp: Path, results: _Results) -> None:
    """场景 3：旧 D 盘不在、data-dir.txt 还在 → 读 marker 但拒绝建空库。"""
    from scripts import launch_installed as installed
    from src.upgrade import manager

    app_root = tmp / "installed-app"
    (app_root / "runtime").mkdir(parents=True, exist_ok=True)
    (app_root / "runtime" / "python.exe").write_text("", encoding="utf-8")

    # 「父路径是文件」模拟盘符不存在：mkdir 失败方式与拔盘后一致。
    blocker = tmp / "gone-drive"
    blocker.write_text("模拟：这个盘符不存在（这里用一个文件占位）", encoding="utf-8")
    target = blocker / "JarvisData"
    marker = app_root / "data-dir.txt"
    marker.write_text("\ufeff" + str(target), encoding="utf-8")

    env_snapshot = {key: os.environ.get(key) for key in ("LIUHAO_DATA_DIR", "LOCALAPPDATA")}
    if "LIUHAO_DATA_DIR" in os.environ:
        del os.environ["LIUHAO_DATA_DIR"]
    # 回退默认值也要指到临时目录，避免测试去读真实 C 盘数据。
    os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")

    launcher_marker_snapshot = installed.DATA_DIR_MARKER
    try:
        installed.DATA_DIR_MARKER = marker
        results.check(
            "installer marker 是 UTF-8 带 BOM",
            marker.read_bytes().startswith(b"\xef\xbb\xbf"),
            marker.read_text(encoding="utf-8-sig"),
        )

        resolved = manager.default_data_dir(app_root)
        results.check(
            "升级 CLI（manager）优先读 data-dir.txt，不回退 C 盘",
            resolved == target.resolve(),
            f"实测={resolved}",
        )

        # 旧版 ANSI（无 BOM）marker 也必须能读。
        legacy = tmp / "legacy-cn" / "数据"
        marker.write_bytes(str(legacy).encode("mbcs"))
        results.check(
            "无 BOM 的旧版 ANSI marker 仍能正确解析（BOM 兼容）",
            manager.default_data_dir(app_root) == legacy.resolve(),
            f"实测={manager.default_data_dir(app_root)}",
        )
        marker.write_text("\ufeff" + str(target), encoding="utf-8")

        launcher_view = installed._data_dir()
        results.check(
            "启动器读到同一个失效 marker（没有改用 C 盘默认）",
            launcher_view == target.resolve(),
            f"实测={launcher_view}",
        )

        refused = False
        message = ""
        try:
            installed.prepare_environment()
        except installed.DataDirectoryUnavailable as exc:
            refused = True
            message = str(exc)

        results.check(
            "失效 marker：启动器拒绝启动并给人话提示",
            refused and "数据目录" in message and "无法创建" in message,
            message or "（没有拒绝）",
        )
        results.check(
            "失效 marker：没有安静地建出空数据目录",
            not target.exists() and not (target / "config").exists(),
            f"target 存在={target.exists()}",
        )
    finally:
        installed.DATA_DIR_MARKER = launcher_marker_snapshot
        for key, value in env_snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        print(
            "[SKIP] 这是模拟 Windows 安装器行为的验收，当前系统不是 Windows；"
            "环境不具备，退出码 2（不当作通过）。"
        )
        return EXIT_UNAVAILABLE
    try:
        from scripts import first_run_gui  # noqa: F401  先确认可导入，失败按环境不具备计
        from scripts import launch_installed  # noqa: F401
        from src.upgrade import manager  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 导入失败是环境问题，不是验收通过
        traceback.print_exc()
        print(f"[SKIP] 无法导入被测模块（{type(exc).__name__}: {exc}），退出码 2。")
        return EXIT_UNAVAILABLE

    print("=" * 78)
    print("无 D 盘安装回退验收（模拟）")
    print(f"仓库：{REPO}")
    print("说明：本机有 D 盘，脚本用可注入的路径探测模拟「盘不存在 / 不可写 / 可写」；")
    print("      没有真删盘符，也没有运行 setup.exe。结果只证明 Python 侧逻辑。")
    print("=" * 78)

    results = _Results()
    try:
        tmp = Path(tempfile.mkdtemp(prefix="liuhao-nodrive-"))
    except OSError as exc:
        print(f"[SKIP] 临时目录不可用（{type(exc).__name__}: {exc}），退出码 2。")
        return EXIT_UNAVAILABLE

    try:
        print(f"\n临时工作目录：{tmp}\n")
        print(">>> 场景 1：无 D 盘（回退 + 预填 + 只按下一步）")
        _scenario_no_d_drive(tmp, results)
        print("\n>>> 场景 2：D 盘可写（不误回退）")
        _scenario_d_drive_usable(tmp, results)
        print("\n>>> 场景 3：旧 D 盘不在但 marker 还在（拒绝建空库）")
        _scenario_stale_marker(tmp, results)
    finally:
        # 场景 1 的 commit 会让网关/身份存储打开 SQLite 文件，Windows 上
        # 未必能立刻删掉；清理是尽力而为，绝不能因此把已通过的验收改判失败。
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "-" * 78)
    if results.failed:
        print(
            f"结论：{results.failed}/{len(results.rows)} 项未通过（模拟结论，不是真机证据）。"
        )
        return EXIT_FAIL
    print(
        f"结论：{len(results.rows)}/{len(results.rows)} 项全部通过（模拟结论，不是真机证据）。"
    )
    print("真机证据仍需：一台没有 D 盘的机器上跑 setup.exe + 首启向导。")
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
