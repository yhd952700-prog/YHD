"""自启入口：供 ``HKCU\\...\\Run`` 用 ``pythonw.exe`` 调用（源码/安装两种布局通用）。

注册表启动项的当前目录不可预期，因此这里显式把程序根加入 ``sys.path``，
再交给 ``src.desktop`` 的命令行入口；``--autostart`` 表示只驻留托盘、
不弹窗口、不自动开浏览器。

安装目录（``runtime\\pythonw.exe`` 存在）的额外处理：

* 先把数据目录/日志/驾驶舱路径切到安装版布局（复用
  ``scripts/launch_installed.prepare_environment``），否则 ``src.desktop``
  会把数据库和日志写进程序目录，并去找仓库里的 ``apps/console``；
* ``autostart enable`` 写注册表之前，先确认解释器（``runtime\\pythonw.exe``
  或当前解释器同目录的 pythonw）与入口脚本都是安装目录内的真实文件，
  找不到就**拒绝写入**并说明缺什么 —— 不写一个注定失败的启动项。

环境准备失败时不会静默失败：原因同时写到数据目录的
``logs/desktop-boot.log``（``pythonw.exe`` 没有控制台，否则无从排查）。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 安装目录的标志：离线运行时随安装包分发，源码目录没有。
INSTALLED_PYTHONW = ROOT / "runtime" / "pythonw.exe"

#: 自启值里绝不允许出现的开发机路径（出现即视为打包缺陷）。
DEV_PATH_MARKERS = ("LiuHao-AI-OS", ".venv")


def _installed_layout() -> bool:
    # 以「安装包自带的离线运行时」为标志；即使 pythonw 丢了，也仍按安装版
    # 处理，从而在写自启前拒绝，而不是退回开发机解释器。
    return (ROOT / "runtime" / "python.exe").is_file() or INSTALLED_PYTHONW.is_file()


def _boot_log_path() -> Path:
    data_override = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    base = (
        Path(data_override).expanduser()
        if data_override
        else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "LiuHao" / "data"
    )
    return base / "logs" / "desktop-boot.log"


def _report_failure(message: str) -> None:
    """把启动失败原因同时写到 stderr 与数据目录日志（无控制台时也能查）。"""
    print(message, file=sys.stderr)
    try:
        path = _boot_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            stamp = datetime.now().isoformat(timespec="seconds")
            handle.write(f"{stamp} {message}\n")
    except OSError:
        pass


def _prepare_installed_layout() -> bool:
    """把 ``src.desktop`` 的数据/日志/驾驶舱路径切到安装版布局。"""
    try:
        import scripts.launch_installed as installed

        data_dir, _logging_config = installed.prepare_environment()

        import src.desktop as desktop
        import src.desktop.__main__ as desktop_main
        import src.desktop.app as desktop_app
        import src.desktop.service as desktop_service
    except Exception as exc:  # noqa: BLE001 - 安装目录损坏时给出可读原因
        _report_failure(f"[FAIL] 安装版运行环境准备失败：{type(exc).__name__}: {exc}")
        return False

    console_dir = ROOT / "console"
    desktop.REPO_ROOT = ROOT
    desktop.DATA_DIR = data_dir
    desktop.LOG_DIR = data_dir / "logs"
    desktop.CONSOLE_DIST = console_dir

    desktop_app.DATA_DIR = data_dir
    desktop_app.LOG_DIR = data_dir / "logs"
    desktop_app.CONSOLE_DIST = console_dir

    # 服务以 ``self._root/config/logging.yaml`` 为子进程日志配置、
    # ``cwd`` 也取 ``self._root``；安装版这两者都在数据目录。
    desktop_service.REPO_ROOT = data_dir
    desktop_service.LOG_DIR = data_dir / "logs"
    # 驾驶舱在安装包里是 ``app/console``，不是仓库的
    # ``apps/console/console/dist``，因此改由属性返回。
    gateway_cls = desktop_service.GatewayService
    gateway_cls.console_dist = property(lambda self: console_dir)
    gateway_cls.console_ready = property(
        lambda self: (console_dir / "index.html").is_file()
    )

    desktop_main.REPO_ROOT = ROOT
    return True


def _resolve_autostart_targets() -> tuple[Path, Path]:
    """解析自启要用的解释器与入口；任一缺失就抛 RuntimeError（不写注册表）。

    安装版只认安装目录自带的 ``runtime\\pythonw.exe``。不退回 ``sys.executable``
    的同目录 pythonw，也不退回开发机的虚拟环境 —— 否则会把开发路径写进
    注册表，开机在别的机器上必然失败。
    """
    import src.desktop.autostart as autostart

    pythonw = ROOT / autostart.INSTALLED_PYTHONW
    entry = ROOT / autostart.DESKTOP_ENTRY

    missing: list[str] = []
    if not pythonw.is_file():
        missing.append(r"runtime\pythonw.exe（安装包内的无控制台解释器）")
    if not entry.is_file():
        missing.append(r"scripts\liuhao_desktop.py（托盘入口）")
    if missing:
        raise RuntimeError("、".join(missing))
    return pythonw, entry


def _value_name(args: list[str]) -> str:
    for index, item in enumerate(args):
        if item == "--value-name" and index + 1 < len(args):
            return args[index + 1]
        if item.startswith("--value-name="):
            return item.split("=", 1)[1]
    return "LiuHaoAIOS"


def _verify_written_autostart(args: list[str], pythonw: Path, entry: Path) -> int:
    """回读注册表，确认写进去的是安装目录内的真实文件且不含开发路径。"""
    import src.desktop.autostart as autostart

    command = (autostart.read(_value_name(args)).get("command") or "")
    print(f"[autostart] 注册表值：{command}")
    problems: list[str] = []
    if str(pythonw) not in command:
        problems.append(f"缺少安装目录解释器 {pythonw}")
    if str(entry) not in command:
        problems.append(f"缺少安装目录入口 {entry}")
    for marker in DEV_PATH_MARKERS:
        if marker in command:
            problems.append(f"含开发路径标记 {marker}")
    if problems:
        _report_failure("[FAIL] 开机自启写入结果不正确：" + "；".join(problems))
        return 3
    print("[autostart] 校验通过：解释器与入口均为安装目录内的真实文件")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "app"

    if _installed_layout() and not _prepare_installed_layout():
        return 2

    checking_autostart = (
        command == "autostart" and len(args) >= 2 and args[1] == "enable"
    )
    targets: tuple[Path, Path] | None = None
    if checking_autostart:
        try:
            targets = _resolve_autostart_targets()
        except RuntimeError as exc:
            _report_failure(
                "[FAIL] 拒绝写入开机自启：安装目录里找不到可用的 " + str(exc)
                + "。未写入注册表，请重新安装完整安装包后重试。"
            )
            return 2
        print(f"[autostart] 解释器 {targets[0]}")
        print(f"[autostart] 入口   {targets[1]}")

    import src.desktop.__main__ as desktop_main

    code = desktop_main.main(args)
    if checking_autostart and code == 0 and targets is not None:
        return _verify_written_autostart(args, targets[0], targets[1])
    if code != 0 and command in ("", "app"):
        _report_failure(f"[FAIL] 桌面壳启动失败，src.desktop 返回 {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
