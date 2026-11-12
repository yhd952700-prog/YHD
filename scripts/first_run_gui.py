#!/usr/bin/env python
"""首次运行的图形化向导（Windows 桌面，tkinter）。

为什么需要它
------------
双击启动原先会打开一个命令行窗口跑 ``input()`` 问答：普通用户要面对黑框、
中文输入法、输错就重来；在计划任务 / ``pythonw`` / 没有 stdin 的环境里直接
``EOFError``，表现就是"装完点不亮"。

本向导把同一套首启动作放进一个原生窗口，共五页
（数据目录 → 管理员账号 → 选择模型 → 完成前请确认 → 自检结果）：

1. 选数据目录（默认 ``D:\\LiuHaoData``，D 盘不可用时回退 ``%LOCALAPPDATA%``）；
2. 创建管理员账号与登录密码；
3. 选择模型：跳过 / 本机 Ollama / 云端 API Key（Key 仍由
   ``src.ai.model_config`` 以 AES-256-GCM 加密落盘，不写明文）；
4. 完成前请确认：再核一遍数据目录、加密、备份、卸载与联网边界；
5. 自检结果：固定四项检测（网关端口 / 数据目录 / 托盘驻留 / 默认模型）——
   端口试绑定（端口上已有贾维斯在监听也算通过）、数据目录试写临时文件、
   托盘查系统级单实例锁（本次启动将驻留托盘时也判为通过）、
   默认模型真的发一次最小请求；必要项通过（模型选「跳过」时标注稍后配置）后
   点「进入控制台」，由启动器拉起网关并打开浏览器。
   （控制台地址显示在状态窗口底部的「控制台：」一行；``config\\current_url.txt``
   只有源码启动器 ``scripts/start_liuhao.py`` 会写，安装版不生成。）

约束
----
* 全程不读 stdin：双击、计划任务、``pythonw`` 都能用。
* 任何失败都给出中文原因 + 日志路径，不把 traceback 甩给用户。
* 首启未完成（取消 / 失败 / 窗口打不开）时**不启动网关、不打印控制台地址**。
* 命令行向导保留为 ``--console`` 回退（见 ``scripts/launch_installed.py``）。

本模块同时提供 UI 无关的校验与提交函数（``validate_password``、
``commit`` 等），测试直接覆盖这些行为，不需要真实窗口。
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

EXIT_OK = 0
EXIT_CANCELED = 1
EXIT_UNAVAILABLE = 3

#: 工作台名字出厂默认值（Joseph 这份：贾维斯 / Jarvis）。向导里改了名字
#: 就不再是默认值；改完顶栏 / 登录页 / 助手名全都跟着变（见 src/brand.py）。
from src.brand import BRAND_NAME_EN, BRAND_NAME_ZH  # noqa: E402

DATA_DIR_ENV = "LIUHAO_DATA_DIR"
DATA_DIR_MARKER = APP_ROOT / "data-dir.txt"

DEFAULT_D_DATA_DIR = Path("D:/LiuHaoData")
#: D 盘根。单独提出来，验收脚本可以注入一个"不存在 / 不可写"的临时目录，
#: 不必真的删盘符（见 ``scripts/acceptance/install_no_d_drive_e2e.py``）。
D_DRIVE_ROOT = Path("D:/")

#: Win32 ``GetDriveTypeW`` 的返回值，与 ``installer/liuhao.iss`` 的可用性判定同义。
DRIVE_REMOVABLE = 2
DRIVE_REMOTE = 4

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
CLOUD_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
CLOUD_DEFAULT_MODEL = "deepseek-chat"

#: 密码策略：至少 8 位，且同时包含字母与数字。过弱的密码等于没有账号。
MIN_PASSWORD_LEN = 8
_PASSWORD_LETTER = re.compile(r"[A-Za-z]")
_PASSWORD_DIGIT = re.compile(r"[0-9]")
_PRINCIPAL = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{1,31}$")

#: commit() 的返回值：``(是否成功, 给用户看的中文说明)``。
CommitResult = tuple[bool, str]

#: 自检的硬超时：正常约 3 秒完成；到点必须把按钮恢复可点，不能让用户干等。
CHECK_TIMEOUT_SECONDS = 30.0
CHECK_TIMEOUT_HINT = "自检超时，可以先进入，稍后在系统状态里看结果。"


# ---------------------------------------------------------------------------
# 纯逻辑：默认值 / 校验 / 落盘
# ---------------------------------------------------------------------------


def _local_appdata_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return (base / "LiuHao" / "data").resolve()


def _probe_writable(path: Path) -> bool:
    """真实写探针：能建目录并写入/删除临时文件才算可写。

    Windows 上 ``os.access`` 对 ACL / 组策略 / 漫游重定向并不可靠，所以
    预填兜底也用和正式校验一样的"真写一次"。
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".liuhao_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_data_dirs() -> list[Path]:
    """D 盘不可用时的候选顺序：用户目录 → 用户主目录 → 公共目录 → 临时目录。"""
    candidates = [_local_appdata_data_dir()]
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    candidates.append((home / "LiuHaoData").resolve())
    public = os.environ.get("PUBLIC")
    if public:
        candidates.append((Path(public) / "LiuHaoData").resolve())
    temp = Path(os.environ.get("TEMP") or tempfile.gettempdir())
    candidates.append((temp / "LiuHao" / "data").resolve())
    return candidates


def _d_drive_usable() -> bool:
    """D 盘是否可用：存在且根目录可写（与安装器的写探针同义）。

    单独一个函数便于验收脚本注入"盘不存在 / 不可写"，不必真删盘符。
    """
    try:
        return D_DRIVE_ROOT.exists() and os.access(D_DRIVE_ROOT, os.W_OK)
    except OSError:
        return False


def default_data_dir() -> Path:
    """首启默认数据目录：D 盘可写就 ``D:\\LiuHaoData``，否则回退用户目录。

    与 ``installer/liuhao.iss`` 的默认值一致；D 盘不存在（或不可写）时不能让
    用户以为数据在 D 盘、实际却失败了。回退到 ``%LOCALAPPDATA%`` 之前会先用
    真写探针确认它可写；如果连它也被组策略/漫游重定向锁住，继续尝试
    ``%USERPROFILE%\\LiuHaoData``、``%PUBLIC%\\LiuHaoData``、临时目录，
    保证第一页一定有一个**能写**的预填值，用户不会卡死在起跑线。
    返回值是规范化的**完整绝对路径**，界面直接显示它，用户仍可改选。
    """
    if _d_drive_usable():
        return DEFAULT_D_DATA_DIR.resolve()
    for candidate in _fallback_data_dirs():
        if _probe_writable(candidate):
            return candidate
    # 全都不可写：仍返回首选位置，由第 1 页给出"换目录"动作，不静默换到别处。
    return _fallback_data_dirs()[0]


def _volume_type(path: Path) -> int | None:
    """返回 Win32 盘类型；非 Windows 或无法判定时返回 ``None``。"""
    if os.name != "nt":
        return None
    try:
        import ctypes

        anchor = path.anchor
        if not anchor:
            return None
        kernel32 = ctypes.windll.kernel32
        kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
        kernel32.GetDriveTypeW.restype = ctypes.c_uint
        return int(kernel32.GetDriveTypeW(anchor))
    except (AttributeError, OSError, ValueError):
        return None


def volume_risk_message(raw: str) -> str:
    """移动盘 / 网络盘上的数据目录风险提示；固定盘或本机目录返回空串。

    只提示、不阻止：用户确实可以把数据放在移动硬盘或网络盘上，但必须先
    知道"设备拔掉或断网后数据会找不到"，否则装完打不开会被当成软件故障。
    """
    text = (raw or "").strip().strip('"')
    if not text:
        return ""
    try:
        kind = _volume_type(Path(text).expanduser())
    except (OSError, ValueError):
        return ""
    if kind == DRIVE_REMOVABLE:
        return (
            "这个目录在可移动磁盘（U 盘 / 移动硬盘）上：设备拔掉后贾维斯会找不到数据，"
            "无法启动。建议改选本机固定磁盘上的目录。"
        )
    if kind == DRIVE_REMOTE:
        return (
            "这个目录在网络盘上：断网或对方关机后贾维斯会找不到数据，无法启动。"
            "建议改选本机固定磁盘上的目录。"
        )
    return ""


def klog_path(data_dir: Path) -> Path:
    """向导自己的日志：即使根本没走到网关，也要有可查的中文记录。"""
    return Path(data_dir) / "logs" / "first_run.log"


def mask_principal(principal: str) -> str:
    """把用户名收敛成可辨认、但不足以还原身份的写法。

    这份日志会随诊断包一起转给外人，用户名属于真实身份信息；排查只需要
    "看到像谁"和"是不是同一个人"，不需要全文。``liuhao`` -> ``li***``。
    不足 3 个字符时整体掩掉——只留两位就等于把名字全给出去了。
    """
    text = (principal or "").strip()
    return f"{text[:2]}***" if len(text) > 2 else "***"


def principal_fingerprint(principal: str) -> str:
    """用户名指纹：同一台机器上对账用（sha256 前 8 位，不可逆）。"""
    text = (principal or "").strip().encode("utf-8")
    return hashlib.sha256(text).hexdigest()[:8]


def _write_log(data_dir: Path, text: str) -> Path | None:
    path = klog_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] {text}\n")
        return path
    except OSError:
        return None


def focus_log_path(data_dir: Path | None = None) -> Path:
    """失败提示里要展示的日志路径（尽量落到真实数据目录）。"""
    if data_dir is not None:
        return klog_path(data_dir)
    return klog_path(default_data_dir())


def validate_password(value: str) -> str | None:
    """返回中文错误说明；``None`` 表示合规。"""
    text = (value or "").strip()
    if not text:
        return "密码不能为空。"
    if len(text) < MIN_PASSWORD_LEN:
        return f"密码太短：至少需要 {MIN_PASSWORD_LEN} 位。"
    if not _PASSWORD_LETTER.search(text):
        return "密码必须包含至少一个英文字母。"
    if not _PASSWORD_DIGIT.search(text):
        return "密码必须包含至少一个数字。"
    return None


def validate_principal(value: str) -> str | None:
    text = (value or "").strip()
    if not text:
        return "管理员账号不能为空。"
    if not _PRINCIPAL.match(text):
        return (
            "账号需以英文字母开头，2–32 位，只能包含字母、数字、点、下划线或连字符。"
        )
    return None


def validate_workbench_name(name_zh: str, name_en: str | None = None) -> str | None:
    """工作台名字校验（与 ``src/brand.py`` 的落盘口径一致）；``None`` 表示合规。

    中文名留空 = 不改名（用出厂默认「贾维斯」），不算错；填了就必须
    1–40 字，英文名可空、填了最多 80 字。
    """
    zh = (name_zh or "").strip()
    if zh and len(zh) > 40:
        return "工作台名字（中文）最多 40 个字。"
    en = (name_en or "").strip()
    if en and len(en) > 80:
        return "工作台英文名最多 80 个字。"
    return None


_INVALID_PATH_CHARS = '<>:"|?*'
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


