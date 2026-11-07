"""一键诊断包：控制台可导出的、**白名单生成**的排障压缩包。

设计约束（为什么不是"打包整个目录再排除密钥"）
------------------------------------------------
排除法永远会漏：新加一个配置文件、日志里多打印一个字段，密钥就跟着出去了。
这里反过来——先写死允许出现在包里的成员与字段，任何未登记内容都不会进包：

* ``README.txt`` / ``manifest.json``
* ``summary.json``（版本、提交号、系统与架构、安装方式、数据目录脱敏路径）
* ``health.json`` / ``readiness.json``（健康检查与就绪自检）
* ``selfcheck.json``（生产配置预检的**代码**与级别，不含配置值）
* ``logs/<file>.txt``（最近日志尾部）

三条硬规则：

1. 所有文本先做用户名脱敏（``C:\\Users\\<name>`` / ``/home/<name>`` /
   ``/Users/<name>``），再做密钥脱敏（复用 ``src.desktop.diagnostics``）；
2. 生成时逐成员跑一遍密钥自检，命中就**拒绝导出**（宁可失败，不泄漏）；
3. 就绪检查里的原始异常文本 ``error`` / ``technical_detail`` **不写入**
   诊断包——它们是机器串，属于服务端日志，不属于用户要发出去的文件。
"""
from __future__ import annotations

import getpass
import hashlib
import io
import json
import os
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Tuple

from fastapi import APIRouter
from fastapi.responses import Response

from ..desktop.about import APP_NAME, collect_about
from ..desktop.diagnostics import contains_secret, redact_text
from ..logging_config import load_uvicorn_log_config, resolve_log_config_path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: 包内允许出现的固定成员。新增成员必须先改这里，白名单之外一律拒绝。
BUNDLE_MEMBERS: Tuple[str, ...] = (
    "README.txt",
    "summary.json",
    "health.json",
    "readiness.json",
    "selfcheck.json",
    "manifest.json",
)

#: 允许的日志成员前缀。文件名仍需再清洗（只保留 ``*.log`` 的安全基名）。
LOG_MEMBER_PREFIX = "logs/"

_MAX_LOG_FILES = 5
_MAX_LOG_LINES = 200
_MAX_LOG_BYTES = 200_000

_USER_PATH_PATTERNS: Tuple[re.Pattern[str], ...] = (
    # C:\Users\name  /  D:/Users/name  /  C:\Documents and Settings\name
    # 用户名段允许空格（"张 三"），边界只认路径分隔符/引号，避免把整句正文当成路径。
    re.compile(
        r"(?i)(?P<prefix>[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+)"
        r"(?P<name>[^\\/\r\n\"'<>|]+)"
    ),
    # /home/name  /  /Users/name
    re.compile(
        r"(?i)(?P<prefix>/(?:home|Users)/)"
        r"(?P<name>[^/\s\"'<>|]+)"
    ),
)

#: 就绪检查里允许进包的字段。``error`` / ``technical_detail`` 故意不在其中。
_READINESS_CHECK_FIELDS: Tuple[str, ...] = (
    "status",
    "status_label",
    "user_message",
    "availability",
    "evidence_status",
)


def mask_user_path(text: Any) -> str:
    """把路径里的用户名换成 ``<用户>``（只动路径段，不碰正常文本）。"""
    out = str(text or "")
    # /root 本身就是家目录，整段替换，而不是把下一级当成用户名。
    out = re.sub(r"/root(?=/|\s|$)", "/<用户>", out)
    for pattern in _USER_PATH_PATTERNS:
        out = pattern.sub(lambda m: f"{m.group('prefix')}<用户>", out)
    # 兜底：当前系统用户名以独立词出现时也遮掉（例如日志里的 user=name）。
    try:
        username = getpass.getuser().strip()
    except Exception:  # noqa: BLE001 - 取不到用户名时不做兜底
        username = ""
    if len(username) >= 3:
        out = re.sub(
            rf"(?i)(?<![A-Za-z0-9_.-]){re.escape(username)}(?![A-Za-z0-9_.-])",
            "<用户>",
            out,
        )
    return out


def sanitize_text(value: Any) -> str:
    """用户要发出去的任何文本都走这里：先遮路径用户名，再遮密钥。"""
    return redact_text(mask_user_path(value))


def _safe_mapping(payload: Mapping[str, Any], allowed: Iterable[str]) -> Dict[str, Any]:
    """按白名单取字段；缺失的键不补，未登记的键丢弃。"""
    return {key: payload[key] for key in allowed if key in payload}


