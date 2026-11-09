"""身份签名密钥的「数据格式」兼容守卫（升级 / 回滚）。

为什么需要它
------------
0.1.2 起，``config/integrity.key`` 从**明文**迁移为带版本头的机器绑定格式
（Windows 上为 ``LIUHAO-DPAPI-1:...``，非 Windows 为 ``LIUHAO-RAW-1:...``）。
0.1.1 及更早版本的 ``read_key_file()`` 只会 ``read_text().strip()``，没有
任何格式判断，于是它把**整段包裹文本**当作签名密钥，导致
``human_identities.json`` 里每一条已签名记录都通不过完整性校验（fail-closed）
——程序能启动、健康检查也正常，但可用账号是 **0 个**，用户被锁在门外。

而升级前备份**故意不包含** ``integrity.key`` 与 ``human_identities.json``
（见 ``backups.is_protected_state_file``：密钥与身份库不外泄），所以回滚只
恢复代码与普通数据，密钥格式不会跟着回去。这就是"回滚成功但登不进去"的
结构性原因。

本模块提供三件事：

* :func:`assess_key_visibility` —— 用两种语义（新版解包 / 旧版原文）分别
  计算"有多少条已登记身份能通过校验"，把"0 个可用账号"变成可判定的数字；
* :func:`downgrade_key_for_legacy` —— 把包裹密钥降级回旧版可读的明文，
  这是回滚后**真正能恢复登录**的那条路；
* :func:`check_startup_key_format` —— 启动门禁：读不懂当前格式的程序
  明确拒绝启动，而不是"看起来能启动但登不进"。

安全取舍（务必随补丁一起看）
----------------------------
降级会把签名密钥以**明文**写回磁盘（权限收紧到当前用户 ACL）。在降级窗口内：

* 同一 Windows 用户下的任何进程都能读到它，进而伪造身份行；
* 把 ``integrity.key`` 拷贝到另一台机器后**仍然可用**——DPAPI 的"换机即
  失效"保护在这段时间里不存在；
* 一旦重新升级/启动新版，启动路径会自动把它重新包裹回 DPAPI 格式，
  明文窗口随之结束。

因此降级必须只发生在"回滚到旧版、且用户确实需要登入取数据"的窗口，并在
journal 里留下明确记录。
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from packaging.version import InvalidVersion, Version

from src.common.atomic_write import atomic_write_text
from src.common.fs_permissions import restrict_to_current_user
from src.common.integrity_key import (
    KEY_FILE_REL,
    REGISTRY_FILE_REL,
    read_key_file_status,
)
from src.security.encryption import DPAPI_PREFIX, RAW_PREFIX

from .errors import UpgradeError

#: 数据格式标记文件（与 ``data-schema.json`` 同级，互不替代）。
KEY_FORMAT_FILENAME = "data-format.json"
KEY_FORMAT_SCHEMA = "liuhao.data-format/1"

#: 旧版可读：``integrity.key`` 是明文密钥文本。
KEY_FORMAT_PLAINTEXT = "plaintext-v0"
#: 新版落盘：带 ``LIUHAO-DPAPI-1:`` / ``LIUHAO-RAW-1:`` 版本头的包裹格式。
KEY_FORMAT_WRAPPED = "wrapped-v1"

#: 第一个能读懂包裹格式的发布版本。低于它的版本读到包裹密钥会 0 账号。
KEY_FORMAT_MIN_BUILD = "0.1.2"

_WRAPPED_PREFIXES = (DPAPI_PREFIX, RAW_PREFIX)


class KeyFormatError(UpgradeError):
    """密钥格式无法按请求处理（例如跨机解不开，不能降级）。"""


def _key_path(data_dir: Union[str, Path]) -> Path:
    return Path(data_dir) / KEY_FILE_REL


def _registry_path(data_dir: Union[str, Path]) -> Path:
    return Path(data_dir) / REGISTRY_FILE_REL


def marker_path(data_dir: Union[str, Path]) -> Path:
    return Path(data_dir) / KEY_FORMAT_FILENAME


def detect_key_format(data_dir: Union[str, Path]) -> str:
    """按文件内容判断当前密钥格式：``wrapped-v1`` / ``plaintext-v0`` / ``missing``。"""
    path = _key_path(data_dir)
    if not path.is_file():
        return "missing"
    try:
        raw = path.read_bytes()
    except OSError:
        return "unreadable"
    text = raw.decode("utf-8", "ignore").lstrip("\ufeff").strip()
    if any(text.startswith(prefix) for prefix in _WRAPPED_PREFIXES):
        return KEY_FORMAT_WRAPPED
    return KEY_FORMAT_PLAINTEXT


def read_key_format_marker(data_dir: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """读取 ``data-format.json``；缺失或损坏一律返回 ``None``（兼容优先）。"""
    path = marker_path(data_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != KEY_FORMAT_SCHEMA:
        return None
    return payload


def read_key_format_marker_strict(
    data_dir: Union[str, Path],
) -> Optional[Dict[str, Any]]:
    """启动门禁用的严格读取。

    与 :func:`read_key_format_marker` 的「兼容优先」相反：**缺失**才返回
    ``None``（从未打过标的老库），**存在但读不懂**直接抛
    :class:`KeyFormatError`。门禁必须区分这两者——把损坏当成缺失，就等于
    把「数据可能已被更高版本写过」判断成「这是老数据，放行」。
    """
    path = marker_path(data_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KeyFormatError(
            f"数据格式标记存在但无法读取或解析（{path}）：{exc}"
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema") != KEY_FORMAT_SCHEMA:
        raise KeyFormatError(
            f"数据格式标记格式不正确（{path}）：schema 必须是 {KEY_FORMAT_SCHEMA}"
        )
    value = payload.get("integrity_key")
    if value not in (KEY_FORMAT_WRAPPED, KEY_FORMAT_PLAINTEXT):
        raise KeyFormatError(
            f"数据格式标记中的 integrity_key 无效（{path}）：{value!r}"
        )
    return payload


def stamp_key_format(
    data_dir: Union[str, Path], app_version: str
) -> Optional[Dict[str, Any]]:
    """记录当前密钥格式与「最低可读版本」。

    物理格式优先于已有标记：即使标记缺失（老数据第一次升级），只要密钥已是
    包裹格式，也写成 ``wrapped-v1`` / ``min_version=0.1.2``。
    """
    fmt = detect_key_format(data_dir)
    if fmt not in (KEY_FORMAT_WRAPPED, KEY_FORMAT_PLAINTEXT):
        return None
    payload: Dict[str, Any] = {
        "schema": KEY_FORMAT_SCHEMA,
        "integrity_key": fmt,
        "min_version": KEY_FORMAT_MIN_BUILD if fmt == KEY_FORMAT_WRAPPED else "0.0.0",
        "written_by": str(app_version),
        "updated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    path = marker_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def _load_rows(data_dir: Union[str, Path]) -> Optional[list]:
    """读取身份登记表的行；文件缺失/损坏返回 ``None``（不猜、不补签）。"""
    path = _registry_path(data_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    entries = payload.get("humans") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return None
    return [row for row in entries if isinstance(row, dict)]


def assess_key_visibility(data_dir: Union[str, Path]) -> Dict[str, Any]:
    """分别用新版/旧版语义计算"可见账号数"。

    * ``rows_visible_wrapped``：用解包后的密钥校验（新版行为）；
    * ``rows_visible_legacy``：用文件原文当密钥校验（旧版行为）。

    旧版把包裹文本当密钥时，这两个数字会分叉成 ``N`` 与 ``0``——这正是
    "回滚后 0 个可用账号"的可判定证据。
    """
    from src.kernels.identity._persistence import tag_matches

    directory = Path(data_dir)
    fmt = detect_key_format(directory)
    key_path = _key_path(directory)
    if key_path.is_file():
        wrapped_key, key_state = read_key_file_status(key_path)
    else:
        wrapped_key, key_state = "", "missing"
    legacy_key = ""
    if key_path.is_file():
        try:
            legacy_key = key_path.read_text(encoding="utf-8").strip()
        except OSError:
            legacy_key = ""
    rows = _load_rows(directory)
    signed = [
        row
        for row in (rows or [])
        if isinstance(row, dict) and str(row.get("integrity") or "").strip()
    ]
    visible_wrapped = (
        sum(1 for row in signed if tag_matches(row, wrapped_key, row.get("integrity")))
        if wrapped_key
        else 0
    )
    visible_legacy = (
        sum(1 for row in signed if tag_matches(row, legacy_key, row.get("integrity")))
        if legacy_key
        else 0
    )
    return {
        "key_format": fmt,
        "key_state": key_state,
        "key_path": str(key_path),
        "registry_present": rows is not None,
        "registry_path": str(_registry_path(directory)),
        "rows_total": len(rows or []),
        "rows_signed": len(signed),
        "rows_visible_wrapped": visible_wrapped,
        "rows_visible_legacy": visible_legacy,
        # 真正的判据：已登记身份存在，但旧版语义下一条都进不来。
        "legacy_would_lock_out": bool(signed) and visible_legacy == 0,
    }


def _target_is_legacy(target_version: Optional[str]) -> bool:
    if not target_version:
        # 版本未知时按"可能读不懂"处理：回滚场景下宁可多降级一次。
        return True
    try:
        return Version(str(target_version)) < Version(KEY_FORMAT_MIN_BUILD)
    except InvalidVersion:
        return True


def key_format_needs_downgrade(
    report: Dict[str, Any], target_version: Optional[str]
) -> bool:
    """回滚到 ``target_version`` 前，是否需要把密钥降级。"""
    if report.get("key_format") != KEY_FORMAT_WRAPPED:
        return False
    if not int(report.get("rows_signed") or 0):
        return False
    return _target_is_legacy(target_version)


def _atomic_write_plaintext(path: Path, text: str) -> None:
    """把明文密钥原子写回，权限收紧到当前用户（失败即抛，不吞）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.downgrade-{os.getpid()}-{secrets.token_hex(4)}.tmp")
    fd = os.open(
        tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if not restrict_to_current_user(tmp):
            raise KeyFormatError(
                f"无法把降级后的密钥权限收紧到当前用户：{tmp}。已中止，原文件未被覆盖。"
            )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    if not restrict_to_current_user(path):
        # 内容已换成明文但 ACL 没追上：必须让调用方知道（不是静默放行）。
        raise KeyFormatError(
            f"密钥已降级为明文，但权限收紧失败：{path}。请立即手工检查该文件 ACL。"
        )


def downgrade_key_for_legacy(
    data_dir: Union[str, Path], *, app_version: str = ""
) -> Dict[str, Any]:
    """把包裹密钥降级为旧版可读的明文（回滚前置动作）。

    这是**唯一**能让 0.1.1 重新认出已登记账号的动作。代价是降级窗口内密钥
    以明文落盘（见模块文档）。调用方必须在成功后重算
    :func:`assess_key_visibility`，确认 ``rows_visible_legacy`` 真的恢复。
    """
    directory = Path(data_dir)
    key_path = _key_path(directory)
    fmt = detect_key_format(directory)
    if fmt == "missing":
        raise KeyFormatError(
            f"找不到身份签名密钥：{key_path}。没有它旧版本无论如何都登不进去；"
            "请从账号备份包恢复，或重新注册管理员。"
        )
    if fmt == KEY_FORMAT_PLAINTEXT:
        return {
            "changed": False,
            "reason": "已经是旧版可读的明文格式，无需降级",
            "key_format": KEY_FORMAT_PLAINTEXT,
            "key_path": str(key_path),
        }
    if fmt != KEY_FORMAT_WRAPPED:
        raise KeyFormatError(f"无法识别身份签名密钥格式（{fmt}）：{key_path}")

    key, state = read_key_file_status(key_path)
    if state != "ok" or not key:
        raise KeyFormatError(
            "密钥带版本头但本机解不开（换机/换用户/密文损坏），无法降级为明文。"
            "请回到原电脑操作，或使用账号备份包恢复；"
            "不要删除该文件，否则所有已登记账号将永久无法验证。"
        )
    _atomic_write_plaintext(key_path, key)
    if app_version:
        try:
            stamp_key_format(directory, app_version)
        except OSError:
            # 标记写失败不影响明文已落盘这一事实，但要让上层看见。
            pass
    return {
        "changed": True,
        "key_format": KEY_FORMAT_PLAINTEXT,
        "key_path": str(key_path),
        "security_note": (
            "签名密钥已以明文写入磁盘（权限已收紧到当前用户）。"
            "在重新升级或再次用新版启动前，请勿拷贝/共享该文件；"
            "新版启动时会自动把它重新包裹为机器绑定格式。"
        ),
    }


def required_min_build(data_dir: Union[str, Path]) -> Optional[str]:
    """当前数据目录要求的最低可读版本；``None`` 表示没有限制。

    物理格式优先：只要密钥已是包裹格式，就是 0.1.2 起才能读懂，即使标记
    文件缺失（老数据第一次升级、标记被删）也不会误放行。
    """
    if detect_key_format(data_dir) == KEY_FORMAT_WRAPPED:
        return KEY_FORMAT_MIN_BUILD
    marker = read_key_format_marker(data_dir)
    if marker and marker.get("integrity_key") == KEY_FORMAT_WRAPPED:
        value = str(marker.get("min_version") or KEY_FORMAT_MIN_BUILD)
        return value
    return None


def check_startup_key_format(
    data_dir: Union[str, Path], app_version: str
) -> Optional[str]:
    """启动门禁：本二进制读不懂当前密钥格式时返回**拒绝说明**，否则 ``None``。

    这里只能保护"本补丁之后发布"的版本——已经发出去的 0.1.1 没有这段代码，
    对它只能靠升级编排器在回滚前降级（见 ``orchestrator``）。
    """
    required = required_min_build(data_dir)
    if not required:
        return None
    try:
        if Version(str(app_version)) >= Version(required):
            return None
    except InvalidVersion:
        return None
    marker = read_key_format_marker(data_dir) or {}
    writer = str(marker.get("written_by") or "更新版本")
    return (
        "[FAIL] 数据目录里的身份签名密钥已升级为新格式，已拒绝启动。\n"
        f"       密钥格式：机器绑定包裹（{KEY_FORMAT_WRAPPED}，写入者 {writer}）；"
        f"当前程序 {app_version} 需要 {required} 或更新版本才能读懂它。\n"
        "       继续启动会出现「程序能打开、但所有账号都登不进」："
        "身份行会被完整性校验全部拒绝（0 个可用账号）。\n"
        "       下一步（任选其一）：\n"
        f"       1) 用 {required} 或更新版本启动（推荐，数据与格式都匹配）；\n"
        "       2) 确实要回滚到当前版本：先执行数据降级命令，再启动当前版本：\n"
        "          python -m src.upgrade key-compat --downgrade "
        "--app-root <程序目录> --data-dir <数据目录>\n"
        f"          （该命令由 {required} 及以上的代码提供；"
        "当前版本里没有它，请从升级包或回滚备份的 code.zip 中运行。）\n"
        "       注意：降级期间签名密钥以明文落盘，仅限本机当前用户可读；"
        "恢复登录后请尽快重新升级。"
    )