def _program_files_roots() -> list[Path]:
    """``Program Files`` 系目录（含 32 位重定向），用于拒绝必然失败的落点。"""
    roots: list[Path] = []
    for name in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(name)
        if not value:
            continue
        try:
            root = Path(value).resolve()
        except OSError:
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _path_form_error(resolved: Path) -> str:
    """Windows 路径形式校验：非法字符 / 保留名 / 末尾点或空格。"""
    anchor = resolved.anchor
    for part in resolved.parts:
        if not part or part == anchor or part in (".", ".."):
            continue
        if any(ch in _INVALID_PATH_CHARS for ch in part) or any(
            ord(ch) < 32 for ch in part
        ):
            return (
                "路径里不能包含 < > : \" | ? * 或控制字符（盘符后的冒号除外）。"
                "请改名后重试。"
            )
        if part.endswith(".") or part.endswith(" "):
            return (
                f"目录名“{part}”末尾有点或空格：Windows 会悄悄去掉，数据会落到"
                "别的目录。请改名后重试。"
            )
        base = part.rstrip(" .").split(".")[0].upper()
        if base in _RESERVED_NAMES:
            return (
                f"“{part}”是 Windows 保留名（CON、PRN、AUX、NUL、COM1–9、LPT1–9），"
                "带后缀也不行。请换一个名字。"
            )
    return ""


def _inside_program_files(resolved: Path) -> bool:
    for root in _program_files_roots():
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def validate_data_dir(raw: str) -> tuple[Path | None, str]:
    """把用户输入变成可用的数据目录；返回 ``(路径, 中文错误)``。

    只做真实可写性验证（建目录 + 写探测文件），不猜磁盘配额。程序目录会
    在升级时被整体覆盖、``Program Files`` 会被虚拟化/权限拦住，因此明确
    拒绝；非法字符、保留名、末尾点/空格、超长路径、同名文件都会在第 1 页
    直接给出中文原因，不让用户走到最后一步才失败。
    """
    text = (raw or "").strip('"')
    if not text.strip():
        return None, "数据目录不能为空。"
    text = os.path.expandvars(text)
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, "请填写完整路径（例如 D:\\LiuHaoData）。"
    # 先在"原始输入"上查形式问题：``resolve`` 会把已存在目录的末尾点/空格
    # 归一化掉（``D:\LiuHaoData.`` 变成 ``D:\LiuHaoData``），那时就查不出来了。
    form_error = _path_form_error(candidate)
    if form_error:
        return None, form_error
    try:
        resolved = candidate.resolve()
    except OSError:
        return None, f"无法解析数据目录：{candidate}"

    if max(len(str(candidate)), len(str(resolved))) >= 260:
        return None, (
            f"路径太长（{len(str(resolved))} 个字符）。Windows 默认只支持 260 个字符"
            "以内的路径（实际可用 259），请把数据目录放到更浅的位置，"
            "例如 D:\\LiuHaoData。"
        )
    form_error = _path_form_error(resolved)
    if form_error:
        return None, form_error
    if _inside_program_files(resolved):
        return None, (
            "数据目录不能放在 Program Files / Program Files (x86) 里：系统会做"
            "写入虚拟化或直接拒绝，装完必然打不开。请改到 D:\\LiuHaoData 或用户"
            "目录（例如 %LOCALAPPDATA%\\LiuHao\\data）。"
        )

    try:
        resolved.relative_to(APP_ROOT.resolve())
    except ValueError:
        pass
    else:
        return None, "数据目录不能放在程序安装目录里（升级会覆盖），请另选一个位置。"

    if resolved.exists() and not resolved.is_dir():
        return None, (
            f"这个路径已经存在，但不是文件夹：{resolved}。"
            "请换一个目录名，或直接选择已有的文件夹。"
        )

    try:
        resolved.mkdir(parents=True, exist_ok=True)
        probe = resolved / ".liuhao_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return None, (
            f"数据目录不可写：{resolved}（{type(exc).__name__}: {exc}）。"
            "请换一个位置，或确认磁盘没有写满。"
        )
    return resolved, ""