def _install_method(root: Path) -> str:
    """安装方式只给中性描述，不暴露安装目录。"""
    if getattr(sys, "frozen", False):
        return "安装包（独立程序）"
    if (root / "scripts" / "launch_installed.py").is_file():
        return "安装版"
    if (root / ".venv").is_dir():
        return "源码运行（虚拟环境）"
    return "源码运行"


def _log_dir() -> Path:
    """按真实生效的日志配置推导目录（安装版会把它改到用户数据目录）。"""
    try:
        config = load_uvicorn_log_config(resolve_log_config_path()) or {}
    except Exception:  # noqa: BLE001 - 配置损坏时退回程序目录
        config = {}
    handlers = config.get("handlers", {}) if isinstance(config, dict) else {}
    for handler in handlers.values():
        filename = handler.get("filename") if isinstance(handler, dict) else None
        if filename:
            return Path(str(filename)).expanduser().parent
    return root_logs_dir()


def root_logs_dir() -> Path:
    return REPO_ROOT / "logs"


def _summary(root: Path, log_dir: Path) -> Dict[str, Any]:
    about = collect_about(root)
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "app": str(about.get("app") or APP_NAME),
        "version": about.get("version"),
        "commit": about.get("commit"),
        "build_time": about.get("build_time"),
        "build_time_source": about.get("build_time_source"),
        "operating_system": {
            "system": platform.system() or "未知",
            "release": platform.release() or "未知",
            "machine": platform.machine() or "未知",
        },
        "python_version": sys.version.split()[0],
        "install_method": _install_method(root),
        "data_dir": sanitize_text(about.get("data_dir") or root / "data"),
        "log_dir": sanitize_text(log_dir),
        "note": "路径中的用户名已替换为 <用户>；本包不含任何密钥或令牌。",
    }


def _health_payload() -> Dict[str, Any]:
    import asyncio

    from .health import liveness_probe

    result = asyncio.run(liveness_probe(_HeadersOnlyRequest("diagnostics-health")))
    body = json.loads(result.body.decode("utf-8"))
    return _safe_mapping(
        body,
        (
            "status",
            "status_label",
            "user_message",
            "service",
            "version",
            "api_contract",
            "timestamp",
        ),
    ) | {"ai_provider": _safe_mapping(body.get("ai_provider") or {}, ("type", "mode", "simulated"))}


def _readiness_payload() -> Dict[str, Any]:
    import asyncio

    from .health import readiness_probe

    result = asyncio.run(readiness_probe(_HeadersOnlyRequest("diagnostics-ready")))
    body = json.loads(result.body.decode("utf-8"))
    checks: Dict[str, Any] = {}
    for name, check in (body.get("checks") or {}).items():
        if not isinstance(check, dict):
            continue
        # 只保留人话字段；原始 error / technical_detail 不进包。
        checks[str(name)] = _safe_mapping(check, _READINESS_CHECK_FIELDS)
    return {
        "status": body.get("status"),
        "status_label": body.get("status_label"),
        "user_message": body.get("user_message"),
        "http_status": result.status_code,
        "checks": checks,
    }


def _selfcheck_payload() -> Dict[str, Any]:
    """生产配置预检：只记录"哪条规则没过"，不记录任何配置值。"""
    from ..observability.production import validate_production_config
    from .display import decorate_finding

    report = validate_production_config()
    body = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    findings = []
    for item in body.get("findings") or []:
        if not isinstance(item, dict):
            continue
        decorated = decorate_finding(dict(item))
        findings.append(
            _safe_mapping(
                decorated,
                ("code", "severity", "severity_label", "display_title", "user_message"),
            )
        )
    critical = int(body.get("critical_count") or 0)
    warning = int(body.get("warning_count") or 0)
    if critical:
        message = f"自检发现 {critical} 个严重配置问题，请先处理后使用"
    elif warning:
        message = f"自检发现 {warning} 个提醒项，不影响启动，建议查看"
    else:
        message = "自检通过，未发现需要处理的配置问题"
    return {
        "ok": bool(body.get("ok")),
        "critical_count": critical,
        "warning_count": warning,
        "user_message": message,
        "findings": findings,
    }


def _log_name(path: Path) -> str | None:
    """只接受 ``*.log`` 的安全基名，其余一律拒绝（白名单之外的入口）。"""
    name = path.name
    if not name.lower().endswith(".log"):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        return None
    return name


def _tail_log(path: Path) -> str:
    data = path.read_bytes()[-_MAX_LOG_BYTES:]
    text = data.decode("utf-8", "replace").splitlines()[-_MAX_LOG_LINES:]
    return sanitize_text("\n".join(text))