def sync_uninstall_data_dir(data_dir: Path) -> str | None:
    """把数据目录同步到卸载键的 ``DataDir``（覆盖升级 / 卸载据此沿用）。

    安装器在安装结束时会写这个值；用户在首启向导里改了目录，如果不同步，
    下一次覆盖安装就会按旧值回落到旧目录，用户看到的是「升级完东西全没了」。
    只在真实安装布局下生效（标记文件位于程序目录、且 ``LiuHao.bat`` 存在），
    测试/源码模式改写了 ``DATA_DIR_MARKER`` 时不会触碰注册表。
    """
    if os.name != "nt":
        return None
    marker = APP_ROOT / "data-dir.txt"
    try:
        if Path(DATA_DIR_MARKER).resolve() != marker.resolve():
            return None
    except OSError:
        return None
    if not (APP_ROOT / "LiuHao.bat").exists():
        return None
    target = str(Path(data_dir))
    app_root = str(APP_ROOT).rstrip("\\/").lower()
    try:
        import winreg
    except ImportError:  # pragma: no cover - 仅 Windows 有
        return None
    roots = (
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, root in roots:
        try:
            key = winreg.OpenKey(hive, root)
        except OSError:
            continue
        try:
            count = winreg.QueryInfoKey(key)[0]
            for index in range(count):
                try:
                    name = winreg.EnumKey(key, index)
                except OSError:
                    continue
                if not name.endswith("_is1"):
                    continue
                try:
                    sub = winreg.OpenKey(
                        key, name, 0, winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
                    )
                except OSError:
                    continue
                try:
                    try:
                        location = str(winreg.QueryValueEx(sub, "InstallLocation")[0])
                    except OSError:
                        location = ""
                    if location.rstrip("\\/").lower() != app_root:
                        continue
                    try:
                        winreg.SetValueEx(sub, "DataDir", 0, winreg.REG_SZ, target)
                    except OSError:
                        continue
                    hive_name = "HKCU" if hive == winreg.HKEY_CURRENT_USER else "HKLM"
                    return f"{hive_name}\\{root}\\{name}"
                finally:
                    sub.Close()
        finally:
            key.Close()
    return None


def write_data_dir_marker(data_dir: Path) -> None:
    """写程序目录下的 ``data-dir.txt``（UTF-8 带 BOM，启动器按此还原路径）。

    安装器在安装时也写同一份文件；用户在向导里改了目录就必须同步更新，否则
    下次双击仍会回到旧目录。卸载键 ``DataDir`` 同步更新，覆盖安装才会沿用
    这次选择，而不是回落到安装时的旧值。
    """
    DATA_DIR_MARKER.write_text("\ufeff" + str(Path(data_dir)) + "\n", encoding="utf-8")
    key = sync_uninstall_data_dir(data_dir)
    if key:
        _write_log(data_dir, f"已同步卸载键 DataDir = {data_dir}（{key}）")


def _recommended_free_port(preferred: int) -> int:
    """给用户推荐一个当前确实空闲的端口，而不是随口报一个数。"""
    from src.desktop.probe import pick_port

    starts = [preferred]
    if preferred != 8081:
        starts.append(8081)
    for start in starts:
        try:
            return pick_port(start, 100)
        except RuntimeError:
            continue
    return preferred


def check_port(host: str, port: int) -> str | None:
    """端口被占用时返回中文说明；可用返回 ``None``。

    用 ``port_is_free`` 同时探 IPv4 通配、回环与 IPv6：只绑 ``127.0.0.1``
    的探测会把监听 ``0.0.0.0`` 的占用者误判成空闲。
    """
    from scripts import start_liuhao as launcher
    from src.desktop.probe import port_is_free

    if port_is_free(port, host):
        return None
    owner = launcher._describe_port_owner(port)
    detail = f"（占用者：{owner}）" if owner else ""
    suggested = _recommended_free_port(port + 1 if port < 65535 else 8081)
    return (
        f"这个端口 {port} 被占用{detail}。换一个（推荐 {suggested}），"
        "或者关掉占用它的程序。"
    )


def chosen_api_port() -> int | None:
    """向导里确定的服务端口（``commit`` 成功后会写进 ``LIUHAO_API_PORT``）。"""
    raw = (os.environ.get("LIUHAO_API_PORT") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def prepare_store_env(data_dir: Path) -> Path:
    """把身份 / 凭据 / 模型配置指到 ``data_dir``，并确保完整性密钥可用。

    与 ``scripts/launch_installed.py`` 共用同一份路径映射，避免两边漂移。
    """
    from scripts import launch_installed as installed
    from src.common.integrity_key import ensure_integrity_key

    for rel in ("config", "workspace", "data", "logs", "data/observability"):
        (Path(data_dir) / rel).mkdir(parents=True, exist_ok=True)
    os.environ[DATA_DIR_ENV] = str(data_dir)
    for key, value in installed.data_path_env(Path(data_dir)).items():
        os.environ[key] = str(value)
    key = ensure_integrity_key(data_dir=Path(data_dir))
    os.environ["LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY"] = key
    return Path(data_dir)


def needs_setup() -> bool:
    """当前数据目录是否还没有可登录的管理员（与 CLI 向导判定完全一致）。"""
    from scripts import first_run_wizard as cli

    return cli.needs_account_setup()


def chosen_data_dir() -> Path | None:
    """向导实际选定的数据目录（未完成时为 ``None``）。"""
    raw = (os.environ.get(DATA_DIR_ENV) or "").strip()
    return Path(raw) if raw else None


def clear_current_url(data_dir: Path) -> None:
    """首启未完成前删掉旧地址文件，避免用户照着一个连不上的地址操作。"""
    with contextlib.suppress(OSError):
        (Path(data_dir) / "config" / "current_url.txt").unlink(missing_ok=True)


def _same_dir(left: Path, right: Path) -> bool:
    """Windows 大小写不敏感；用规范化字符串比较，避免 resolve() 差异误判。"""
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(
        os.path.abspath(str(right))
    )


def _env_api_port() -> int:
    """默认端口：允许运维用 ``LIUHAO_API_PORT`` 覆盖（与启动器保持一致）。"""
    try:
        return int((os.environ.get("LIUHAO_API_PORT") or "").strip() or 8080)
    except ValueError:
        return 8080


def _persist_api_port(data_dir: Path, port: int) -> None:
    """把选定的端口落到桌面端约定的位置（``<数据目录>/config/desktop_port.json``）。

    只调 ``src.desktop.service`` 的持久化接口，不在这里另造一份配置：下次启动
    时桌面端按「环境变量 → 这份文件 → 8080」的顺序取值。写失败不把首启判死
    （账号已建好、本次启动仍用环境变量），但必须留下可查的中文日志。
    """
    from src.desktop.service import save_saved_port

    try:
        target = save_saved_port(port)
    except Exception as exc:  # noqa: BLE001 - 端口配置写失败不能阻断首启
        _write_log(
            data_dir,
            f"端口 {port} 未能持久化：{type(exc).__name__}: {exc}；"
            "下次启动可能回落到 8080。",
        )
        return
    _write_log(data_dir, f"端口 {port} 已持久化：{target}")


def _fail(data_dir: Path | None, message: str, *, detail: str = "") -> CommitResult:
    """返回「一句人话 + 日志路径」；技术细节只进日志，不上界面。"""
    log = focus_log_path(data_dir)
    _write_log(log.parent.parent, detail or message)
    return False, f"{message}\n日志：{log}"


# ---------------------------------------------------------------------------
# 提交：把表单值真正写进数据目录
# ---------------------------------------------------------------------------


def _resolve_port(values: dict[str, Any], port: int | None) -> int | None:
    """确定本次要预检的端口：显式参数 > 表单里的端口 > 环境变量 > 8080。"""
    if port is not None:
        return int(port)
    raw = values.get("api_port")
    if raw not in (None, ""):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return _env_api_port()


def commit(
    values: dict[str, Any],
    *,
    port: int | None = None,
    recovery_out: list[str] | None = None,
) -> CommitResult:
    """校验并执行首启动作（不依赖窗口，可被测试直接调用）。

    ``values`` 字段：``data_dir``、``principal``、``display``、``password``、
    ``model``（``skip`` / ``ollama`` / ``cloud``）、``api_port``（可选）、
    ``workbench_name_zh`` / ``workbench_name_en``（可选：工作台名字；
    留空或与出厂默认相同就不写配置文件，填了新名字则在建账号之前先
    落盘到数据目录的 ``workspace_settings.json``，顶栏 / 登录页 /
    助手名随后都读它）以及模型相关字段。失败时返回「中文原因 +
    日志路径」，技术细节只写日志。``recovery_out`` 收集管理员恢复码
    明文，供图形向导当场「只展示一次」；其余调用方（CLI / 测试）不传
    即可，恢复码仍会由 CLI 打印。
    """
    current = chosen_data_dir()
    dir_value, dir_error = validate_data_dir(str(values.get("data_dir") or ""))
    if dir_error or dir_value is None:
        return _fail(current, dir_error or "数据目录不可用。")

    principal_error = validate_principal(str(values.get("principal") or ""))
    if principal_error:
        return _fail(dir_value, principal_error)
    password_error = validate_password(str(values.get("password") or ""))
    if password_error:
        return _fail(dir_value, password_error)

    # 工作台名字先校验（不合规则在建账号之前就拦住，不留半个账号）。
    name_zh = str(values.get("workbench_name_zh") or "").strip()
    name_en = str(values.get("workbench_name_en") or "").strip()
    name_error = validate_workbench_name(name_zh, name_en)
    if name_error:
        return _fail(dir_value, name_error)
    rename = bool(name_zh) and (
        name_zh != BRAND_NAME_ZH or (name_en and name_en != BRAND_NAME_EN)
    )

    api_port = _resolve_port(values, port)
    if api_port is None:
        return _fail(dir_value, "端口必须是 1–65535 之间的数字。")
    if api_port != 0 and not 1 <= api_port <= 65535:
        return _fail(dir_value, f"端口 {api_port} 不在 1–65535 范围内，请换一个。")
    if api_port != 0:
        port_error = check_port("127.0.0.1", api_port)
        if port_error:
            return _fail(dir_value, port_error)

    # 换目录时旧密钥不能带过去：新目录要生成自己的密钥，否则两份数据共用一把。
    same_dir = current is not None and _same_dir(current, dir_value)
    if not same_dir:
        os.environ.pop("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", None)

    try:
        # 程序目录可能只读（装在 Program Files）：目录没变就不要写 marker，
        # 否则一个不需要改动的首启也会因为 marker 写不进去而失败。
        if not same_dir:
            write_data_dir_marker(dir_value)
        prepare_store_env(dir_value)
    except Exception as exc:  # noqa: BLE001 - 落盘失败要变成中文提示
        return _fail(
            dir_value,
            f"无法准备数据目录 {dir_value}：请确认目录可写、磁盘没有写满，或换一个目录后重试。",
            detail=f"prepare_store_env failed: {type(exc).__name__}: {exc}",
        )

    # 工作台改名（只在用户真的起了新名字时写；默认贾维斯不写文件）。
    # prepare_store_env 已把 LIUHAO_DATA_DIR 指到本次数据目录，所以
    # workspace_settings.json 正好落在该目录里，随数据目录一起备份/迁移。
    if rename:
        from src.brand import set_workspace_name

        try:
            set_workspace_name(name_zh, name_en or None)
        except ValueError as exc:
            return _fail(dir_value, f"工作台名字保存失败：{exc}")

    from scripts import first_run_wizard as cli

    principal = str(values.get("principal") or "").strip()
    display = str(values.get("display") or "").strip() or principal
    try:
        rc = cli.register_admin(
            principal, display, str(values.get("password") or ""),
            recovery_out=recovery_out,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            dir_value,
            "创建管理员账号失败，请重试；若仍失败请更换数据目录。",
            detail=f"register_admin failed: {type(exc).__name__}: {exc}",
        )
    if rc != 0:
        return _fail(
            dir_value,
            "创建管理员账号未通过校验，请检查账号与密码后重试（详见日志）。",
            detail=f"register_admin returned {rc}",
        )

    mode = str(values.get("model") or "skip")
    if mode == "ollama":
        model_name = str(values.get("model_name") or "").strip()
        if not model_name:
            return _fail(dir_value, "请填写 Ollama 模型名（例如 qwen2.5:7b）。")
        try:
            cli.configure_ollama(
                model_name,
                str(values.get("base_url") or OLLAMA_DEFAULT_BASE_URL).strip(),
            )
        except Exception as exc:  # noqa: BLE001
            return _fail(
                dir_value,
                "登记本机 Ollama 模型失败：请确认 Ollama 已安装并正在运行，或先跳过（稍后可在「系统设置 → 模型管理」添加）。",
                detail=f"configure_ollama failed: {type(exc).__name__}: {exc}",
            )
    elif mode == "cloud":
        base_url = str(values.get("base_url") or "").strip()
        model_name = str(values.get("model_name") or "").strip()
        api_key = str(values.get("api_key") or "").strip()
        if not (base_url and model_name and api_key):
            return _fail(
                dir_value,
                "云端模型需要完整填写 base_url、模型名和 API Key；也可以先跳过，稍后再配。",
            )
        try:
            cli.configure_openai_compatible(base_url, model_name, api_key)
        except Exception as exc:  # noqa: BLE001
            return _fail(
                dir_value,
                "登记云端模型失败：请检查 base_url / 模型名 / API Key 是否正确；也可以先跳过。",
                detail=f"configure_openai_compatible failed: {type(exc).__name__}: {exc}",
            )

    # 让启动器后续使用的正是这次校验通过的端口（不自动顺延）。0 = 由系统挑，
    # 只出现在测试/显式要求里，不往环境变量里写。
    if api_port:
        os.environ["LIUHAO_API_PORT"] = str(api_port)
        _persist_api_port(dir_value, api_port)
    clear_current_url(dir_value)
    _write_log(
        dir_value,
        "first-run done:"
        f" principal={mask_principal(principal)}"
        f" principal_hash={principal_fingerprint(principal)}"
        f" model={mode} port={api_port}"
        + (f" workbench_name={name_zh}" if rename else ""),
    )
    return True, "设置完成，正在启动贾维斯窗口…"


# ---------------------------------------------------------------------------
# 完成页自检：四项当场检查 + 可点击的下一步
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckResult:
    """一条自检结果。

    ``action`` 是该项目失败时唯一可点击的下一步；``blocking`` 表示"必须先
    处理"（否则这次打不开控制台），否则是"可以稍后再弄，软件已可用"。
    ``code`` 只进日志，用与 ``evt=startup.check`` 相同的词汇（``free`` /
    ``serving`` / ``occupied`` / ``ok`` / ``skipped`` / ``failed`` 等）。
    """

    key: str
    title: str
    ok: bool
    detail: str
    action: str = ""
    blocking: bool = False
    advice: str = ""
    code: str = ""
    log_detail: str = ""

    def as_log_fields(self) -> str:
        detail = (self.log_detail or self.detail).replace('"', "'").replace("\n", " ")
        return (
            f"check_{self.key}={self.code or ('ok' if self.ok else 'failed')} "
            f"check_{self.key}_ok={_flag(self.ok)} "
            f'check_{self.key}_detail="{detail}"'
        )


def _flag(value: Any) -> str:
    """与 ``boot_report._flag`` 同口径：``1`` / ``0`` / ``unknown``。"""
    if value is None:
        return "unknown"
    return "1" if bool(value) else "0"


def check_port_ready(host: str, port: int) -> CheckResult:
    """网关端口：可绑定，或已被本机贾维斯监听（两种情况都可以直接使用）。"""
    from src.desktop.probe import http_json, port_is_free

    if port_is_free(port, host):
        return CheckResult(
            key="port",
            title="网关端口",
            ok=True,
            detail=f"端口 {port} 可用（没有其他程序占用）。",
            code="free",
        )
    info = http_json(f"http://{host}:{port}/v1/health", timeout=0.8)
    body = info.get("body") if isinstance(info.get("body"), dict) else {}
    if info.get("ok") and body.get("service") == "liuhao-gateway":
        return CheckResult(
            key="port",
            title="网关端口",
            ok=True,
            detail=f"端口 {port} 上已有贾维斯在运行，可以直接打开贾维斯窗口。",
            code="serving",
        )
    suggested = _recommended_free_port(port + 1 if port < 65535 else 8081)
    return CheckResult(
        key="port",
        title="网关端口",
        ok=False,
        detail=(
            f"这个端口 {port} 被占用；换一个（推荐 {suggested}），"
            "或者关掉占用它的程序。"
        ),
        action="换一个端口",
        blocking=False,
        advice="可以稍后再弄，软件已可用：启动时还会再提示一次，也可以现在就点按钮改好。",
        code="occupied",
    )


def check_data_dir_writable(data_dir: Path) -> CheckResult:
    """数据目录：真的写一个临时文件再删掉，并展示**实际路径**。"""
    from src.observability.boot_report import probe_writable

    ok, exc = probe_writable(data_dir)
    if ok:
        return CheckResult(
            key="data_dir",
            title="数据目录",
            ok=True,
            detail=f"可写入：{data_dir}",
            code="ok",
        )
    return CheckResult(
        key="data_dir",
        title="数据目录",
        ok=False,
        detail=f"现在无法写入：{data_dir}（可能磁盘已满、目录被设为只读，或被安全软件拦截）。",
        action="打开数据目录",
        blocking=True,
        advice="必须先处理：账号和对话都写不进这个目录，贾维斯窗口打不开。处理好后点下方「重新检测全部四项」。",
        code="unwritable",
        log_detail=f"{type(exc).__name__}: {exc}" if exc else "",
    )


def check_tray_resident(data_dir: Path, *, tray_planned: bool) -> CheckResult:
    """托盘：已有实例在托盘里运行，或本次启动后会自动驻留。

    「已驻留」以操作系统级单实例锁为准（进程活着就持有，崩溃自动释放），
    元信息只用来展示地址；没有托盘时如实说明"关窗会停服务"，不写成通过。
    """
    from src.common.instance_lock import InstanceLock, instance_root

    holder = InstanceLock(instance_root(data_dir)).probe()
    if holder:
        url = str(holder.get("url") or "").strip()
        where = f"（{url}）" if url else ""
        return CheckResult(
            key="tray",
            title="托盘驻留（关窗后继续运行）",
            ok=True,
            detail=f"已在托盘运行{where}；关闭这个窗口不会停止服务。",
            code="resident",
        )
    if not tray_planned:
        return CheckResult(
            key="tray",
            title="托盘驻留（关窗后继续运行）",
            ok=False,
            detail="这次从命令窗口启动，没有托盘；关闭启动窗口会停止服务，请先保留它。",
            action="重新检测",
            blocking=False,
            advice="可以稍后再弄，软件已可用：装好桌面版后启动会自动驻留托盘。",
            code="not_planned",
        )
    try:
        from src.desktop.tray import SystemTray  # noqa: F401 - 只验证组件可加载
    except Exception as exc:  # noqa: BLE001 - 缺组件要变成可读提示
        return CheckResult(
            key="tray",
            title="托盘驻留（关窗后继续运行）",
            ok=False,
            detail="托盘组件不可用；关闭窗口会停止服务，请先保留启动窗口。",
            action="重新检测",
            blocking=False,
            advice="可以稍后再弄，软件已可用：重新安装桌面版可以修好托盘。",
            code="unavailable",
            log_detail=f"{type(exc).__name__}: {exc}",
        )
    return CheckResult(
        key="tray",
        title="托盘驻留（关窗后继续运行）",
        ok=True,
        detail=(
            "托盘组件可用；启动后会在托盘显示，关闭窗口后服务继续运行"
            "（本次只验证组件能加载，不启动托盘）。"
        ),
        code="planned",
    )


def check_default_model(expected_name: str = "") -> CheckResult:
    """默认模型：真的发一次最小请求，通不通以这次结果为准。"""
    from src.ai.model_config import get_model_config_store

    store = get_model_config_store()
    config: dict[str, Any] | None = None
    resolved_by_name = False
    if expected_name:
        with contextlib.suppress(Exception):
            config = store.resolve(expected_name)
            resolved_by_name = config is not None
    if config is None:
        with contextlib.suppress(Exception):
            config = store.resolve()
    if not config:
        return CheckResult(
            key="model",
            title="默认模型",
            ok=False,
            detail="本次选择了跳过，之后可在「系统设置 → 模型管理」配置。",
            action="去模型页测试连接",
            blocking=False,
            advice="不阻塞进入控制台：账号和数据已就绪；需要真实回复时再配置模型。",
            code="skipped",
        )

    model_name = str(config.get("model") or "")
    started = time.monotonic()
    try:
        from src.ai.providers import provider_from_config

        probe_config = dict(config)
        probe_config["timeout"] = min(float(probe_config.get("timeout") or 10.0), 10.0)
        provider = provider_from_config(probe_config)
        reply = provider.generate("ping", max_tokens=8, temperature=0.0)
        if not str(reply or "").strip():
            raise RuntimeError("模型没有返回内容")
    except Exception as exc:  # noqa: BLE001 - 任何失败都变成可读提示
        with contextlib.suppress(Exception):
            store.mark_verified(config["id"], False, actor="first-run-wizard")
        return CheckResult(
            key="model",
            title="默认模型",
            ok=False,
            detail=f"默认模型 {model_name} 测试连接失败。请检查地址、模型名或 API Key，也可以换一个模型。",
            action="去模型页测试连接",
            blocking=False,
            advice="可以稍后再弄，软件已可用：账号和数据都已就绪，聊天要等模型连通后才能用。",
            code="failed",
            log_detail=f"{type(exc).__name__}: {exc}",
        )

    with contextlib.suppress(Exception):
        store.mark_verified(config["id"], True, actor="first-run-wizard")
        if resolved_by_name:
            store.set_default(config["id"], "first-run-wizard")
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return CheckResult(
        key="model",
        title="默认模型",
        ok=True,
        detail=f"默认模型 {model_name} 连接正常（{elapsed_ms} 毫秒）。",
        code="ok",
    )


def run_self_check(
    *, data_dir: Path, port: int, model_name: str = "", tray_planned: bool = False
) -> list[CheckResult]:
    """依次做四项当场检查（托盘一项只验证组件可加载）；任何一项异常都变成"需要处理"，不中断其余检查。"""
    results: list[CheckResult] = []
    for probe in (
        lambda: check_port_ready("127.0.0.1", int(port)),
        lambda: check_data_dir_writable(Path(data_dir)),
        lambda: check_tray_resident(Path(data_dir), tray_planned=tray_planned),
        lambda: check_default_model(model_name),
    ):
        try:
            results.append(probe())
        except Exception as exc:  # noqa: BLE001 - 自检本身绝不能把界面卡死
            key = ("port", "data_dir", "tray", "model")[len(results)]
            title = {
                "port": "网关端口",
                "data_dir": "数据目录",
                "tray": "托盘驻留（关窗后继续运行）",
                "model": "默认模型",
            }[key]
            results.append(
                CheckResult(
                    key=key,
                    title=title,
                    ok=False,
                    detail="这项检查没能完成；请点「重新检测」再试一次。",
                    action="重新检测",
                    blocking=False,
                    advice="可以稍后再弄，软件已可用。",
                    code="error",
                    log_detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def self_check_log_line(
    *, data_dir: Path, port: int, results: list[CheckResult]
) -> str:
    """生成一行 ``evt=startup.check``，字段名与启动自检日志完全一致。

    与排障日志的差别只有一处：这里写**实际路径**（这份日志只在本机数据
    目录里，用户报问题时需要直接看懂位置），不做 ``<本地路径>`` 脱敏。
    """
    from src.common.instance_lock import instance_root
    from src.observability.boot_report import (
        port_state,
        probe_writable,
        provider_summary,
    )

    by_key = {item.key: item for item in results}
    log_dir = Path(data_dir) / "logs"
    bindable, state = port_state("127.0.0.1", int(port))
    data_dir_ok = by_key["data_dir"].ok if "data_dir" in by_key else False
    log_ok, _ = probe_writable(log_dir)
    summary = provider_summary()
    console_dist = (os.environ.get("LIUHAO_CONSOLE_DIST") or "").strip()
    console_index = str(Path(console_dist) / "index.html") if console_dist else ""
    tray_ok = by_key["tray"].ok if "tray" in by_key else False
    model_state = by_key["model"].code if "model" in by_key else "unknown"
    blocking = any(item.blocking and not item.ok for item in results)
    checks = " ".join(item.as_log_fields() for item in results)
    return (
        "evt=startup.check source=first_run "
        f"data_dir={data_dir} data_writable={_flag(data_dir_ok)} "
        f"log_dir={log_dir} log_writable={_flag(log_ok)} "
        "db=[] db_open=1 "
        f"host=127.0.0.1 port={int(port)} port_bindable={_flag(bindable)} "
        f"port_state={state} console_dist={console_dist or 'unset'} "
        f"console_index={console_index or 'unset'} "
        f"provider={summary.get('provider')} model={summary.get('model')} "
        f"key_fp={summary.get('key_fp')} provider_source={summary.get('source')} "
        f"tray_resident={_flag(tray_ok)} model_test={model_state} "
        f"blocking={_flag(blocking)} check_result="
        f"{'needs_attention' if any(not item.ok for item in results) else 'all_green'} "
        f"instance_root={instance_root(data_dir)} {checks}"
    )


def write_self_check_log(
    data_dir: Path, results: list[CheckResult], *, port: int
) -> Path | None:
    """把四项结果写进 ``first_run.log``（与 ``evt=startup.check`` 同字段）。"""
    line = self_check_log_line(data_dir=Path(data_dir), port=port, results=results)
    return _write_log(Path(data_dir), line)


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------

#: 测试可替换 UI 工厂；签名 ``(default_dir, on_submit, log_path, port) -> UI``。
UIFactory = Callable[[Path, Callable[[dict[str, Any]], CommitResult], Path, int], Any]


class _WizardWindow:
    """五步式窗口：数据目录 → 管理员账号 → 模型（含服务端口）→ 确认 → 自检结果。

    点击「完成并自检」时调用注入的 ``on_submit``；失败只在窗口内显示中文原因，
    窗口不关闭，并给出「重试 / 换目录 / 打开日志目录 / 改用其他端口」等按钮；
    成功后进入自检页，四项结果全部来自本次当场检查（托盘一项为组件可加载、
    启动后将驻留）；失败项各自带一个可点击的下一步；用户确认后才关闭窗口，
    由启动器继续拉起控制台。
    """

    def __init__(
        self,
        default_dir: Path,
        on_submit: Callable[[dict[str, Any]], CommitResult],
        log_path: Path,
        port: int = 8080,
    ) -> None:
        import tkinter as tk
        from tkinter import filedialog, ttk

        from src.desktop import dpi as dpi_support

        self._tk = tk
        self._dpi_support = dpi_support
        self._on_submit = on_submit
        self._log_path = log_path
        self._submitted = False
        self.committed = False
        self.accepted = False
        self.canceled = False
        self.error_message = ""
        self.port = int(port)
        self.data_dir = Path(default_dir)
        #: 用户已确认过风险的移动盘/网络盘路径；同一个路径不重复弹窗。
        self._volume_ack = ""
        self.tray_planned = False
        self._model_name = ""
        self._model_mode = "skip"
        self._check_results: list[CheckResult] = []
        self._check_running = False
        #: 自检还没结束就点了「进入贾维斯」：排队，等自检收尾后自动进入。
        self._finish_pending = False
        #: 自检的开始时间与次数，只用于日志与超时兜底（不含任何凭据）。
        self._check_seq = 0
        self._check_started_at = 0.0
        self._check_poll_failures = 0
        self._ready = False
        self._wrapped_labels: list[tuple[Any, int]] = []

        # 必须在 tk.Tk() 之前声明：晚一步进程已被系统按 96 DPI 虚拟化。
        dpi_support.enable_dpi_awareness()
        self.root = tk.Tk()
        self._dpi = dpi_support.apply_tk_scaling(self.root)
        self.root.title("贾维斯首次设置")
        self.root.protocol("WM_DELETE_WINDOW", self._cancel)
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))

        self.dir_var = tk.StringVar(value=str(default_dir))
        self.principal_var = tk.StringVar(value="liuhao")
        self.display_var = tk.StringVar(value="")
        self.password_var = tk.StringVar()
        self.confirm_var = tk.StringVar()
        # 工作台名字：默认就是出厂名（贾维斯 / Jarvis）；用户改了才写配置。
        self.workbench_name_zh_var = tk.StringVar(value=BRAND_NAME_ZH)
        self.workbench_name_en_var = tk.StringVar(value=BRAND_NAME_EN)
        self.mode_var = tk.StringVar(value="skip")
        self.ollama_model_var = tk.StringVar(value="qwen2.5:7b")
        self.ollama_url_var = tk.StringVar(value=OLLAMA_DEFAULT_BASE_URL)
        self.cloud_url_var = tk.StringVar(value=CLOUD_DEFAULT_BASE_URL)
        self.cloud_model_var = tk.StringVar(value=CLOUD_DEFAULT_MODEL)
        self.cloud_key_var = tk.StringVar()
        self.port_var = tk.StringVar(value=str(self.port))
        self.summary_var = tk.StringVar()

        self._pages: list[Any] = []
        self._index = 0
        self._build(ttk, filedialog)
        self._show(0)
        self._wrapped_labels = self._collect_wraplabels()
        self._apply_geometry(center=True)
        self._dpi_watcher = dpi_support.DpiWatcher(self.root, self._on_dpi_changed)
        self._dpi_watcher.start()

    # -- 布局 ---------------------------------------------------------------
    def _build(self, ttk: Any, filedialog: Any) -> None:
        header = self._tk.Label(
            self.root,
            text="贾维斯首次设置",
            font=("Microsoft YaHei UI", 16, "bold"),
            anchor="w",
        )
        header.pack(fill="x", padx=20, pady=(16, 4))
        self._tk.Label(
            self.root,
            text="完成这几步即可使用；最后一页会再确认数据位置与联网边界。",
            fg="#555555",
            anchor="w",
            wraplength=620,
            justify="left",
        ).pack(fill="x", padx=20, pady=(0, 10))

        # 页面内容放进可滚动区域：高缩放 / 小屏时窗口被钳制在工作区内，
        # 底部「上一步 / 取消 / 下一步」仍然固定可见。
        self._scroll = self._dpi_support.ScrollableFrame(self.root)
        self._scroll.frame.pack(fill="both", expand=True, padx=20)
        body = self._scroll.inner
        self._pages = [
            self._page_dir(body, filedialog),
            self._page_account(body),
            self._page_model(body, ttk),
            self._page_summary(body),
            self._page_check(body),
        ]

        self.error_label = self._tk.Label(
            self.root, text="", fg="#b00020", anchor="w", justify="left", wraplength=620
        )
        self.error_label.pack(fill="x", padx=20, pady=(4, 0))

        # 失败时的可点击动作：任何错误都给人一条路走，而不是只有一段文字。
        self._actions = self._tk.Frame(self.root)
        self._retry_button = self._tk.Button(
            self._actions, text="重试", width=8, command=self._retry
        )
        self._change_dir_button = self._tk.Button(
            self._actions, text="换目录", width=10, command=self._change_dir
        )
        self._other_port_button = self._tk.Button(
            self._actions, text="改用其他端口", width=14, command=self._use_other_port
        )
        self._log_button = self._tk.Button(
            self._actions, text="打开日志目录", width=14, command=self._open_log_dir
        )

        buttons = self._tk.Frame(self.root)
        buttons.pack(fill="x", padx=20, pady=(8, 16))
        self._back = self._tk.Button(buttons, text="上一步", width=10, command=self._back_step)
        self._back.pack(side="left")
        self._cancel = self._tk.Button(buttons, text="取消", width=10, command=self._cancel)
        self._cancel.pack(side="left", padx=(8, 0))
        self._next = self._tk.Button(buttons, text="下一步", width=16, command=self._next_step)
        self._next.pack(side="right")

        # 底部按钮优先占位：窗口被压到工作区高度时先压缩可滚动内容，而不是
        # 把「下一步」挤出屏幕。重新 pack 以固定"按钮 → 错误区 → 内容区"的顺序。
        self._scroll.frame.pack_forget()
        self.error_label.pack_forget()
        buttons.pack_forget()
        buttons.pack(side="bottom", fill="x", padx=20, pady=(8, 16))
        self.error_label.pack(side="bottom", fill="x", padx=20, pady=(4, 0))
        self._scroll.frame.pack(fill="both", expand=True, padx=20)

        self.root.bind("<Return>", lambda _e: self._next_step())

    # -- 尺寸 / DPI ---------------------------------------------------------
    def _collect_wraplabels(self) -> list[tuple[Any, int]]:
        """记录自动换行标签的 96 DPI 基准宽度，缩放时等比调整。"""
        collected: list[tuple[Any, int]] = []
        stack: list[Any] = [self.root]
        while stack:
            widget = stack.pop()
            stack.extend(widget.winfo_children())
            try:
                base = int(widget.cget("wraplength"))
            except Exception:  # noqa: BLE001 - 控件没有 wraplength 选项
                continue
            if base > 0:
                collected.append((widget, base))
        return collected

    def _apply_wraplength(self, dpi_value: int) -> None:
        for widget, base in self._wrapped_labels:
            try:
                widget.configure(wraplength=self._dpi_support.scaled(base, dpi_value))
            except Exception:  # noqa: BLE001 - 控件可能已销毁
                continue

    def _apply_geometry(self, *, center: bool, dpi_value: int | None = None) -> None:
        """按内容自然尺寸开窗，并钳制在当前显示器工作区内。

        内容放不下时由滚动区域承接，底部按钮始终留在屏内（200% 缩放下
        旧的固定坐标会把「下一步」推到屏幕外）。
        """
        try:
            current = int(
                dpi_value or self._dpi or self._dpi_support.window_dpi(self.root.winfo_id())
            )
            self._apply_wraplength(current)
            self._scroll.prepare()
            self._dpi_support.fit_window(
                self.root, min_width=520, min_height=360, dpi=current, center=center
            )
            self._ready = True
        except Exception:  # noqa: BLE001 - 尺寸计算失败不能挡住向导
            if not self._ready:
                self.root.geometry("680x460")

    def _on_dpi_changed(self, dpi_value: int) -> None:
        self._dpi = int(dpi_value)
        self._apply_wraplength(self._dpi)
        try:
            self.root.after_idle(
                lambda: self._apply_geometry(center=False, dpi_value=dpi_value)
            )
        except Exception:  # noqa: BLE001 - 窗口正在关闭
            pass

    def _stop_dpi_watcher(self) -> None:
        watcher = getattr(self, "_dpi_watcher", None)
        if watcher is not None:
            watcher.stop()
            self._dpi_watcher = None

    def _page_dir(self, parent: Any, filedialog: Any) -> Any:
        frame = self._tk.Frame(parent)
        self._tk.Label(
            frame, text="第 1 步 · 数据目录", font=("Microsoft YaHei UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self._tk.Label(
            frame,
            text=(
                "账号、对话、知识库、记忆和审计记录都会写进这个目录。\n"
                "· 重装或升级程序不会动它；换电脑时把整个目录复制过去即可继续使用。\n"
                "· 只有 API Key 加密保存；对话、知识库、审计是本地明文 SQLite，"
                "不逐条加密，建议开启 Windows BitLocker。\n"
                "· 备份：退出程序后复制整个目录（必须包含 data\\model_config.key 和 "
                "data\\model_registry.sqlite3）。\n"
                "· 卸载默认保留数据，只有连续两次确认才会删除。"
            ),
            fg="#555555", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w")
        # 第一页就说清楚：关窗等于放弃本次设置；服务启动后的窗口关闭只是隐藏到托盘。
        self._tk.Label(
            frame,
            text="关闭将放弃本次设置，不会创建账号。",
            fg="#b00020", anchor="w", wraplength=620, justify="left",
        ).pack(anchor="w", pady=(6, 0))
        row = self._tk.Frame(frame)
        row.pack(fill="x", pady=8)
        entry = self._tk.Entry(row, textvariable=self.dir_var)
        entry.pack(side="left", fill="x", expand=True)
        self._tk.Button(
            row,
            text="浏览…",
            command=lambda: self._browse(filedialog),
        ).pack(side="left", padx=(8, 0))
        # 选到 U 盘 / 移动硬盘 / 网络盘时，必须在页面上直说后果，
        # 不能等用户装完打不开才解释。
        self._volume_warning = self._tk.Label(
            frame, text="", fg="#b00020", anchor="w", justify="left", wraplength=620
        )
        self._volume_warning.pack(anchor="w")
        self.dir_var.trace_add("write", lambda *_: self._refresh_volume_warning())
        self._refresh_volume_warning()
        self._tk.Label(
            frame,
            text=(
                "提示：如果这台电脑没有 D 盘，上面已预填当前用户的目录"
                "（%LOCALAPPDATA%\\LiuHao\\data）；确认无误可直接点「下一步」，"
                "也可以改成别的可写目录。"
            ),
            fg="#555555", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(4, 0))
        self._tk.Label(
            frame, text=f"日志：{self._log_path}", fg="#777777", anchor="w", wraplength=620
        ).pack(anchor="w", pady=(6, 0))
        return frame

    def _refresh_volume_warning(self) -> None:
        """输入框变化时刷新风险提示（不弹窗，只让用户看见）。"""
        label = getattr(self, "_volume_warning", None)
        if label is None:
            return
        try:
            label.configure(text=volume_risk_message(self.dir_var.get()))
        except Exception:  # noqa: BLE001 - 窗口可能已销毁
            pass

    def _confirm_volume_risk(self) -> bool:
        """数据目录在移动盘/网络盘上时，先让用户明确知道后果。

        默认按钮是「否」：拔盘/断网后数据找不到是可预见故障，宁可多问一句；
        用户确认后同一路径不再重复弹窗。
        """
        risk = volume_risk_message(self.dir_var.get())
        if not risk:
            self._volume_ack = ""
            return True
        current = self.dir_var.get().strip()
        if self._volume_ack:
            try:
                if _same_dir(Path(self._volume_ack), Path(current)):
                    return True
            except (OSError, ValueError):
                pass
        from tkinter import messagebox

        try:
            confirmed = bool(
                messagebox.askyesno(
                    "数据目录在外接设备上",
                    risk + "\n\n仍要使用这个位置吗？",
                    parent=self.root,
                    default=messagebox.NO,
                )
            )
        except Exception:  # noqa: BLE001 - 弹窗不可用时不猜测，停在当前页
            confirmed = False
        if confirmed:
            self._volume_ack = current
        else:
            self._show_actions(risk)
        return confirmed

    def _page_account(self, parent: Any) -> Any:
        frame = self._tk.Frame(parent)
        self._tk.Label(
            frame, text="第 2 步 · 管理员账号", font=("Microsoft YaHei UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))
        grid = self._tk.Frame(frame)
        grid.pack(fill="x", pady=4)
        for row, (label, var, extra) in enumerate(
            (
                ("账号（英文）", self.principal_var, {}),
                ("显示名（可中文）", self.display_var, {}),
                ("登录密码", self.password_var, {"show": "*"}),
                ("确认密码", self.confirm_var, {"show": "*"}),
                ("工作台名字（中文）", self.workbench_name_zh_var, {}),
                ("工作台英文名（可空）", self.workbench_name_en_var, {}),
            )
        ):
            self._tk.Label(grid, text=label, width=14, anchor="e").grid(
                row=row, column=0, sticky="e", pady=3
            )
            self._tk.Entry(grid, textvariable=var, width=32, **extra).grid(
                row=row, column=1, sticky="w", padx=8, pady=3
            )
        self._tk.Label(
            frame,
            text=f"密码至少 {MIN_PASSWORD_LEN} 位，且同时包含字母和数字。请记下来——之后登录就是用它。",
            fg="#555555", anchor="w", wraplength=620,
        ).pack(anchor="w", pady=(6, 0))
        self._tk.Label(
            frame,
            text="工作台名字就是顶栏、登录页和助手显示的名字（默认贾维斯）；改了全站都跟着变，之后随时可在控制台「系统设置 → 工作台名字」里再改。",
            fg="#555555", anchor="w", wraplength=620,
        ).pack(anchor="w", pady=(6, 0))
        return frame

    def _page_model(self, parent: Any, ttk: Any) -> Any:
        frame = self._tk.Frame(parent)
        self._tk.Label(
            frame, text="第 3 步 · 选择模型（可跳过）", font=("Microsoft YaHei UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self._tk.Radiobutton(
            frame, text="跳过，稍后在「系统设置 → 模型管理」里添加",
            variable=self.mode_var, value="skip", command=self._sync_model_fields,
        ).pack(anchor="w")
        self._tk.Radiobutton(
            frame, text="本机 Ollama（需先安装 Ollama 并 pull 模型）",
            variable=self.mode_var, value="ollama", command=self._sync_model_fields,
        ).pack(anchor="w")
        self.ollama_row = self._tk.Frame(frame)
        self.ollama_row.pack(fill="x", padx=(24, 0), pady=(2, 8))
        self._pair(self.ollama_row, "模型名", self.ollama_model_var, "qwen2.5:7b")
        self._pair(self.ollama_row, "地址", self.ollama_url_var, OLLAMA_DEFAULT_BASE_URL)

        self._tk.Radiobutton(
            frame, text="云端 API Key（OpenAI 兼容，如 DeepSeek）",
            variable=self.mode_var, value="cloud", command=self._sync_model_fields,
        ).pack(anchor="w")
        self.cloud_row = self._tk.Frame(frame)
        self.cloud_row.pack(fill="x", padx=(24, 0), pady=(2, 0))
        self._pair(self.cloud_row, "base_url", self.cloud_url_var, CLOUD_DEFAULT_BASE_URL)
        self._pair(self.cloud_row, "模型名", self.cloud_model_var, CLOUD_DEFAULT_MODEL, 0)
        self._pair(self.cloud_row, "API Key", self.cloud_key_var, "", 1, show="*")
        self.cloud_help = self._tk.Frame(frame)
        self.cloud_help.pack(fill="x", padx=(24, 0), pady=(2, 0))
        self._tk.Label(
            self.cloud_help,
            text="Key 在 DeepSeek 开放平台申请；新账号可能需要先充值才能调用。",
            fg="#555555", anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w")
        self._tk.Label(
            self.cloud_help,
            text="填完不用自己找地方测试：第 5 步自检会真的发一次请求验证 Key。",
            fg="#555555", anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w")
        self._tk.Button(
            self.cloud_help, text="打开申请页", command=self._open_deepseek
        ).pack(anchor="w", pady=(2, 0))
        self._tk.Label(
            frame,
            text=(
                "Key 只保存在本机数据目录的 data\\model_registry.sqlite3，"
                "由 AES-256-GCM 加密，不会写进明文配置；"
                "解密用的 data\\model_config.key 在同目录，仅当前 Windows 用户可读。\n"
                "删除：系统设置 → 模型管理 → 删除该模型，Key 会一起删除。\n"
                "联网：保存后，对话内容会直接发送到你填写的 base_url，不经过贾维斯服务器；"
                "本机 Ollama 的推理只连 127.0.0.1，不出网。\n"
                "「联网检索」默认关闭；开启后才把提炼后的关键词发到 hn.algolia.com / api.github.com / export.arxiv.org 三个公开接口。"
                "只发送提炼后的关键词；不发送你的对话内容、对话历史、个人画像、账号信息或 API Key。"
                "关闭后立即生效，无需重启。"
            ),
            fg="#555555", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(4, 0))
        port_row = self._tk.Frame(frame)
        port_row.pack(fill="x", pady=(10, 0))
        self._tk.Label(port_row, text="服务端口", width=10, anchor="e").pack(side="left")
        self._tk.Entry(port_row, textvariable=self.port_var, width=10).pack(
            side="left", padx=(6, 8)
        )
        self._tk.Label(
            port_row,
            text="默认 8080。被占用时不会自动改端口，请在这里换一个空闲端口。",
            fg="#555555", anchor="w", wraplength=460, justify="left",
        ).pack(side="left")
        self._sync_model_fields()
        return frame

    def _page_summary(self, parent: Any) -> Any:
        frame = self._tk.Frame(parent)
        self._tk.Label(
            frame, text="第 4 步 · 完成前请确认", font=("Microsoft YaHei UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self._tk.Label(
            frame, textvariable=self.summary_var, fg="#111111",
            anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(0, 6))
        self._tk.Label(
            frame,
            text=(
                "加密：只加密 API Key；对话、知识库、审计是本地明文 SQLite，"
                "依赖 Windows 账号权限保护。\n"
                "备份：退出程序后复制整个数据目录（含 data\\model_config.key 与 "
                "data\\model_registry.sqlite3），缺一个已存 Key 就解不开。\n"
                "卸载：默认保留数据，连续两次确认才会删除。\n"
                "联网：只有你配置云端模型后，对话内容与检索到的知识片段才会发到你填的 "
                "base_url；本机 Ollama 的推理只连 127.0.0.1，不出网。\n"
                "联网检索默认关闭；开启后才把提炼后的关键词发到 hn.algolia.com / api.github.com / export.arxiv.org 三个公开接口。"
                "只发送提炼后的关键词；不发送你的对话内容、对话历史、个人画像、账号信息或 API Key。"
                "关闭后立即生效，无需重启。不做统计上报，也不自动检查更新。"
            ),
            fg="#555555", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w")
        self._tk.Label(
            frame,
            text="之后随时可在贾维斯窗口左侧底部的「关于与隐私」查看版本、数据目录、"
                 "许可证与联网说明。",
            fg="#777777", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(8, 0))
        return frame

    def _page_check(self, parent: Any) -> Any:
        """最后一页：四项当场检查结果 + 失败项的可点击下一步。"""
        frame = self._tk.Frame(parent)
        # 本页文字比前几页短，不撑一下会被窗口自适应压得很窄、换行受伤。
        self._tk.Frame(frame, width=640, height=1).pack()
        self._tk.Label(
            frame, text="第 5 步 · 自检结果", font=("Microsoft YaHei UI", 11, "bold")
        ).pack(anchor="w", pady=(0, 6))
        self._tk.Label(
            frame,
            text=(
                "下面四项会当场检查：端口试绑定、数据目录试写入、"
                "托盘组件试加载（启动后驻留）、默认模型会真的发一次请求。"
            ),
            fg="#555555", anchor="w", wraplength=620,
        ).pack(anchor="w")
        self._tk.Label(
            frame,
            text=(
                "「换一个端口」＝把 8080 换成别的数字，例如 8081。\n"
                "「打开数据目录」＝打开账号和聊天记录所在的文件夹。\n"
                "「去模型页测试连接」＝回第 3 步检查 Key、地址和模型名。"
            ),
            fg="#777777", anchor="w", justify="left", wraplength=620,
        ).pack(anchor="w", pady=(4, 0))
        self._check_summary_label = self._tk.Label(
            frame, text="正在自检…", fg="#555555", anchor="w", justify="left",
            wraplength=620,
        )
        self._check_summary_label.pack(anchor="w", pady=(6, 8))

        self._check_widgets: dict[str, dict[str, Any]] = {}
        for key, title in (
            ("port", "网关端口"),
            ("data_dir", "数据目录"),
            ("tray", "托盘驻留（关窗后继续运行）"),
            ("model", "默认模型"),
        ):
            box = self._tk.Frame(frame)
            box.pack(fill="x", pady=(0, 6))
            head = self._tk.Frame(box)
            head.pack(fill="x")
            self._tk.Label(
                head, text=title, font=("Microsoft YaHei UI", 10, "bold"), anchor="w"
            ).pack(side="left")
            badge = self._tk.Label(head, text="检测中…", fg="#777777")
            badge.pack(side="left", padx=(10, 0))
            action = self._tk.Button(
                head, text="", width=14, command=lambda k=key: self._run_check_action(k)
            )
            detail = self._tk.Label(
                box, text="", fg="#333333", anchor="w", justify="left", wraplength=600
            )
            detail.pack(fill="x")
            advice = self._tk.Label(
                box, text="", fg="#777777", anchor="w", justify="left", wraplength=600
            )
            advice.pack(fill="x")
            self._check_widgets[key] = {
                "badge": badge,
                "detail": detail,
                "advice": advice,
                "action": action,
            }

        self._recheck_button = self._tk.Button(
            frame, text="重新检测全部四项", command=self._start_checks
        )
        self._recheck_button.pack(anchor="w", pady=(4, 0))
        return frame

    def _pair(self, parent: Any, label: str, var: Any, placeholder: str, row: int = 0, show: str = "") -> None:
        self._tk.Label(parent, text=label, width=10, anchor="e").grid(
            row=row, column=0, sticky="e", pady=2
        )
        entry = self._tk.Entry(parent, textvariable=var, width=42, show=show)
        entry.grid(row=row, column=1, sticky="w", padx=6, pady=2)
        if placeholder and not var.get():
            var.set(placeholder)

    def _browse(self, filedialog: Any) -> None:
        try:
            chosen = filedialog.askdirectory(initialdir=self.dir_var.get() or str(APP_ROOT))
        except Exception:  # noqa: BLE001 - 目录选择失败不阻断手输
            chosen = ""
        if chosen:
            self.dir_var.set(chosen)

    def _sync_model_fields(self) -> None:
        mode = self.mode_var.get()
        state_ollama = "normal" if mode == "ollama" else "disabled"
        state_cloud = "normal" if mode == "cloud" else "disabled"
        for child in self.ollama_row.winfo_children():
            child.configure(state=state_ollama)
        for child in self.cloud_row.winfo_children():
            child.configure(state=state_cloud)
        for child in self.cloud_help.winfo_children():
            child.configure(state=state_cloud)

    def _open_deepseek(self) -> None:
        """用系统默认浏览器打开 DeepSeek 开放平台的 API Key 申请页。"""
        url = "https://platform.deepseek.com/api_keys"
        try:
            opened = webbrowser.open(url)
        except Exception:  # noqa: BLE001 - 打不开浏览器不能卡住向导
            opened = False
        if not opened:
            self.error_label.configure(text=f"没能自动打开浏览器，请手动访问：{url}")

    # -- 导航 ---------------------------------------------------------------
    def _show(self, index: int) -> None:
        self._index = index
        if index == 3:
            workbench_zh = (self.workbench_name_zh_var.get() or "").strip() or BRAND_NAME_ZH
            workbench_en = (self.workbench_name_en_var.get() or "").strip() or BRAND_NAME_EN
            workbench = (
                workbench_zh
                if workbench_zh == workbench_en
                else f"{workbench_en} · {workbench_zh}"
            )
            self.summary_var.set(
                f"数据目录：{self.dir_var.get()}\n工作台名字：{workbench}"
            )
        for position, page in enumerate(self._pages):
            if position == index:
                page.pack(fill="both", expand=True)
            else:
                page.pack_forget()
        self._back.configure(state="normal" if 0 < index < 4 else "disabled")
        if index == 4:
            self._next.configure(text="进入贾维斯", state="normal")
            self._start_checks()
        elif index == 3:
            self._next.configure(text="完成并自检", state="normal")
        else:
            self._next.configure(text="下一步", state="normal")
        if self._ready:
            # 各步骤内容高度不同：切换后重新贴合内容，避免窗口留白或超高。
            self.root.after_idle(lambda: self._apply_geometry(center=False))

    def _local_error(self, index: int) -> str:
        if index == 0:
            _, error = validate_data_dir(self.dir_var.get())
            return error
        if index == 1:
            return (
                validate_principal(self.principal_var.get())
                or validate_password(self.password_var.get())
                or (
                    "两次输入的密码不一致。"
                    if self.confirm_var.get() != self.password_var.get()
                    else ""
                )
                or (
                    validate_workbench_name(
                        self.workbench_name_zh_var.get(),
                        self.workbench_name_en_var.get(),
                    )
                    or ""
                )
            )
        if index >= 3:
            return ""
        raw = (self.port_var.get() or "").strip()
        try:
            port = int(raw)
        except ValueError:
            return "服务端口必须是数字（1–65535）。"
        if not 1 <= port <= 65535:
            return "服务端口必须在 1–65535 之间。"
        return ""

    # -- 失败动作 -----------------------------------------------------------
    def _hide_actions(self) -> None:
        self.error_label.configure(text="")
        for widget in (
            self._retry_button,
            self._change_dir_button,
            self._other_port_button,
            self._log_button,
        ):
            widget.pack_forget()
        self._actions.pack_forget()

    def _show_actions(self, message: str) -> None:
        """一句人话 + 可点击动作；端口问题额外给「改用其他端口」。"""
        self.error_label.configure(text=message)
        self._actions.pack(side="bottom", fill="x", padx=20, pady=(2, 0))
        self._retry_button.pack(side="left")
        self._change_dir_button.pack(side="left", padx=(8, 0))
        if "端口" in message:
            self._other_port_button.pack(side="left", padx=(8, 0))
        else:
            self._other_port_button.pack_forget()
        self._log_button.pack(side="left", padx=(8, 0))

    def _retry(self) -> None:
        self._hide_actions()
        self._submit()

    def _change_dir(self) -> None:
        self._hide_actions()
        self._show(0)

    def _open_log_dir(self) -> None:
        target = self._log_path.parent
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # noqa: S606 - Windows 打开资源管理器
        except (OSError, AttributeError):
            self.error_label.configure(text=f"无法自动打开目录，请手动前往：{target}")

    def _ask_new_port(self, suggestion: int) -> int | None:
        """弹窗输入新端口；单独抽出来，测试可以替换成固定值。"""
        from tkinter import simpledialog

        return simpledialog.askinteger(
            "改用其他端口",
            "请输入新的服务端口（1–65535）：\n换端口后地址会变，请以新地址为准。",
            initialvalue=suggestion,
            parent=self.root,
            minvalue=1,
            maxvalue=65535,
        )

    def _use_other_port(self) -> None:
        from tkinter import messagebox
        from src.desktop.probe import port_is_free

        suggestion = _recommended_free_port(self.port + 1 if self.port < 65535 else 8081)
        while True:
            value = self._ask_new_port(suggestion)
            if value is None:
                return
            if not port_is_free(int(value), "127.0.0.1"):
                suggested = _recommended_free_port(
                    int(value) + 1 if int(value) < 65535 else 8081
                )
                messagebox.showwarning(
                    "端口不可用",
                    f"这个端口 {value} 被占用。换一个（推荐 {suggested}），或者关掉占用它的程序。",
                    parent=self.root,
                )
                suggestion = suggested
                continue
            self.port = int(value)
            self.port_var.set(str(value))
            os.environ["LIUHAO_API_PORT"] = str(value)
            self._hide_actions()
            if self.committed:
                # 设置已落盘，只需要把新端口交给启动器，然后重新检测。
                self._start_checks()
            else:
                self._submit()
            return

    # -- 自检页的动作 -------------------------------------------------------
    def _start_checks(self) -> None:
        if self._check_running:
            _write_log(
                self.data_dir,
                f"evt=startup.check.start_ignored seq={self._check_seq}"
                " reason=already_running next_state=disabled",
            )
            return
        self._check_running = True
        self._check_seq += 1
        self._check_started_at = time.monotonic()
        self._check_poll_failures = 0
        self._finish_pending = False
        self._write_check_event(
            f"evt=startup.check.start seq={self._check_seq}"
            " next_state=disabled next_text=正在自检…"
        )
        self._check_summary_label.configure(text="正在本机自检…", fg="#555555")
        for widgets in self._check_widgets.values():
            widgets["badge"].configure(text="检测中…", fg="#777777")
            widgets["detail"].configure(text="")
            widgets["advice"].configure(text="")
            widgets["action"].pack_forget()
        self._next.configure(state="disabled", text="正在自检…")

        data_dir = Path(self.data_dir)
        port = int(self.port)
        model_name = self._model_name
        tray_planned = bool(self.tray_planned)
        results_box: queue.Queue[list[CheckResult]] = queue.Queue()

        def worker() -> None:
            try:
                results = run_self_check(
                    data_dir=data_dir,
                    port=port,
                    model_name=model_name,
                    tray_planned=tray_planned,
                )
            except Exception:  # noqa: BLE001 - 自检失败也不能让界面停在"检测中"
                _write_log(data_dir, traceback.format_exc())
                results = [
                    CheckResult(
                        key=key,
                        title=title,
                        ok=False,
                        detail="这项检查没能完成；请点「重新检测」再试一次。",
                        action="重新检测",
                        advice="可以稍后再弄，软件已可用。",
                        code="error",
                    )
                    for key, title in (
                        ("port", "网关端口"),
                        ("data_dir", "数据目录"),
                        ("tray", "托盘驻留（关窗后继续运行）"),
                        ("model", "默认模型"),
                    )
                ]
            try:
                write_self_check_log(data_dir, results, port=port)
            except Exception:  # noqa: BLE001 - 写日志失败不影响结果展示
                _write_log(data_dir, traceback.format_exc())
            # tkinter 的 after() 不能从工作线程调用：改用队列 + 主线程轮询。
            results_box.put(results)

        threading.Thread(target=worker, name="first-run-self-check", daemon=True).start()
        self.root.after(100, lambda: self._poll_checks(results_box))

    def _write_check_event(self, text: str) -> None:
        """自检链路的埋点：只写事件名/序号/计数/按钮状态，绝不写凭据与用户名。"""
        _write_log(self.data_dir, text)

    def _check_timed_out(self) -> bool:
        if not self._check_running:
            return False
        return (time.monotonic() - self._check_started_at) >= CHECK_TIMEOUT_SECONDS

    def _force_check_timeout(self, reason: str) -> None:
        """自检卡住时的兜底：把按钮一定恢复成可点，并给用户一行中文提示。

        正常自检约 3 秒完成，这里只在异常路径触发；结果全部标成不阻塞，
        所以按钮会变成「进入贾维斯」，用户可以先进控制台再回头看结果。
        """
        if not self._check_running:
            return
        self._write_check_event(
            f"evt=startup.check.timeout seq={self._check_seq}"
            f" reason={reason} elapsed={int(time.monotonic() - self._check_started_at)}"
            " blocking=0 next_state=normal"
        )
        results = [
            CheckResult(
                key=key,
                title=title,
                ok=False,
                detail="这项检查没有在预期时间内完成，结果暂缺。",
                action="",
                advice="可以先进入控制台，稍后在系统状态里再检查。",
                code="timeout",
            )
            for key, title in (
                ("port", "网关端口"),
                ("data_dir", "数据目录"),
                ("tray", "托盘驻留（关窗后继续运行）"),
                ("model", "默认模型"),
            )
        ]
        self._show_checks(results, summary_override=CHECK_TIMEOUT_HINT)

    def _poll_checks(self, results_box: queue.Queue[list[CheckResult]]) -> None:
        try:
            results = results_box.get_nowait()
        except queue.Empty:
            if self._check_timed_out():
                self._force_check_timeout("deadline")
                return
            try:
                self.root.after(100, lambda: self._poll_checks(results_box))
            except Exception:  # noqa: BLE001 - after() 失败不能再静默吞掉
                self._check_poll_failures += 1
                self._write_check_event(
                    "evt=startup.check.poll_error"
                    f" seq={self._check_seq} failures={self._check_poll_failures}"
                )
                if self._check_poll_failures >= 3:
                    self._force_check_timeout("poll_error")
            return
        self._show_checks(results)

    def _show_checks(
        self, results: list[CheckResult], *, summary_override: str = ""
    ) -> None:
        self._check_running = False
        self._check_results = list(results)
        by_key = {item.key: item for item in results}
        for key, widgets in self._check_widgets.items():
            result = by_key.get(key)
            if result is None:
                widgets["badge"].configure(text="需要处理", fg="#b00020")
                widgets["detail"].configure(
                    text="这项检查没有结果；请点下方「重新检测全部四项」。"
                )
                widgets["advice"].configure(text="")
                widgets["action"].pack_forget()
                continue
            if result.code == "skipped":
                # 跳过模型是合法选择：灰色「稍后配置」，不按失败标红。
                widgets["badge"].configure(text="稍后配置", fg="#777777")
            else:
                widgets["badge"].configure(
                    text="通过" if result.ok else "需要处理",
                    fg="#0a7d33" if result.ok else "#b00020",
                )
            widgets["detail"].configure(text=result.detail)
            widgets["advice"].configure(text=result.advice)
            if result.action:
                widgets["action"].configure(text=result.action, state="normal")
                widgets["action"].pack(side="right", padx=(10, 0))
            else:
                widgets["action"].pack_forget()

        failures = [item for item in results if not item.ok and item.code != "skipped"]
        skipped = [item for item in results if item.code == "skipped"]
        if failures:
            names = "、".join(item.title for item in failures)
            self._check_summary_label.configure(
                text=f"有 {len(failures)} 项需要处理：{names}。每项右侧都有可点的下一步。",
                fg="#b00020",
            )
        elif skipped:
            names = "、".join(item.title for item in skipped)
            self._check_summary_label.configure(
                text=f"{names}稍后配置，不阻塞进入控制台；其余必要项都已通过。",
                fg="#777777",
            )
        else:
            self._check_summary_label.configure(
                text="四项全部通过：账号、数据目录、端口和模型都可用。",
                fg="#0a7d33",
            )
        if summary_override:
            # 超时兜底要有一行明确的中文提示，不能只留日志让用户去翻。
            self._check_summary_label.configure(text=summary_override, fg="#b45309")
        if any(item.blocking for item in failures):
            self._next.configure(state="disabled", text="请先处理上面的问题")
        else:
            self._next.configure(state="normal", text="进入贾维斯")
        blocking_count = len([item for item in failures if item.blocking])
        self._write_check_event(
            f"evt=startup.check.rendered seq={self._check_seq}"
            f" results={len(results)} blocking={blocking_count}"
            f" summary_override={1 if summary_override else 0}"
            f" next_state={'disabled' if blocking_count else 'normal'}"
        )
        if self._ready:
            # 几何调整只是外观：它失败不能反过来把窗口留在「正在自检…」这种异常态。
            try:
                self.root.after_idle(lambda: self._apply_geometry(center=False))
            except Exception:  # noqa: BLE001
                self._write_check_event(
                    f"evt=startup.check.geometry_deferred_error seq={self._check_seq}"
                )
        self._resume_deferred_finish()

    def _resume_deferred_finish(self) -> None:
        """自检收尾：若用户此前已点过「进入贾维斯」，在这里自动接着走。

        用户在按钮还是「正在自检…」时点的那一下不能被丢掉——排队后由这里兑现。
        自检真有阻塞项时不替用户越过，把决定权交回界面。
        """
        if not self._finish_pending:
            return
        self._finish_pending = False
        blocking = [item for item in self._check_results if item.blocking]
        if blocking:
            _write_log(
                self.data_dir,
                "evt=wizard.finish_resume skipped=1"
                f" blocking={len(blocking)} next_state=disabled",
            )
            return
        _write_log(
            self.data_dir,
            "evt=wizard.finish_resume auto=1 blocking=0 next_text=进入贾维斯",
        )
        self._finish()

    def _run_check_action(self, key: str) -> None:
        result = next(
            (item for item in self._check_results if item.key == key), None
        )
        if result is None or not result.action:
            return
        if key == "port":
            self._use_other_port()
        elif key == "data_dir":
            self._open_data_dir()
        elif key == "tray":
            self._start_checks()
        elif key == "model":
            self._hide_actions()
            self._show(2)

    def _open_data_dir(self) -> None:
        target = Path(self.data_dir)
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.startfile(str(target))  # noqa: S606 - Windows 打开资源管理器
        except (OSError, AttributeError):
            self.error_label.configure(text=f"无法自动打开目录，请手动前往：{target}")

    def _next_step(self) -> None:
        if self._index == len(self._pages) - 1:
            self._finish()
            return
        # 第 1 页：移动盘/网络盘要先被告知后果并确认，再往下走。
        if self._index == 0 and not self._confirm_volume_risk():
            return
        error = self._local_error(self._index)
        if error:
            self._show_actions(error)
            return
        if self._index < len(self._pages) - 1:
            self._hide_actions()
            if self._index == 3:
                self._submit()
                return
            self._show(self._index + 1)
            return
        self._submit()

    def _finish(self) -> None:
        """自检页的「进入控制台」：设置已保存，关闭窗口让启动器继续。"""
        if self._check_running:
            # 自检还没结束就点了：不静默丢弃，也不只写一行日志让用户干等。
            # 按钮改成明确的等待文案并禁用，自检收尾时由 _show_checks 自动接着走 _finish。
            self._finish_pending = True
            self._next.configure(state="disabled", text="自检完成后自动进入…")
            _write_log(
                self.data_dir,
                "evt=wizard.finish_deferred self_check_running=1"
                " next_state=disabled next_text=自检完成后自动进入",
            )
            return
        _write_log(
            self.data_dir,
            f"evt=wizard.finish accepted=1 check_running=0 checks={len(self._check_results)}",
        )
        self.accepted = True
        self.error_message = ""
        self._stop_dpi_watcher()
        self.root.destroy()

    def _back_step(self) -> None:
        if self._index > 0:
            self._hide_actions()
            self._show(self._index - 1)

    def _show_recovery_code(self, code: str) -> None:
        """一次性展示管理员恢复码；关闭后系统只剩哈希，无法再次查看。"""
        tk = self._tk
        dialog = tk.Toplevel(self.root)
        dialog.title("管理员恢复码（只显示这一次）")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        body = tk.Frame(dialog, padx=18, pady=16)
        body.pack(fill="both", expand=True)
        tk.Label(
            body,
            text=(
                "请立即复制或抄下这张恢复码。\n"
                "关闭本窗口后将无法再次查看——系统只保存它的哈希。\n"
                "换电脑或忘记密码时，用它恢复管理员账号。"
            ),
            justify="left",
            anchor="w",
            wraplength=460,
        ).pack(fill="x")
        code_var = tk.StringVar(master=dialog, value=code)
        entry = tk.Entry(
            body,
            textvariable=code_var,
            font=("Consolas", 13),
            justify="center",
            state="readonly",
        )
        entry.pack(fill="x", pady=(12, 8))
        with contextlib.suppress(Exception):
            entry.selection_range(0, "end")
        hint = tk.Label(body, text="", fg="#b45309", anchor="w")
        hint.pack(fill="x")

        def _copy() -> None:
            dialog.clipboard_clear()
            dialog.clipboard_append(code)
            hint.configure(text="已复制到剪贴板。")

        def _close() -> None:
            dialog.destroy()

        buttons = tk.Frame(body)
        buttons.pack(fill="x", pady=(10, 0))
        tk.Button(buttons, text="复制恢复码", command=_copy).pack(side="left")
        tk.Button(buttons, text="我已保存，关闭", command=_close).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", _close)

        # 这个弹窗只显示一次恢复码，绝不能自动关闭；但必须保证它不会被主窗挡住：
        # 一旦被挡住，主窗在 grab_set() 期间对点击完全无响应，用户只会看到"卡死"。
        # 日志只记事件，不记码本身。
        with contextlib.suppress(Exception):
            dialog.update_idletasks()
            dialog.lift()
            dialog.focus_force()
            dialog.attributes("-topmost", True)

            def _drop_topmost() -> None:
                with contextlib.suppress(Exception):
                    dialog.attributes("-topmost", False)

            dialog.after(400, _drop_topmost)
        _write_log(self.data_dir, "evt=wizard.recovery_dialog.show topmost=1 grab=1")
        dialog.grab_set()
        dialog.wait_window()
        _write_log(self.data_dir, "evt=wizard.recovery_dialog.hide")

    def _submit(self) -> None:
        if self._submitted:
            return
        self._submitted = True
        self.error_label.configure(text="正在创建账号并写入配置…")
        self._actions.pack_forget()
        self.root.update_idletasks()
        values = self.values()
        try:
            ok, message = self._on_submit(values)
        except Exception:  # noqa: BLE001 - 任何异常都只给一句人话，堆栈进日志
            _write_log(self._log_path.parent.parent, traceback.format_exc())
            ok, message = (
                False,
                f"设置过程中出现意外，请点「重试」或「换目录」。\n日志：{self._log_path}",
            )
        if ok:
            self.error_message = ""
            # 用户可能改了数据目录：自检与日志都必须落到**实际**目录。
            chosen = chosen_data_dir()
            if chosen is not None:
                self.data_dir = Path(chosen)
            self._log_path = focus_log_path(self.data_dir)
            with contextlib.suppress(TypeError, ValueError):
                self.port = int(values.get("api_port") or self.port)
            self._model_mode = str(values.get("model") or "skip")
            self._model_name = (
                str(values.get("model_name") or "").strip()
                if self._model_mode != "skip"
                else ""
            )
            _write_log(self.data_dir, message)
            self.committed = True
            self._submitted = False
            self._hide_actions()
            recovery_code = str(getattr(self._on_submit, "recovery_code", "") or "")
            if recovery_code:
                setattr(self._on_submit, "recovery_code", "")
                self._show_recovery_code(recovery_code)
            self._show(4)
            return
        self._submitted = False
        self.error_message = message
        self._show_actions(message)

    def _cancel(self) -> None:
        if self.accepted:
            return
        if self.committed:
            # 设置已经落盘，关窗等于「先不进控制台」；不能让启动器误判为用户取消。
            self.accepted = True
            self.error_message = ""
            self._stop_dpi_watcher()
            self.root.destroy()
            return
        from tkinter import messagebox

        if not messagebox.askyesno(
            "放弃首次设置？",
            "确定要放弃本次设置吗？\n\n"
            "放弃后不会创建账号；需要重新双击桌面图标再来一次。",
            parent=self.root,
            default=messagebox.NO,
        ):
            return
        self.canceled = True
        self._stop_dpi_watcher()
        self.root.destroy()

    def values(self) -> dict[str, Any]:
        return {
            "data_dir": self.dir_var.get(),
            "principal": self.principal_var.get(),
            "display": self.display_var.get(),
            "password": self.password_var.get(),
            "confirm": self.confirm_var.get(),
            "workbench_name_zh": self.workbench_name_zh_var.get(),
            "workbench_name_en": self.workbench_name_en_var.get(),
            "model": self.mode_var.get(),
            "api_port": self.port_var.get(),
            "model_name": (
                self.ollama_model_var.get()
                if self.mode_var.get() == "ollama"
                else self.cloud_model_var.get()
            ),
            "base_url": (
                self.ollama_url_var.get()
                if self.mode_var.get() == "ollama"
                else self.cloud_url_var.get()
            ),
            "api_key": self.cloud_key_var.get(),
        }

    def wait(self) -> None:
        self.root.mainloop()


def _create_ui(
    default_dir: Path,
    on_submit: Callable[[dict[str, Any]], CommitResult],
    log_path: Path,
    port: int = 8080,
) -> _WizardWindow:
    return _WizardWindow(default_dir, on_submit, log_path, port)


def run_wizard(
    ui_factory: UIFactory | None = None,
    *,
    port: int | None = None,
    tray_planned: bool = False,
) -> int:
    """打开向导并等待用户完成；返回进程退出码（0 表示可以继续启动）。

    窗口打不开（无桌面会话 / Tk 缺失）时返回 ``EXIT_UNAVAILABLE``，并给出
    中文原因与日志路径——绝不退回 ``input()`` 让用户面对 ``EOFError``。

    ``port`` 是启动器实际要用的端口（``--api-port``），作为向导里的默认值；
    用户可以在界面上改成别的端口。``tray_planned`` 表示本次启动之后会进入
    托盘壳（安装版桌面入口），自检页据此如实说明「关窗后继续运行」是否成立。
    """
    # 界面要显示完整路径：环境变量/调用方给相对路径时也先补成绝对路径。
    data_dir = Path(os.path.abspath(str(chosen_data_dir() or default_data_dir())))
    log_path = focus_log_path(data_dir)
    effective_port = _env_api_port() if port is None else int(port)

    def on_submit(values: dict[str, Any]) -> CommitResult:
        # 界面上改过端口就以界面为准；没有端口字段（测试替身）则用启动器传入值。
        recovery: list[str] = []
        result = commit(
            values,
            port=None if "api_port" in values else effective_port,
            recovery_out=recovery,
        )
        if recovery and result[0]:
            # 挂到回调上而不是窗口上：测试替身会在 ui 赋值前同步调用本回调。
            on_submit.recovery_code = recovery[0]
        return result

    try:
        ui = (ui_factory or _create_ui)(data_dir, on_submit, log_path, effective_port)
        with contextlib.suppress(Exception):  # 测试替身可能只读属性
            ui.tray_planned = bool(tray_planned)
    except Exception:  # noqa: BLE001 - 无桌面会话是最常见的真实故障
        _write_log(data_dir, traceback.format_exc())
        message = (
            "无法打开设置窗口。请在正常登录的 Windows 桌面上运行贾维斯；"
            "如果这台电脑没有桌面（例如计划任务 / 远程无桌面会话），"
            f"请改用命令行方式：\"{APP_ROOT / 'LiuHao.bat'}\" --console\n"
            f"日志：{log_path}"
        )
        _emit(message)
        return EXIT_UNAVAILABLE

    ui.wait()
    if ui.canceled:
        _emit("[cancel] 已取消首次设置：没有创建账号，也不会启动控制台。")
        return EXIT_CANCELED
    if not ui.accepted:
        message = getattr(ui, "error_message", "") or "首次设置未完成。"
        _emit(f"[FAIL] {message}")
        return EXIT_UNAVAILABLE
    _emit("[ok] 首次设置完成，正在启动控制台…")
    return EXIT_OK


def _emit(text: str) -> None:
    """有控制台就打印（同时已写日志）；``pythonw`` 下 stdout 为 None。"""
    stream = sys.stdout
    if stream is not None:
        try:
            stream.write(text + "\n")
            stream.flush()
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="贾维斯图形化首次设置向导")
    parser.add_argument("--data-dir", default="", help="覆盖默认数据目录（排障用）")
    parser.add_argument("--api-port", type=int, default=None, help="预检的网关端口")
    args = parser.parse_args(argv)
    if args.data_dir:
        path, error = validate_data_dir(args.data_dir)
        if error or path is None:
            _emit(f"[FAIL] {error}")
            return EXIT_UNAVAILABLE
        os.environ[DATA_DIR_ENV] = str(path)
    return run_wizard(port=args.api_port)


if __name__ == "__main__":
    raise SystemExit(main())