def _collect_logs(log_dir: Path) -> Dict[str, str]:
    if not log_dir.is_dir():
        return {}
    try:
        files = sorted(
            (p for p in log_dir.glob("*.log") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return {}
    members: Dict[str, str] = {}
    for path in files:
        name = _log_name(path)
        if not name:
            continue
        try:
            members[f"{LOG_MEMBER_PREFIX}{name}.txt"] = _tail_log(path)
        except OSError as exc:
            members[f"{LOG_MEMBER_PREFIX}{name}.txt"] = (
                f"[日志读取失败] {type(exc).__name__}；请确认日志文件未被占用。"
            )
        if len(members) >= _MAX_LOG_FILES:
            break
    return members


def _assert_whitelisted(members: Iterable[str]) -> None:
    allowed = set(BUNDLE_MEMBERS)
    for name in members:
        if name in allowed:
            continue
        if name.startswith(LOG_MEMBER_PREFIX) and name.count("/") == 1:
            continue
        raise ValueError(f"诊断包含未登记成员：{name}")


def _manifest(members: Mapping[str, bytes]) -> Dict[str, Any]:
    return {
        "schema": 1,
        "redaction": "username-path+secret",
        "members": [
            {
                "name": name,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for name, data in sorted(members.items())
        ],
    }


_README = """鎏灏 AI OS 一键诊断包
=====================

这个包用于排查启动、模型连接或运行异常，可以安全地发给技术支持。

这个包不含你的密钥、令牌或密码
------------------------------
* 模型 API Key、登录令牌、密码、口令哈希、完整性密钥都不会写入；
* 路径里的用户名已替换为 <用户>；
* 内容按白名单生成，只有下面这些文件。

包含哪些文件
------------
* summary.json    版本号、提交号、操作系统与架构、安装方式、数据目录（已脱敏）
* health.json     网关存活检查结果
* readiness.json  依赖就绪检查结果（只有结论与人话说明）
* selfcheck.json  生产配置预检结论（只有规则代码与级别，不含配置值）
* logs/           最近的日志尾部
* manifest.json   每个文件的校验值

怎么用
------
把这个 zip 发给技术支持，或在反馈问题时附上。日志只保留最近一段，
不会包含历史会话全文。
"""


class _HeadersOnlyRequest:
    """只提供 ``headers`` 的最小请求对象，供探针函数在进程内复用。"""

    def __init__(self, trace_id: str) -> None:
        self.headers = {"X-Trace-ID": trace_id}


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(info, data)


def build_bundle(root: Path | None = None, log_dir: Path | None = None) -> bytes:
    """生成诊断包字节流。任何成员命中密钥特征都会抛 ``ValueError`` 拒绝导出。"""
    base = Path(root or REPO_ROOT)
    logs = _collect_logs(Path(log_dir) if log_dir else _log_dir())

    payload: Dict[str, Any] = {
        "README.txt": _README,
        "summary.json": _summary(base, Path(log_dir) if log_dir else _log_dir()),
        "health.json": _health_payload(),
        "readiness.json": _readiness_payload(),
        "selfcheck.json": _selfcheck_payload(),
    }
    payload.update(logs)

    files: Dict[str, bytes] = {}
    for name, content in payload.items():
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        else:
            text = str(content)
        text = sanitize_text(text)
        if contains_secret(text):
            raise ValueError(f"诊断成员 {name} 仍含疑似密钥，已拒绝导出")
        files[name] = text.encode("utf-8")

    _assert_whitelisted(files)

    files["manifest.json"] = json.dumps(
        _manifest(files), ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            _write_member(archive, name, data)
    return buffer.getvalue()


def bundle_filename(now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"liuhao-diagnostics-{stamp}.zip"


router = APIRouter(prefix="/v1", tags=["diagnostics"])


@router.get("/diagnostics/bundle")
def download_diagnostics_bundle() -> Response:
    """下载一键诊断包（登录闸门由 ``main.py`` 统一挂载）。内容见 :data:`BUNDLE_MEMBERS`。"""
    name = bundle_filename()
    try:
        data = build_bundle()
    except ValueError as exc:
        # 自检失败时不导出半成品，也不把内部细节回显给用户。
        from fastapi import HTTPException

        raise HTTPException(
            status_code=500,
            detail="诊断包自检未通过，已停止导出。请重试；若仍失败，请改为手动收集日志。",
        ) from exc
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "X-LiuHao-Diagnostics-Filename": name,
            "Cache-Control": "no-store",
        },
    )


__all__ = [
    "BUNDLE_MEMBERS",
    "LOG_MEMBER_PREFIX",
    "build_bundle",
    "bundle_filename",
    "mask_user_path",
    "router",
    "sanitize_text",
]
