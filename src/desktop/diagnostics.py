"""一键诊断包：白名单收集 + 强制脱敏 + 生成后自检。

设计要点（全部是硬约束）：
  * **白名单生成**：zip 里的每个成员都由本模块在内存中构造
    （``manifest.json`` / ``README.txt`` / ``*.json`` / ``logs/*.txt``），
    从不遍历目录、从不打包数据库文件。因此密钥、token、完整性键、
    身份表、数据库原文都不可能被"顺手"带进去。
  * **写入前脱敏**：所有文本先过 :func:`redact_text`，再进 zip。
  * **写入前剔除**：仍含明文凭据的成员不进包，其余成员照常导出，并在包内
    ``REDACTED-MEMBERS.txt`` 列出被剔除的文件与命中条数；只有真实诊断成员
    一个都留不下时才整体拒绝（用户最需要求助时不能什么包都拿不到）。
  * **写入后自检**：:func:`verify_bundle` 重新打开 zip 逐成员扫描，
    一旦发现疑似密钥就删除产物并抛错（fail-closed），不留"看起来正常"的包。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata as metadata
import json
import logging
import os
import platform
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..brand import BRAND_LOCKUP
from ..common.leak_scan import (
    LOG_MEMBER_ALIAS_NOTE,
    OMITTED_MEMBERS_NAME,
    SCAN_RULE_HIT_NAME,
    completeness_line,
    format_hits,
    log_bundle_blocked,
    log_member_name,
    omitted_members_text,
    omitted_readme,
    scan_rule_hit_report,
    scan_text,
)
from ..common.redaction import (
    REDACTION_VERSION,
    contains_secret,
    leak_violations,
    mask_user_path,
    redact_text,
    sanitize_text,
)
from ..logging_config import (
    prune_log_retention,
    read_retention_report,
    resolve_data_dir,
    resolve_log_file,
)
from . import REPO_ROOT, diagnostics_dir
from .about import collect_about
from .probe import DEFAULT_PORTS, discover_service, http_json, port_is_free

logger = logging.getLogger(__name__)

_MAX_LOG_FILES = 8
_MAX_LOG_LINES = 200
_MAX_LOG_BYTES = 200_000
_MAX_DEPENDENCIES = 500
_MAX_INVENTORY_ENTRIES = 60
_MAX_INVENTORY_FILES = 5000
_MAX_PROCESSES = 15
_MAX_READY_PORTS = 8
_READY_TIMEOUT = 0.5

#: 采集上限：静态资源、数据库、日志信号、模型端点探测都要有明确天花板，
#: 否则一个装满构建产物的目录会让"一键诊断"变成"一键扫描整块磁盘"。
_MAX_STATIC_ASSETS = 40
_MAX_DB_FILES = 20
_MAX_DB_MESSAGES = 5
_MAX_DB_CHECK_BYTES = 512 * 1024 * 1024
_MAX_SIGNAL_SAMPLES = 20
_MAX_ENDPOINT_PROBES = 5
_ENDPOINT_TIMEOUT = 1.5
_MAX_INDEX_BYTES = 1_000_000
#: 引导日志（数据/日志目录都不可写时的唯一证据）与结构化事件的保留行数。
_MAX_BOOTSTRAP_LINES = 200
_MAX_EVENT_LINES = 20
#: 低于这个剩余空间就按"磁盘满"给结论（64 MB 已不足以写日志与重建库）。
_MIN_FREE_BYTES = 64 * 1024 * 1024

#: checks 的先后顺序 = 处置优先级。headline 里"建议先处理 X"必须与
#: ``SUMMARY.txt`` 的结论指向同一个检查，否则两份文件会互相矛盾。
_CHECK_ORDER = (
    "disk_space_low",
    "directory_not_writable",
    "console_assets_missing",
    "installation_incomplete",
    "database_corrupt",
    "port_conflict",
    "model_key_invalid",
    "model_network_unreachable",
    "service_not_running",
)


def _check_priority(check_id: Any) -> int:
    try:
        return _CHECK_ORDER.index(str(check_id))
    except ValueError:
        return len(_CHECK_ORDER)


#: 这些提供方必须填 Key；本地推理（ollama 等）不填 Key 是正常配置，不能误报。
_KEY_REQUIRED_PROVIDERS = frozenset(
    {
        "openai",
        "openai-compatible",
        "openai_compatible",
        "deepseek",
        "anthropic",
        "azure",
        "gemini",
        "google",
        "qwen",
        "dashscope",
        "moonshot",
        "zhipu",
        "doubao",
        "volcengine",
        "siliconflow",
    }
)

#: 驾驶舱构建产物目录的覆盖变量（与网关 ``console_static`` 同一契约）。
_CONSOLE_DIST_ENV = "LIUHAO_CONSOLE_DIST"

#: 每条故障结论给非开发者的下一步动作。只对 detected / inconclusive 输出，
#: 让"看不懂 evidence 的人"也知道该点什么、该找谁。
_NEXT_STEPS = {
    "service_not_running": "点托盘菜单的「重新启动服务」；仍失败就点「生成诊断包」，把 zip 发给技术支持。",
    "installation_incomplete": "用同一个安装包重新安装（或重新解压）补齐缺失文件；若杀毒软件隔离了文件，先从隔离区恢复。",
    "directory_not_writable": "把数据目录改到当前用户可写的位置（例如 %LOCALAPPDATA%\\LiuHao\\data），或让管理员给该目录写权限。",
    "port_conflict": "在任务管理器结束占用该端口的程序后重试；不确定是哪个程序时，生成诊断包，ports.json 会写明进程名和 PID。",
    "model_key_invalid": "打开「系统设置 → 模型管理」，重新填写 Key 并点「测试」。",
    "model_network_unreachable": "检查网络、代理和防火墙，确认能访问模型服务地址后重试。",
    "console_assets_missing": "重新安装以补齐 console 目录；不要只复制可执行文件。",
    "database_corrupt": "先备份数据目录，再把损坏的数据库文件改名或移走，重启后会自动重建空库。",
    "disk_space_low": "清理磁盘释放空间，或把数据目录换到其它剩余空间充足的卷后重启。",
}

#: SQLite 只按文件头识别，不按扩展名 —— 改名的库同样是数据库。
_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")

#: 安装完整性清单：``source`` 模式只要求源码；安装版额外要求运行时与驾驶舱产物。
#: "缺 src / 缺 runtime" 就是这两组里的条目缺失。
_CORE_REQUIRED_ENTRIES = (
    ("src", "dir"),
    ("src/__init__.py", "file"),
    ("src/gateway/__init__.py", "file"),
    ("src/desktop/__init__.py", "file"),
    ("config/logging.yaml", "file"),
)
_INSTALLED_REQUIRED_ENTRIES = (
    ("LiuHao.bat", "file"),
    ("runtime/python.exe", "file"),
    ("console/index.html", "file"),
)

#: index.html 里指向本地的脚本 / 样式 / 图标引用。
_ASSET_REF_RE = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)

#: 日志里可判定的模型失败机器信号（provider 与 gateway 两处都会写）。
#: ``type=`` 兜底旧版日志；``upstream_code`` / ``upstream_type`` 是上游原样字段。
_SIGNAL_CODE_RE = re.compile(
    r"\b(?:reason|kind|upstream_kind|upstream_code|upstream_type|type)=([a-z_]+)"
)
_SIGNAL_STATUS_RE = re.compile(r"\bstatus=(\d{3})\b")
_SIGNAL_CATEGORIES = {
    "invalid_key": "key_invalid",
    "invalid_api_key": "key_invalid",
    "authentication_error": "key_invalid",
    "key_revoked": "key_invalid",
    "quota_exhausted": "quota_exhausted",
    "insufficient_quota": "quota_exhausted",
    "billing_hard_limit_reached": "quota_exhausted",
    "billing_not_active": "quota_exhausted",
    "rate_limited": "rate_limited",
    "rate_limit_exceeded": "rate_limited",
    "model_not_found": "model_not_found",
    "invalid_model": "model_not_found",
    "unreachable": "network_unreachable",
    "timeout": "network_unreachable",
    "upstream_error": "upstream_error",
}
_STATUS_CATEGORIES = {
    401: "key_invalid",
    403: "key_invalid",
    402: "quota_exhausted",
    404: "model_not_found",
    429: "rate_limited",
}
#: 旧版 / 第三方 SDK 只把错误原文写进日志，没有 reason=/kind=/status= 结构化字段。
#: 这些文本特征用来兜底，顺序即优先级。
_LOG_TEXT_SIGNALS = (
    (
        "key_invalid",
        (
            "invalid_api_key",
            "Incorrect API key",
            "AuthenticationError",
            "Unauthorized",
        ),
    ),
    (
        "quota_exhausted",
        ("insufficient_quota", "exceeded your current quota", "billing hard limit"),
    ),
    (
        "network_unreachable",
        (
            "WinError 10061",
            "积极拒绝",
            "NewConnectionError",
            "Connection refused",
            "WinError 10060",
            "read timeout",
            "Read timed out",
            "getaddrinfo failed",
            "NameResolutionError",
            "11001",
            "Max retries exceeded",
        ),
    ),
)
#: 进程命令行里出现这些入口才算是本产品进程（避免把路径里带 liuhao 的无关进程也算进来）。
_APP_PROCESS_MARKERS = (
    "src.gateway",
    "src.desktop",
    "liuhao_desktop",
    "launch_installed",
    "start_liuhao",
    "first_run_wizard",
)

#: zip 成员只允许这两种后缀，杜绝把二进制数据库塞进来。
_ALLOWED_SUFFIXES = (".json", ".txt")
_FORBIDDEN_NAME_TOKENS = (
    "secret",
    "credential",
    "password",
    "integrity",
    "identity",
    "private_key",
    "apikey",
    "api_key",
)

#: 脱敏与自检的**唯一实现**在 ``src/common/redaction.py``：桌面与网关两个诊断
#: 入口共用同一份规则和同一个自检谓词，自检清单独立于脱敏规则单独维护。
#: 这里只保留模块级别名，方便既有调用方（``from .diagnostics import redact_text``）。


def _sanitize_url(url: str | None) -> str | None:
    """去掉 URL 里的 userinfo（``http://user:pass@host`` 的凭据部分）。"""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return "***REDACTED-URL***"
    if parts.username or parts.password:
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


def _tail_log(path: Path) -> str:
    try:
        data = path.read_bytes()[-_MAX_LOG_BYTES:]
    except OSError as exc:
        return f"[读取失败] {type(exc).__name__}: {exc}"
    lines = data.decode("utf-8", "replace").splitlines()[-_MAX_LOG_LINES:]
    return redact_text("\n".join(lines))


def _collect_log_retention(log_dir: Path) -> dict[str, Any]:
    """最近一次日志保留清理的结果（保留 / 删除清单），随诊断包一起导出。

    生成诊断包前先按当前策略清一轮，保证报告对应当前磁盘状态；清理失败
    不影响诊断包本身。
    """
    try:
        if not log_dir.is_dir():
            return {"status": "日志目录不存在"}
    except OSError as exc:
        # 目录存在但连 stat 都被拒绝（ACL 拒绝写/读）：诊断包仍要能生成。
        return {"status": f"日志目录不可访问：{type(exc).__name__}"}
    try:
        prune_log_retention(log_dir)
    except Exception as exc:  # noqa: BLE001 - 诊断包不能因为清理失败而失败
        return {"status": "清理失败", "error": f"{type(exc).__name__}: {exc}"}
    try:
        report = read_retention_report(log_dir)
    except OSError as exc:
        return {"status": f"无法读取清理报告：{type(exc).__name__}"}
    if report is None:
        return {"status": "没有清理报告"}
    report["status"] = "ok"
    return report


def _collect_logs(log_dir: Path) -> dict[str, str]:
    """收集最近日志（含 ``liuhao.log.1`` 之类的轮转文件），并附截断说明。

    ``*.log.bak-*`` 是用户按提示留下的改名备份：它是证据，但不能再被收进包，
    否则"改名后复导"会失败。``error.log`` 在包内显示为 ``issue.log``，
    避免成员名把人吓到（本机源文件名字不变）。
    """
    try:
        if not log_dir.is_dir():
            return {}
    except OSError:
        # ACL 拒绝访问时连 is_dir() 都会抛 PermissionError；包必须照样能出。
        return {}
    logs: dict[str, str] = {}
    index: list[dict[str, Any]] = []
    try:
        candidates = {
            p
            for pattern in ("*.log", "*.log.*")
            for p in log_dir.glob(pattern)
            if p.is_file()
            and not p.name.endswith(".lock")
            and ".bak-" not in p.name
        }
        files = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return logs
    for path in files[:_MAX_LOG_FILES]:
        content = _tail_log(path)
        try:
            original = path.stat().st_size
        except OSError:
            original = None
        retained = len(content.encode("utf-8"))
        index.append(
            {
                "name": path.name,
                "original_bytes": original,
                "retained_bytes": retained,
                "retained_lines": len(content.splitlines()),
                "truncated": original is not None and original > retained,
            }
        )
        logs[f"logs/{log_member_name(path.name)}.txt"] = content
    if index:
        index.append(
            {
                "note": (
                    f"最多保留 {_MAX_LOG_FILES} 份、每份尾部 {_MAX_LOG_LINES} 行且不超过 "
                    f"{_MAX_LOG_BYTES} 字节；已脱敏。完整原文仍在本机日志目录。"
                )
            }
        )
    logs["logs/index.json"] = json.dumps(index, ensure_ascii=False, indent=2)
    return logs


def _listening_owners() -> dict[int, dict[str, Any]]:
    """返回 ``{端口: {pid, process}}``；psutil 不可用或权限不足时为空。"""
    try:
        import psutil
    except ImportError:
        return {}
    owners: dict[int, dict[str, Any]] = {}
    try:
        connections = psutil.net_connections(kind="inet")
    except (psutil.AccessDenied, OSError):
        return owners
    for conn in connections:
        if getattr(conn, "status", None) != psutil.CONN_LISTEN or not conn.laddr:
            continue
        port = conn.laddr.port
        if port in owners or not conn.pid:
            continue
        try:
            proc = psutil.Process(conn.pid)
            name = proc.name()
            command = " ".join(proc.cmdline())
        except psutil.Error:
            name, command = "", ""
        haystack = f"{name} {command}".lower()
        owners[port] = {
            "pid": conn.pid,
            "process": name,
            "command": command[:200],
            # 是鎏灏自己占的端口就不是"冲突"，诊断结论必须能区分这两件事。
            "app_related": name.lower().startswith("liuhao")
            or any(marker in haystack for marker in _APP_PROCESS_MARKERS),
        }
    return owners


def _collect_ports(ports: list[int], owners: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    owners = owners or {}
    entries = [
        {
            "port": port,
            "free": port_is_free(port),
            "listening": port in owners,
            "owner": owners.get(port),
        }
        for port in ports
    ]
    # 只看"被别的进程占着"的端口：鎏灏自己监听的端口不算冲突。
    conflicts = [
        item
        for item in entries
        if item["listening"] and not (item["owner"] or {}).get("app_related")
    ]
    if conflicts:
        described = "、".join(
            f"{item['port']}（{(item['owner'] or {}).get('process') or '无法识别进程'}"
            + (f"，PID {item['owner']['pid']}" if item["owner"] else "")
            + "）"
            for item in conflicts
        )
        verdict = f"端口被其它进程占用：{described}"
    else:
        verdict = "未发现被其它进程占用的常见端口"
    return {
        "checked": ports,
        "entries": entries,
        "port_conflicts": conflicts,
        "verdict": verdict,
    }


def _collect_ready_probe(ports: list[int]) -> dict[str, Any]:
    """/v1/ready 与 /v1/health 的逐端口探测结果；服务未启动时也如实记录失败原因。"""
    candidates: list[int] = []
    for port in [*ports, *DEFAULT_PORTS, *range(8080, 8086)]:
        if port not in candidates:
            candidates.append(port)
    probes: list[dict[str, Any]] = []
    for port in candidates[:_MAX_READY_PORTS]:
        for endpoint in ("/v1/ready", "/v1/health"):
            url = f"http://127.0.0.1:{port}{endpoint}"
            started = time.perf_counter()
            info = http_json(url, timeout=_READY_TIMEOUT)
            probes.append(
                {
                    "url": url,
                    "reachable": info["ok"],
                    "status": info["status"],
                    "error": info["error"],
                    "body": info["body"],
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            )
    return {
        "timeout_seconds": _READY_TIMEOUT,
        "note": (
            "端口 free=true 且 ready 超时/拒绝，通常表示本机没有服务在监听；"
            "端口被占用但 ready 失败，则可能是无关进程占位或服务卡在启动。"
        ),
        "probes": probes,
    }


def _is_gateway_process(item: dict[str, Any]) -> bool:
    """只把真正的网关子进程算作"服务在运行"。

    桌面壳自己（``src.desktop``）、诊断命令（``diagnose``）同样是鎏灏进程，
    但它们在服务死掉时仍然存在；拿它们当"网关还活着"的证据会误判。
    网关的实际启动命令是 ``python -m src.desktop._runner``。
    """
    text = f"{item.get('name') or ''} {item.get('command') or ''}".lower()
    if "diagnose" in text or "tray-demo" in text:
        return False
    return any(
        marker in text
        for marker in ("src.desktop._runner", "src.desktop\\_runner", "src/gateway", "src.gateway", "uvicorn")
    )


def _collect_processes(expected_port: int | None = None) -> dict[str, Any]:
    """只保留与本产品相关的进程（名称或命令行命中），命令行原文会被统一脱敏。

    ``expected_port`` 由桌面壳传入：同一台机器上可能同时跑着别的网关（开发机
    常见），"本机存在网关进程"不能证明"本应用的服务还活着"。带上端口时额外
    统计真正监听该端口的网关进程数，诊断结论优先用它。
    """
    try:
        import psutil
    except ImportError:
        return {"available": False, "note": "psutil 不可用，跳过进程采集", "processes": []}
    matched: list[dict[str, Any]] = []
    try:
        iterator = psutil.process_iter(["pid", "name", "create_time", "cmdline"])
        for proc in iterator:
            try:
                info = proc.info
                name = info.get("name") or ""
                command = " ".join(info.get("cmdline") or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            haystack = f"{name} {command}".lower()
            if not name.lower().startswith("liuhao") and not any(
                marker in haystack for marker in _APP_PROCESS_MARKERS
            ):
                continue
            created = info.get("create_time")
            matched.append(
                {
                    "pid": info.get("pid"),
                    "name": name,
                    "started_at": (
                        datetime.fromtimestamp(created).astimezone().isoformat(timespec="seconds")
                        if created
                        else None
                    ),
                    "command": command[:300],
                }
            )
    except (psutil.Error, OSError):
        return {"available": False, "note": "进程枚举失败", "processes": matched}
    matched.sort(key=lambda item: item["pid"] or 0)
    gateway = [item for item in matched if _is_gateway_process(item)]
    payload: dict[str, Any] = {
        "available": True,
        "matched": len(matched),
        # 网关子进程单独计数：桌面壳仍在、服务已死时，这里应为 0。
        "gateway_matched": len(gateway),
        "gateway": gateway[:_MAX_PROCESSES],
        "truncated": len(matched) > _MAX_PROCESSES,
        "processes": matched[:_MAX_PROCESSES],
    }
    if expected_port:
        pattern = re.compile(rf"--port[= ]{expected_port}\b")
        scoped = [item for item in gateway if pattern.search(item.get("command") or "")]
        payload["expected_port"] = expected_port
        payload["gateway_for_port"] = len(scoped)
        payload["gateway_scoped"] = scoped[:_MAX_PROCESSES]
    return payload


def _dir_usage(path: Path) -> dict[str, Any]:
    """只统计数量与字节数，不读取任何文件内容；条目超限时截断。"""
    files = 0
    total = 0
    truncated = False
    try:
        for root, _dirs, names in os.walk(path):
            for name in names:
                if files >= _MAX_INVENTORY_FILES:
                    truncated = True
                    break
                try:
                    size = (Path(root) / name).stat().st_size
                except OSError:
                    size = 0
                total += size
                files += 1
            if truncated:
                break
        readable = True
    except OSError:
        readable = False
    return {"readable": readable, "files": files, "bytes": total, "truncated": truncated}


def _safe_is_dir(path: Path) -> bool:
    """``is_dir`` 的安全版：ACL 拒绝时 ``pathlib`` 会抛 ``PermissionError``。

    极端场景（数据目录被拒绝访问）下，诊断包必须照样能生成，所以这里把
    "看不见"当成"不存在"，由 ``startup_check.json`` 的可写探测负责说明原因。
    """
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    """``is_file`` 的安全版（理由同 ``_safe_is_dir``）。"""
    try:
        return path.is_file()
    except OSError:
        return False


def _collect_data_inventory(data_dir: Path) -> dict[str, Any]:
    """数据目录顶层清单 + 每个顶层目录的占用大小（不读取文件内容）。"""
    payload: dict[str, Any] = {
        "root": mask_user_path(str(data_dir)),
        "exists": _safe_is_dir(data_dir),
        "entries": [],
    }
    if not _safe_is_dir(data_dir):
        return payload
    try:
        children = sorted(data_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        payload["error"] = "数据目录无法枚举"
        return payload
    entries: list[dict[str, Any]] = []
    for child in children[:_MAX_INVENTORY_ENTRIES]:
        stat = None
        try:
            stat = child.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")
        except OSError:
            modified = None
        if _safe_is_dir(child):
            usage = _dir_usage(child)
            entries.append(
                {
                    "name": child.name,
                    "kind": "dir",
                    "files": usage["files"],
                    "bytes": usage["bytes"],
                    "truncated": usage["truncated"],
                    "modified_at": modified,
                }
            )
        else:
            entries.append(
                {
                    "name": child.name,
                    "kind": "file",
                    "bytes": stat.st_size if stat is not None else None,
                    "modified_at": modified,
                }
            )
    payload["entries"] = entries
    payload["truncated"] = len(children) > _MAX_INVENTORY_ENTRIES
    return payload


def _path_kind(path: Path) -> str:
    """目标路径类型：``directory`` / ``file`` / ``missing`` / ``other`` / ``inaccessible``。

    ``winerror=183``（"已存在"）最常见的成因是路径被同名普通文件挡住；只报错误码
    用户没法判断，所以每次写探测都带上这个类型（安装诊断包的判决字段之一）。
    """
    try:
        if path.is_dir():
            return "directory"
        if path.is_file():
            return "file"
        if path.exists():
            return "other"
        return "missing"
    except OSError:
        return "inaccessible"


def _probe_error(exc: BaseException, path: Path, stage: str) -> dict[str, Any]:
    """写探测失败的结构化证据：类型 / errno / winerror / 卡在哪一步 / 异常原文。

    异常原文里可能带绝对路径，这里先替换掉目标路径本身，再把其它本机路径折成
    ``<本地路径>/最后两段``——"卡在哪一步"比错误码更能指向处理方法。
    """
    text = f"{type(exc).__name__}: {exc}".replace(str(path), "<目标路径>")
    winerror = getattr(exc, "winerror", None)
    errno_v = getattr(exc, "errno", None)
    if winerror is None and errno_v == 13 and os.name == "nt":
        winerror = 5  # Python errmap：EACCES ↔ ERROR_ACCESS_DENIED(5)
    return {
        "type": type(exc).__name__,
        "errno": errno_v,
        "winerror": winerror,
        "stage": stage,
        "message": _mask_drive_paths(text.replace('"', "'"))[:200],
    }


def _probe_directory_writable(path: Path) -> dict[str, Any]:
    """真实写入探测：``os.access`` 在 Windows 上只看只读属性，判定不了 ACL。

    做法是在目标目录里建一个临时文件、写一个字节、再删掉；失败时记录
    异常类型 / errno / winerror / 卡在哪一步 / 目标路径类型，异常原文中的
    绝对路径会先折叠再进包。

    契约：目录不存在时**先创建再探测**（首次启动、日志被清理时目录本来
    就还没建，此时报"不可写"会误导用户去改权限），``created`` 如实记录
    是否由本次探测创建；只有创建或写入真的失败才判 ``writable=False``。
    """
    payload: dict[str, Any] = {
        "path": mask_user_path(str(path)),
        "path_kind": _path_kind(path),
        "exists": False,
        "created": False,
        "writable": False,
        "method": "create-temp-file",
        "error": None,
    }
    try:
        exists = path.is_dir()
    except OSError as exc:
        # ACL 拒绝访问时连 stat 都会被拒；这本身就是"不可写"的证据。
        payload["error"] = {**_probe_error(exc, path, "stat"), "inaccessible": True}
        return payload
    payload["exists"] = exists
    if not exists:
        # 目录不存在 ≠ 不可写：首次安装、还没启动过网关时日志目录本来就还没建。
        # 先尝试创建（与网关启动时的行为一致），创建失败才按"不可写"报告。
        try:
            path.mkdir(parents=True, exist_ok=True)
            payload["created"] = True
            payload["exists"] = True
        except OSError as exc:
            payload["error"] = {**_probe_error(exc, path, "create_directory"), "missing": True}
            return payload
    probe = path / f".liuhao-diag-{os.getpid()}-{time.time_ns()}.tmp"
    try:
        with open(probe, "wb") as handle:
            handle.write(b"liuhao diagnostics writable probe")
        payload["writable"] = True
    except OSError as exc:
        payload["error"] = _probe_error(exc, path, "write_temp_file")
    finally:
        with contextlib.suppress(OSError):
            probe.unlink(missing_ok=True)
    return payload


def probe_directory_writable(path: str | Path) -> dict[str, Any]:
    """桌面壳复用的公开入口：判断某个目录当前是否真的可写。"""
    return _probe_directory_writable(Path(path))


def _install_mode(base: Path) -> str:
    """安装版看 ``LiuHao.bat`` / ``runtime``；源码树两者都没有。"""
    if (base / "LiuHao.bat").is_file() or (base / "runtime").is_dir():
        return "installed"
    return "source"


def _collect_integrity(base: Path) -> dict[str, Any]:
    """安装完整性清单：逐项记录必需路径是否存在，并给出缺失清单。

    这份清单回答的是"程序目录是不是缺东西"，与数据目录是否可写无关。
    """
    mode = _install_mode(base)
    required = list(_CORE_REQUIRED_ENTRIES)
    if mode == "installed":
        required.extend(_INSTALLED_REQUIRED_ENTRIES)
    entries: list[dict[str, Any]] = []
    missing: list[str] = []
    for relative, kind in required:
        target = base / relative
        exists = target.is_dir() if kind == "dir" else target.is_file()
        entries.append({"path": relative, "kind": kind, "exists": exists})
        if not exists:
            missing.append(relative)
    # 目录已经缺失时，不再重复罗列它下面的每个文件，结论里只留最外层原因。
    missing = [
        item
        for item in missing
        if not any(item.startswith(f"{parent}/") for parent in missing if parent != item)
    ]
    return {
        "mode": mode,
        "required": entries,
        "missing": missing,
        "complete": not missing,
    }


def _collect_installation(
    base: Path, data_dir: Path, log_dir: Path, version: str | None = None
) -> dict[str, Any]:
    """安装位置 / 版本 / 运行期 Python / 数据与日志目录是否存在；路径按统一规则遮用户。"""
    try:
        top_level = sum(1 for _ in base.iterdir())
    except OSError:
        top_level = None
    data_write = _probe_directory_writable(data_dir)
    logs_write = _probe_directory_writable(log_dir)
    return {
        "version": version,
        "app_root": mask_user_path(str(base)),
        "app_root_exists": base.is_dir(),
        "app_top_level_entries": top_level,
        "runtime_python": mask_user_path(sys.executable),
        "runtime_python_exists": Path(sys.executable).is_file(),
        "app_runtime_python_exists": (base / "runtime" / "python.exe").is_file(),
        "data_dir": mask_user_path(str(data_dir)),
        "data_dir_exists": _safe_is_dir(data_dir),
        "data_dir_writable": data_write["writable"],
        "data_dir_write_probe": data_write,
        "logs_dir": mask_user_path(str(log_dir)),
        "logs_dir_exists": _safe_is_dir(log_dir),
        "logs_dir_writable": logs_write["writable"],
        "logs_dir_write_probe": logs_write,
        "launcher_log_present": _safe_is_file(log_dir / "launcher.log"),
        "key_files": {
            "launcher_bat": (base / "LiuHao.bat").is_file(),
            "data_dir_marker": (base / "data-dir.txt").is_file(),
            "console_index": (base / "console" / "index.html").is_file(),
        },
        "integrity": _collect_integrity(base),
    }


def _read_providers() -> dict[str, Any]:
    """只读打开模型配置库，**只取名称/类型/模型/地址与"有没有填 Key"**，从不解密密钥。"""
    db_path = Path(
        os.environ.get("LIUHAO_MODEL_CONFIG_DB")
        or resolve_data_dir() / "model_registry.sqlite3"
    )
    if not _safe_is_file(db_path):
        return {"source": db_path.name, "available": False, "models": []}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(ai_model_configs)")
            }
            # 只取"有没有填"这个布尔值，从不解密、也不读取密钥内容。
            key_column = next(
                (name for name in ("api_key_enc", "api_key_encrypted") if name in columns),
                None,
            )
            selected = "name, provider, model, base_url, enabled, is_default"
            if key_column:
                selected = f"{selected}, {key_column}"
            rows = conn.execute(f"SELECT {selected} FROM ai_model_configs").fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        # 异常原文可能带绝对路径；诊断包里只留结论，细节进本地日志。
        return {
            "source": db_path.name,
            "available": False,
            "models": [],
            "error": "模型配置库只读打开失败",
        }
    models = [
        {
            "name": row[0],
            "provider": row[1],
            "model": row[2],
            "base_url": _sanitize_url(row[3]),
            "enabled": bool(row[4]),
            "is_default": bool(row[5]),
            "has_key": (bool(row[6]) if key_column else None),
        }
        for row in rows
    ]
    return {"source": db_path.name, "available": True, "models": models}


def _collect_environment() -> dict[str, Any]:
    """只保留中性标签 + 值；**环境变量名本身也是泄漏面**，不再原样写入。"""
    hints: dict[str, str] = {}
    if os.environ.get("APP_ENV"):
        hints["运行环境"] = str(os.environ["APP_ENV"])
    if os.environ.get("AI_PROVIDER_TYPE"):
        hints["提供方类型"] = str(os.environ["AI_PROVIDER_TYPE"])
    if os.environ.get("AI_PROVIDER_MODEL"):
        hints["提供方模型"] = str(os.environ["AI_PROVIDER_MODEL"])
    if os.environ.get("OLLAMA_BASE_URL"):
        hints["本地模型地址"] = _sanitize_url(os.environ["OLLAMA_BASE_URL"]) or ""
    runtime_data_dir = resolve_data_dir()
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python_version": sys.version.split()[0],
        # 运行期实际目录（不是仓库默认值），用户名按统一规则遮掉。
        "data_dir": sanitize_text(str(runtime_data_dir)),
        "log_dir": sanitize_text(str(runtime_data_dir / "logs")),
        "app_env": hints,
    }


def _collect_dependencies() -> dict[str, Any]:
    packages: list[dict[str, str]] = []
    try:
        for dist in metadata.distributions():
            name = dist.metadata.get("Name")
            if not name:
                continue
            packages.append({"name": name, "version": dist.version})
    except Exception as exc:  # noqa: BLE001 - 元数据损坏不应让整包失败
        return {"available": False, "error": f"{type(exc).__name__}: {exc}", "packages": []}
    packages.sort(key=lambda item: item["name"].lower())
    return {
        "available": True,
        "total": len(packages),
        "packages": packages[:_MAX_DEPENDENCIES],
        "truncated": len(packages) > _MAX_DEPENDENCIES,
    }


def _collect_service_snapshot(base_url: str | None) -> dict[str, Any]:
    target = base_url or discover_service()
    if not target:
        return {
            "base_url": None,
            "reachable": False,
            "note": "未发现正在运行的网关（已尝试 8080-8089）；服务未启动时属正常",
        }
    health = http_json(f"{target}/v1/health")
    ready = http_json(f"{target}/v1/ready")
    return {
        "base_url": target,
        "reachable": bool(health["ok"]),
        "health": health,
        "ready": ready,
    }


def _console_dist_dir(base: Path) -> Path:
    """驾驶舱构建产物目录：与网关 ``console_static.resolve_console_dist`` 同一顺序。"""
    configured = (os.environ.get(_CONSOLE_DIST_ENV) or "").strip()
    if configured:
        return Path(configured).expanduser()
    installed = base / "console"
    if installed.is_dir():
        return installed
    return base / "apps" / "console" / "console" / "dist"


def _local_asset_path(dist: Path, reference: str) -> str | None:
    """把 index.html 里的引用归一成本地相对路径；外部 URL / 锚点一律忽略。"""
    value = reference.strip().split("#", 1)[0].split("?", 1)[0]
    if not value or value.startswith(
        ("http://", "https://", "//", "data:", "mailto:", "javascript:")
    ):
        return None
    value = value.lstrip("/")
    if not value:
        return None
    root = dist.resolve()
    try:
        candidate = (root / value).resolve()
        return candidate.relative_to(root).as_posix()
    except (OSError, ValueError):
        return None


def _http_head(url: str, timeout: float = 1.0) -> dict[str, Any]:
    """只取状态码的 HEAD 探测（绕开本机代理），不下载正文。"""
    request = urllib.request.Request(url, method="HEAD")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            return {"status": status, "ok": 200 <= status < 400}
    except urllib.error.HTTPError as exc:
        return {"status": int(exc.code), "ok": False}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"status": None, "ok": False, "error": type(exc).__name__}


def _collect_static_assets(base: Path, service: dict[str, Any]) -> dict[str, Any]:
    """静态资源可达性：先查文件是否存在，服务在跑时再补一次 HEAD 探测。

    白屏的典型真因是 index.html 还在、它引用的 ``/assets/main-*.js`` 不在（404）。
    """
    dist = _console_dist_dir(base)
    payload: dict[str, Any] = {
        "dist_dir": mask_user_path(str(dist)),
        "dist_exists": dist.is_dir(),
        "index_present": (dist / "index.html").is_file(),
        "referenced": [],
        "missing": [],
        "complete": False,
        "http_checked": False,
        "http": [],
        "note": (
            "只读取 index.html 里的本地引用并检查文件是否存在；服务可达时再补一次 HEAD 探测。"
        ),
    }
    references: list[str] = []
    if payload["index_present"]:
        try:
            html = (dist / "index.html").read_text(encoding="utf-8", errors="replace")[
                :_MAX_INDEX_BYTES
            ]
        except OSError:
            html = ""
        for match in _ASSET_REF_RE.finditer(html):
            relative = _local_asset_path(dist, match.group(1))
            if relative and relative not in references:
                references.append(relative)
    else:
        payload["missing"].append("index.html")

    for relative in references[:_MAX_STATIC_ASSETS]:
        target = dist / relative
        entry: dict[str, Any] = {"path": relative, "exists": target.is_file()}
        try:
            entry["bytes"] = target.stat().st_size if entry["exists"] else None
        except OSError:
            entry["bytes"] = None
        if not entry["exists"]:
            payload["missing"].append(relative)
        payload["referenced"].append(entry)

    service_url = service.get("base_url") if service else None
    if service_url and references:
        checked: list[dict[str, Any]] = []
        for url_path in ["/", *[f"/{item}" for item in references[:_MAX_STATIC_ASSETS]]]:
            result = _http_head(f"{service_url}{url_path}")
            result["path"] = url_path
            checked.append(result)
            if url_path != "/" and result.get("status") == 404:
                relative = url_path.lstrip("/")
                if relative not in payload["missing"]:
                    payload["missing"].append(relative)
        payload["http_checked"] = True
        payload["http"] = checked

    payload["complete"] = payload["index_present"] and not payload["missing"]
    if payload["complete"]:
        payload["verdict"] = "驾驶舱静态资源齐全"
    elif not payload["dist_exists"] or not payload["index_present"]:
        payload["verdict"] = "找不到驾驶舱构建产物（index.html），页面会白屏或 404"
    else:
        payload["verdict"] = f"缺少 {len(payload['missing'])} 个静态资源，页面可能白屏"
    return payload


def _database_failure_state(exc: sqlite3.Error) -> str:
    """区分"文件损坏"与"这次没检查成"（被占用 / 权限不足）。"""
    detail = str(exc).lower()
    if any(token in detail for token in ("malformed", "corrupt", "not a database", "disk image")):
        return "failed"
    return "error"


def _quick_check_database(path: Path, relative: str) -> dict[str, Any]:
    """只读 + ``PRAGMA quick_check``；只保留结论与错误摘要，不复制任何内容。"""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    result: dict[str, Any] = {
        "path": relative,
        "bytes": size,
        "result": "error",
        "messages": [],
    }
    if size > _MAX_DB_CHECK_BYTES:
        result["result"] = "skipped"
        result["messages"] = ["数据库超过单次检查上限，未做 quick_check"]
        return result
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result["result"] = _database_failure_state(exc)
        result["messages"] = [f"{type(exc).__name__}"]
        return result
    try:
        conn.execute("PRAGMA busy_timeout=2000")
        rows = conn.execute("PRAGMA quick_check").fetchall()
        messages = [str(row[0])[:200] for row in rows if row]
        ok = bool(messages) and all(message.lower() == "ok" for message in messages)
        result["result"] = "ok" if ok else "failed"
        result["messages"] = messages[:_MAX_DB_MESSAGES]
    except sqlite3.Error as exc:
        result["result"] = _database_failure_state(exc)
        result["messages"] = [f"{type(exc).__name__}: {str(exc)[:160]}"]
    finally:
        conn.close()
    return result


def _collect_db_check(data_dir: Path) -> dict[str, Any]:
    """数据目录下的 SQLite 文件逐个 quick_check；按文件头识别，不按扩展名。"""
    payload: dict[str, Any] = {
        "root": mask_user_path(str(data_dir)),
        "databases": [],
        "note": "以只读方式执行 PRAGMA quick_check；只记录结论与错误摘要，不读取任何行内容。",
    }
    if not _safe_is_dir(data_dir):
        payload["note"] = "数据目录不存在或不可访问，没有可检查的数据库"
        return payload
    try:
        for root, dirs, names in os.walk(data_dir):
            dirs[:] = sorted(
                item for item in dirs if item not in {"node_modules", ".git", "__pycache__"}
            )
            for name in sorted(names):
                if name.endswith(_SQLITE_SIDECARS):
                    continue
                path = Path(root) / name
                try:
                    if path.stat().st_size <= 0:
                        continue
                    with open(path, "rb") as handle:
                        header = handle.read(16)
                except OSError:
                    continue
                if header != _SQLITE_MAGIC:
                    continue
                relative = path.relative_to(data_dir).as_posix()
                payload["databases"].append(_quick_check_database(path, relative))
                if len(payload["databases"]) >= _MAX_DB_FILES:
                    payload["truncated"] = True
                    return payload
    except OSError:
        payload["error"] = "数据目录无法遍历"
    return payload


def _configured_api_port(default: int = 8080) -> int:
    """与 ``GatewayService`` 同一优先级：环境变量 > 持久化配置 > 默认端口。"""
    raw = (os.environ.get("LIUHAO_API_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    try:
        from .service import load_saved_port

        saved = load_saved_port()
    except Exception:  # noqa: BLE001 - 诊断包不能因为读配置失败而中断
        saved = None
    return saved or default


def _error_text(exc: BaseException | None) -> str | None:
    if exc is None:
        return None
    stage = getattr(exc, "liuhao_probe_step", None)
    detail = f"{type(exc).__name__}: {exc}"
    if stage:
        detail += f"（停在这一步：{stage}）"
    return _mask_drive_paths(detail)


def _exc_detail(exc: BaseException | None) -> dict[str, Any] | None:
    """异常的结构化副本：类型 / errno / winerror / 停在哪一步 / 折叠过路径的原文。"""
    if exc is None:
        return None
    return {
        "type": type(exc).__name__,
        "errno": getattr(exc, "errno", None),
        "winerror": getattr(exc, "winerror", None),
        "stage": getattr(exc, "liuhao_probe_step", None),
        "message": _mask_drive_paths(f"{type(exc).__name__}: {exc}")[:200],
    }


def _free_bytes_with_seam(path: Path) -> int | None:
    """数据目录所在卷的剩余字节；``LIUHAO_DIAG_FREE_BYTES`` 仅供演练注入。

    "磁盘满"无法在用户机器上真造（要先把盘写满），所以加一个**只影响读数**的
    演练开关：注入后 ``startup_check.json.data_free_bytes`` 就是该值，结论仍然
    只从字段推出，不会出现"字段说正常、结论说满了"的错位。生产不设置该变量时
    完全走真实 ``shutil.disk_usage``。
    """
    from ..observability.boot_report import free_bytes

    injected = (os.environ.get("LIUHAO_DIAG_FREE_BYTES") or "").strip()
    if injected:
        try:
            return max(0, int(injected))
        except ValueError:
            pass
    return free_bytes(path)


def _collect_startup_check(
    data_dir: Path, log_dir: Path, base: Path, port: int
) -> dict[str, Any]:
    """离线启动自检：与 ``evt=startup.check`` 同一口径，但**不经过 HTTP**。

    网关根本没起来时也能回答三个问题：哪一步坏了（``data_writable`` /
    ``port_bindable`` / ``db_open`` / ``console_index``）、为什么（错误原文与
    winerror/errno）、下一步做什么（``actions``）。探测函数全部有 2 秒上限，
    不会因为安全软件卡住而让"一键诊断"自己卡住。
    """
    from ..observability.boot_report import (
        display_path,
        port_state,
        probe_db,
        probe_writable,
        provider_summary,
    )

    data_ok, data_exc = probe_writable(data_dir, "data_dir")
    log_ok, log_exc = probe_writable(log_dir, "log_dir")
    bindable, bind_state = port_state("127.0.0.1", int(port))

    databases: list[dict[str, Any]] = []
    data_is_dir = _safe_is_dir(data_dir)
    if data_is_dir:
        try:
            for root, dirs, names in os.walk(data_dir):
                dirs[:] = sorted(
                    item
                    for item in dirs
                    if item not in {"node_modules", ".git", "__pycache__"}
                )
                for name in sorted(names):
                    if name.endswith(_SQLITE_SIDECARS):
                        continue
                    path = Path(root) / name
                    try:
                        if path.stat().st_size <= 0:
                            continue
                        with open(path, "rb") as handle:
                            header = handle.read(len(_SQLITE_MAGIC))
                    except OSError:
                        continue
                    if header != _SQLITE_MAGIC:
                        continue
                    ok, message = probe_db(path)
                    databases.append(
                        {
                            "path": display_path(path, data_dir),
                            "open": ok,
                            "message": message or None,
                        }
                    )
                    if len(databases) >= _MAX_DB_FILES:
                        break
                if len(databases) >= _MAX_DB_FILES:
                    break
        except OSError:
            databases.append({"path": "data", "open": None, "message": "数据目录无法遍历"})

    dist = _console_dist_dir(base)
    provider = provider_summary()
    free_value = _free_bytes_with_seam(data_dir)

    actions: list[str] = []
    if not data_ok:
        actions.append(
            "数据目录不可写：授予写权限，或把数据目录改到 "
            "%LOCALAPPDATA%\\LiuHao\\data，并检查安全软件拦截"
        )
    if not log_ok:
        actions.append(
            "日志目录不可写：同上处理；本次证据只留在 bootstrap.log"
        )
    if isinstance(free_value, int) and 0 <= free_value < _MIN_FREE_BYTES:
        actions.append(
            f"磁盘剩余空间不足（{free_value // (1024 * 1024)} MB）：清理磁盘，"
            "或把数据目录换到其它剩余空间充足的卷"
        )
    if not bindable:
        actions.append(
            f"端口 {port} 不可用（port_state={bind_state}）：换端口，或在任务管理器结束占用该端口的进程"
        )
    for item in databases:
        if item["open"] is False:
            actions.append(
                f"{item['path']} 打不开：先备份数据目录，再修复权限或把损坏文件隔离后重启"
            )
    if not (dist / "index.html").is_file():
        actions.append(
            "驾驶舱构建产物缺失：用同一个安装包覆盖安装（补齐 console 目录），不要只复制可执行文件"
        )

    return {
        "schema": 1,
        "data_dir": display_path(data_dir, tail=2),
        "data_path_kind": _path_kind(data_dir),
        "data_writable": data_ok,
        "data_error": _error_text(data_exc),
        "data_error_detail": _exc_detail(data_exc),
        "data_free_bytes": free_value,
        "log_dir": display_path(log_dir, tail=2),
        "log_path_kind": _path_kind(log_dir),
        "log_writable": log_ok,
        "log_error": _error_text(log_exc),
        "log_error_detail": _exc_detail(log_exc),
        "host": "127.0.0.1",
        "port": int(port),
        "port_bindable": bindable,
        "port_state": bind_state,
        "databases": databases,
        "db_open": all(item["open"] is not False for item in databases),
        "console_dist": display_path(dist, tail=3),
        "console_index": (dist / "index.html").is_file(),
        "provider": provider["provider"],
        "model": provider["model"],
        "key_fp": provider["key_fp"],
        "provider_source": provider["source"],
        "actions": actions,
        "note": (
            "本文件由启动器 --diagnose 离线生成，不依赖网关；"
            "字段与日志里的 evt=startup.check 一致。"
        ),
    }


def _collect_bootstrap_log() -> str:
    """数据/日志目录都不可写时唯一留下的证据（独立于业务数据目录）。"""
    try:
        from .. import bootstrap_log

        path = bootstrap_log.bootstrap_log_path()
        text = bootstrap_log.read_bootstrap_log(_MAX_BOOTSTRAP_LINES)
    except Exception as exc:  # noqa: BLE001 - 引导日志取不到不能拖垮诊断包
        return f"[引导日志不可用] {type(exc).__name__}: {exc}"
    if not text:
        return (
            f"没有引导日志（期望位置：{mask_user_path(str(path))}）。"
            "这通常意味着数据目录与日志目录都能正常写入；"
            "只有它们都不可写时才会在这里留下 evt=boot.*.unwritable。"
        )
    return f"路径：{mask_user_path(str(path))}\n{text}"


def _collect_recent_events(logs: dict[str, str]) -> str:
    """最近 20 条 ``evt=`` 结构化事件（跨日志按采集顺序取尾部）。"""
    events: list[str] = []
    for name, text in logs.items():
        if not name.startswith("logs/") or not name.endswith(".txt"):
            continue
        for line in text.splitlines():
            if "evt=" in line:
                events.append(f"[{name}] {line}")
    if not events:
        return "没有发现 evt= 事件（旧版日志或日志级别过高）。"
    return "\n".join(events[-_MAX_EVENT_LINES:])


def _classify_signal_line(line: str) -> dict[str, str] | None:
    """从一行日志里取模型失败的机器信号（provider / gateway 两处格式都认）。"""
    match = _SIGNAL_CODE_RE.search(line)
    if match:
        category = _SIGNAL_CATEGORIES.get(match.group(1))
        if category:
            return {"category": category, "code": match.group(1), "source": "reason"}
    status = _SIGNAL_STATUS_RE.search(line)
    if status:
        code = int(status.group(1))
        category = _STATUS_CATEGORIES.get(code)
        if category is None and 500 <= code < 600:
            category = "upstream_error"
        if category:
            return {"category": category, "code": str(code), "source": "status"}
    for category, tokens in _LOG_TEXT_SIGNALS:
        for token in tokens:
            if token in line:
                return {"category": category, "code": token, "source": "text"}
    return None


def _collect_log_signals(logs: dict[str, str]) -> dict[str, Any]:
    """扫描已收集的日志尾部，按"最新一次失败"给出结论。"""
    counts: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    newest: dict[str, Any] | None = None
    for name, text in logs.items():
        if not name.startswith("logs/") or not name.endswith(".txt"):
            continue
        file_hit: dict[str, Any] | None = None
        for number, line in enumerate(text.splitlines(), 1):
            hit = _classify_signal_line(line)
            if not hit:
                continue
            counts[hit["category"]] = counts.get(hit["category"], 0) + 1
            if len(samples) < _MAX_SIGNAL_SAMPLES:
                samples.append(
                    {
                        "file": name,
                        "line": number,
                        "category": hit["category"],
                        "code": hit["code"],
                        "source": hit["source"],
                    }
                )
            file_hit = {
                "file": name,
                "line": number,
                "category": hit["category"],
                "code": hit["code"],
                "source": hit["source"],
            }
        # 日志按修改时间倒序传入：最新文件里的最后一条信号就是最近一次失败。
        if file_hit is not None and newest is None:
            newest = file_hit
    return {"counts": counts, "samples": samples, "newest": newest}


def _proxy_configured() -> bool:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        if (os.environ.get(name) or "").strip():
            return True
    return False


def _probe_endpoint(model: dict[str, Any]) -> dict[str, Any]:
    """只做 DNS + TCP 直连探测：不发送任何请求，不使用、也不读取密钥。"""
    raw = str(model.get("base_url") or "")
    parsed = urlsplit(raw)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    proxy = _proxy_configured()
    entry: dict[str, Any] = {
        "model": model.get("name"),
        "provider": model.get("provider"),
        "host": host,
        "port": port,
        "proxy_configured": proxy,
        "dns": "unknown",
        "tcp": "unknown",
    }
    if not host:
        entry["note"] = "未配置 base_url，无法直连探测"
        return entry
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        entry["dns"] = "ok" if infos else "failed"
    except (socket.gaierror, OSError, UnicodeError) as exc:
        entry["dns"] = "failed"
        entry["error"] = type(exc).__name__
        return entry
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=_ENDPOINT_TIMEOUT):
            pass
        entry["tcp"] = "reachable"
    except OSError as exc:
        entry["tcp"] = "unreachable"
        entry["error"] = type(exc).__name__
        entry["errno"] = getattr(exc, "errno", None)
        if proxy:
            entry["note"] = "检测到代理配置；直连失败不代表模型服务不可达"
    entry["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return entry


def _collect_model_health(
    registry: dict[str, Any], logs: dict[str, str], probe_network: bool
) -> dict[str, Any]:
    """模型健康结论：日志机器信号（最近一次失败）+ base_url 直连探测。"""
    models = [item for item in (registry.get("models") or []) if isinstance(item, dict)]
    enabled = [item for item in models if item.get("enabled", True)]
    models_without_key = [
        str(item.get("name") or "未命名模型")
        for item in enabled
        if item.get("has_key") is False
        and str(item.get("provider") or "").strip().lower() in _KEY_REQUIRED_PROVIDERS
    ]
    signals = _collect_log_signals(logs)
    endpoints: list[dict[str, Any]] = []
    if probe_network:
        configured = [item for item in models if str(item.get("base_url") or "").strip()]
        endpoints = [_probe_endpoint(item) for item in configured[:_MAX_ENDPOINT_PROBES]]

    reachable = [item for item in endpoints if item["tcp"] == "reachable"]
    unreachable = [item for item in endpoints if item["tcp"] == "unreachable"]
    if not probe_network:
        endpoint_verdict = "skipped"
    elif reachable and unreachable:
        endpoint_verdict = "partial"
    elif unreachable:
        endpoint_verdict = "unreachable"
    elif reachable:
        endpoint_verdict = "reachable"
    else:
        endpoint_verdict = "unknown"

    newest = signals.get("newest") or {}
    return {
        "note": (
            "日志信号来自 provider 与网关写入的 reason=/kind=/status= 字段；"
            "端点只做 DNS/TCP 直连探测，不发送请求、不读取密钥。"
        ),
        "models_configured": len(models),
        "models_without_key": models_without_key,
        "log_signals": signals,
        "log_verdict": newest.get("category") or "unknown",
        "endpoints": endpoints,
        "endpoint_verdict": endpoint_verdict,
        "network_probe_enabled": probe_network,
    }


def _evidence(file: str, field: str, value: Any = None) -> dict[str, Any]:
    entry: dict[str, Any] = {"file": file, "field": field}
    if value is not None:
        entry["value"] = value
    return entry


def _probe_port(url: Any) -> int | None:
    """从 ``http://127.0.0.1:8091/v1/health`` 取端口；解析不了就返回 None。"""
    try:
        return urlsplit(str(url)).port
    except (TypeError, ValueError):
        return None


def format_bytes(size: int | float) -> str:
    """把字节数变成用户能读的大小（诊断包提示用）。"""
    value = float(max(size, 0))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def service_guidance(
    *,
    running: bool,
    last_error: str | None = None,
    port: int | None = None,
    requested_port: int | None = None,
    stopped_by_user: bool = False,
    data_dir_writable: bool | None = None,
    process_alive: bool = False,
) -> dict[str, str]:
    """把网关状态翻译成"发生了什么 + 下一步做什么"。

    托盘菜单/桌面窗口在服务被杀、端口占满、数据目录只读这三种情况下都必须给出
    中文解释和可执行动作，而不是只显示一个状态词或空白。
    """
    if running:
        target = f"127.0.0.1:{port}" if port else "本机"
        service = f"网关：运行中（{target}）"
        if requested_port and port and port != requested_port:
            return {
                "service": service,
                "next": (
                    f"端口 {requested_port} 被其它程序占用，已自动改用 {port}。"
                    f"若要固定端口，请关闭占用 {requested_port} 的程序后点「重新启动服务」。"
                ),
            }
        return {
            "service": service,
            "next": "一切正常。遇到异常时点「生成诊断包」，把 zip 发给技术支持即可。",
        }
    if stopped_by_user:
        return {
            "service": "网关：已按你的要求停止",
            "next": "点托盘菜单的「重新启动服务」可再次启动；排障时可直接生成诊断包。",
        }
    if process_alive:
        # 进程在、健康接口不通：和"进程不存在"必须分开说，否则用户会去杀一个已经在跑的进程。
        return {
            "service": "网关：进程在，但健康接口不通",
            "next": (
                "网关进程还在，但 /v1/health 没有响应。点托盘菜单的「重新启动服务」原地重启；"
                "仍不行就生成诊断包，里面的 health.json 会写明超时原因。"
            ),
        }
    if data_dir_writable is False:
        return {
            "service": "网关：启动失败（数据目录不可写）",
            "next": (
                "数据目录需要写入权限。请换到当前用户可写的位置"
                "（例如 %LOCALAPPDATA%\\LiuHao\\data），或让管理员授予写权限，"
                "然后点「重新启动服务」。"
            ),
        }
    error = (last_error or "").strip()
    if "全部被占用" in error or "找不到空闲端口" in error:
        return {
            "service": "网关：启动失败（端口被占用）",
            "next": (
                "从指定端口起的 100 个端口都被其它程序占用。请在任务管理器结束占用端口的程序后"
                "点「重新启动服务」；不确定是谁占用时生成诊断包，"
                "ports.json 会写明进程名和 PID。"
            ),
        }
    if "被占用" in error:
        # 默认不自动换端口时的原文："端口 8095 已被占用。为避免地址变化…"
        target = port or requested_port
        return {
            "service": f"网关：启动失败（端口 {target} 被占用）",
            "next": (
                f"端口 {target} 被其它程序占用。请在任务管理器结束占用该端口的程序后"
                "点「重新启动服务」；不确定是哪个程序时生成诊断包，"
                "ports.json 会写明进程名和 PID。"
            ),
        }
    if error:
        return {
            "service": f"网关：启动失败（{error}）",
            "next": "先点「重新启动服务」重试一次；仍失败就生成诊断包发给技术支持，包里的 diagnosis.json 会指出原因。",
        }
    return {
        "service": "网关：已停止（进程不存在）",
        "next": "点「重新启动服务」恢复；若反复停止，生成诊断包发给技术支持。",
    }


def _build_diagnosis(
    installation: dict[str, Any],
    ports: dict[str, Any],
    static_assets: dict[str, Any],
    model_health: dict[str, Any],
    db_check: dict[str, Any],
    ready_probe: dict[str, Any] | None = None,
    processes: dict[str, Any] | None = None,
    service: dict[str, Any] | None = None,
    startup_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把各路采集结果收敛成 9 条"能不能判定"的结论，逐条带证据定位。"""
    ready_probe = ready_probe or {}
    processes = processes or {}
    service = service or {}
    startup_check = startup_check or {}
    checks: list[dict[str, Any]] = []

    probes = ready_probe.get("probes") or []
    # 桌面壳生成时带 ``expected_port``：只看本应用端口的响应。同一台机器上
    # 还跑着别的网关（开发机常见）时，否则会把"别人活着"当成"我们的服务活着"。
    expected_port = service.get("expected_port")
    if expected_port:
        probes = [probe for probe in probes if _probe_port(probe.get("url")) == expected_port]
    gateway_hits = [
        probe
        for probe in probes
        if probe.get("reachable")
        and isinstance(probe.get("body"), dict)
        and probe["body"].get("service") == "liuhao-gateway"
    ]
    reachable = [probe for probe in probes if probe.get("reachable")]
    if expected_port and "gateway_for_port" in processes:
        gateway_processes = processes.get("gateway_scoped") or []
        gateway_count = processes.get("gateway_for_port")
    else:
        gateway_processes = processes.get("gateway") or []
        gateway_count = processes.get("gateway_matched")
    listening = [entry for entry in ports.get("entries") or [] if entry.get("listening")]
    if expected_port:
        listening = [entry for entry in listening if entry.get("port") == expected_port]
    if service.get("reachable") or gateway_hits:
        checks.append(
            {
                "id": "service_not_running",
                "title": "服务未启动 / 已停止",
                "status": "clear",
                "conclusion": "已探测到鎏灏网关的 /v1/health 响应，服务正在运行",
                "evidence": [
                    _evidence("ready_probe.json", "probes", gateway_hits[:3]),
                    _evidence("service.json", "reachable", service.get("reachable")),
                ],
            }
        )
    elif reachable or gateway_processes or listening:
        checks.append(
            {
                "id": "service_not_running",
                "title": "服务未启动 / 已停止",
                "status": "inconclusive",
                "conclusion": (
                    "端口上有响应或存在相关进程，但没有确认到鎏灏网关的 /v1/health："
                    "服务可能卡在启动，或端口被其它程序占用"
                ),
                "evidence": [
                    _evidence("ready_probe.json", "probes", (reachable or gateway_hits)[:3]),
                    _evidence("processes.json", "gateway_matched", gateway_count),
                    _evidence("ports.json", "entries", listening[:3]),
                ],
            }
        )
    else:
        checks.append(
            {
                "id": "service_not_running",
                "title": "服务未启动 / 已停止",
                "status": "detected",
                "conclusion": "候选端口上没有任何鎏灏网关响应，也没有发现网关子进程：服务没有启动",
                "evidence": [
                    _evidence(
                        "ready_probe.json",
                        f"probes(port={expected_port})" if expected_port else "probes",
                        probes[:3],
                    ),
                    _evidence(
                        "processes.json",
                        "gateway_for_port" if expected_port else "gateway_matched",
                        0,
                    ),
                    _evidence("ports.json", "verdict", ports.get("verdict")),
                ],
            }
        )

    integrity = installation.get("integrity") or {}
    missing = integrity.get("missing") or []
    if missing:
        checks.append(
            {
                "id": "installation_incomplete",
                "title": "程序 / 安装目录不完整",
                "status": "detected",
                "conclusion": f"程序目录缺少 {len(missing)} 个必需项：{'、'.join(missing)}",
                "evidence": [_evidence("installation.json", "integrity.missing", missing)],
            }
        )
    else:
        checks.append(
            {
                "id": "installation_incomplete",
                "title": "程序 / 安装目录不完整",
                "status": "clear",
                "conclusion": "安装完整性清单里的必需项都存在",
                "evidence": [_evidence("installation.json", "integrity.complete", True)],
            }
        )

    unwritable: list[str] = []
    failed_labels: list[str] = []
    for label, key in (
        ("数据目录", "data_dir_write_probe"),
        ("日志目录", "logs_dir_write_probe"),
    ):
        probe = installation.get(key) or {}
        if not probe.get("writable"):
            reason = (probe.get("error") or {}).get("type") or "未知原因"
            failed_labels.append(label)
            unwritable.append(f"{label}（{reason}）")
    if unwritable:
        checks.append(
            {
                "id": "directory_not_writable",
                "title": "、".join(failed_labels) + "不可写",
                "status": "detected",
                "conclusion": "写入探测失败：" + "、".join(unwritable),
                "evidence": [
                    _evidence("installation.json", "data_dir_write_probe"),
                    _evidence("installation.json", "logs_dir_write_probe"),
                ],
            }
        )
    else:
        checks.append(
            {
                "id": "directory_not_writable",
                "title": "数据目录 / 日志目录不可写",
                "status": "clear",
                "conclusion": "数据目录与日志目录都通过了真实写入探测",
                "evidence": [
                    _evidence("installation.json", "data_dir_write_probe.writable", True),
                    _evidence("installation.json", "logs_dir_write_probe.writable", True),
                ],
            }
        )

    conflicts = ports.get("port_conflicts") or []
    known = [item for item in conflicts if item.get("owner")]
    if known:
        detail = "、".join(
            f"{item['port']} 被 {(item['owner'] or {}).get('process') or '未知进程'}"
            f"（PID {(item['owner'] or {}).get('pid')}）占用"
            for item in known
        )
        checks.append(
            {
                "id": "port_conflict",
                "title": "端口被占用",
                "status": "detected",
                "conclusion": detail,
                "evidence": [_evidence("ports.json", "port_conflicts", known)],
            }
        )
    elif conflicts:
        ports_text = "、".join(str(item["port"]) for item in conflicts)
        checks.append(
            {
                "id": "port_conflict",
                "title": "端口被占用",
                "status": "inconclusive",
                "conclusion": f"端口 {ports_text} 正在监听，但无法识别占用进程（可能正是鎏灏自身）",
                "evidence": [_evidence("ports.json", "port_conflicts", conflicts)],
            }
        )
    else:
        checks.append(
            {
                "id": "port_conflict",
                "title": "端口被占用",
                "status": "clear",
                "conclusion": "未发现被其它进程占用的端口",
                "evidence": [_evidence("ports.json", "port_conflicts", [])],
            }
        )

    log_verdict = model_health.get("log_verdict") or "unknown"
    keyless = model_health.get("models_without_key") or []
    if log_verdict == "key_invalid":
        checks.append(
            {
                "id": "model_key_invalid",
                "title": "模型 Key 无效",
                "status": "detected",
                "conclusion": "日志显示最近一次模型调用因 Key 无效 / 被吊销被拒绝（401 / 403）",
                "evidence": [
                    _evidence(
                        "model_health.json",
                        "log_signals.newest",
                        model_health["log_signals"].get("newest"),
                    )
                ],
            }
        )
    elif keyless:
        checks.append(
            {
                "id": "model_key_invalid",
                "title": "模型 Key 无效",
                "status": "detected",
                "conclusion": f"这些已启用的模型没有填写 Key：{'、'.join(keyless)}",
                "evidence": [
                    _evidence("model_health.json", "models_without_key", keyless),
                    _evidence("providers.json", "registry.models"),
                ],
            }
        )
    elif model_health.get("models_configured") == 0:
        # 全新安装：一个模型都没配。这不是"无法判定"，就是"Key 没配"。
        checks.append(
            {
                "id": "model_key_invalid",
                "title": "模型 Key 没配",
                "status": "detected",
                "conclusion": "还没有配置任何模型，AI 功能当前不可用（没有可用的 Key）",
                "evidence": [
                    _evidence("model_health.json", "models_configured", 0),
                    _evidence("providers.json", "registry.models", []),
                ],
            }
        )
    elif log_verdict in {
        "quota_exhausted",
        "rate_limited",
        "model_not_found",
        "network_unreachable",
        "upstream_error",
    }:
        checks.append(
            {
                "id": "model_key_invalid",
                "title": "模型 Key 无效",
                "status": "clear",
                "conclusion": f"最近一次模型失败原因是 {log_verdict}，不是 Key 无效",
                "evidence": [_evidence("model_health.json", "log_signals.newest")],
            }
        )
    else:
        checks.append(
            {
                "id": "model_key_invalid",
                "title": "模型 Key 无效",
                "status": "inconclusive",
                "conclusion": "日志里没有鉴权失败记录，无法判定 Key 是否有效（需要在「系统设置 → 模型管理」测试一次）",
                "evidence": [_evidence("model_health.json", "log_signals.counts", {})],
            }
        )

    endpoint_verdict = model_health.get("endpoint_verdict") or "unknown"
    endpoints = model_health.get("endpoints") or []
    if log_verdict == "network_unreachable" or endpoint_verdict in {"unreachable", "partial"}:
        checks.append(
            {
                "id": "model_network_unreachable",
                "title": "模型服务网络不通",
                "status": "detected",
                "conclusion": "模型端点 DNS / TCP 直连失败，或日志里记录了不可达 / 超时",
                "evidence": [
                    _evidence("model_health.json", "endpoints", endpoints),
                    _evidence("model_health.json", "log_signals.counts"),
                ],
            }
        )
    elif endpoint_verdict == "reachable":
        checks.append(
            {
                "id": "model_network_unreachable",
                "title": "模型服务网络不通",
                "status": "clear",
                "conclusion": "已配置模型的端点 DNS / TCP 直连均可达",
                "evidence": [_evidence("model_health.json", "endpoints", endpoints)],
            }
        )
    else:
        checks.append(
            {
                "id": "model_network_unreachable",
                "title": "模型服务网络不通",
                "status": "inconclusive",
                "conclusion": "没有可用的端点探测结果（未配置 base_url 或本次跳过了网络探测）",
                "evidence": [_evidence("model_health.json", "endpoint_verdict", endpoint_verdict)],
            }
        )

    assets_missing = static_assets.get("missing") or []
    if assets_missing:
        checks.append(
            {
                "id": "console_assets_missing",
                "title": "静态资源缺失（白屏）",
                "status": "detected",
                "conclusion": f"驾驶舱缺少 {len(assets_missing)} 个静态资源：{'、'.join(assets_missing[:5])}",
                "evidence": [
                    _evidence("static_assets.json", "missing", assets_missing),
                    _evidence("static_assets.json", "http"),
                ],
            }
        )
    else:
        checks.append(
            {
                "id": "console_assets_missing",
                "title": "静态资源缺失（白屏）",
                "status": "clear",
                "conclusion": static_assets.get("verdict") or "驾驶舱静态资源齐全",
                "evidence": [_evidence("static_assets.json", "referenced")],
            }
        )

    databases = db_check.get("databases") or []
    failed = [item for item in databases if item.get("result") == "failed"]
    errored = [item for item in databases if item.get("result") == "error"]
    if failed:
        names = "、".join(str(item.get("path")) for item in failed)
        checks.append(
            {
                "id": "database_corrupt",
                "title": "数据库文件损坏",
                "status": "detected",
                "conclusion": f"quick_check 未通过：{names}",
                "evidence": [_evidence("db_check.json", "databases", failed)],
            }
        )
    elif errored:
        checks.append(
            {
                "id": "database_corrupt",
                "title": "数据库文件损坏",
                "status": "inconclusive",
                "conclusion": "数据库无法完成 quick_check（可能被占用或权限不足），未能判定是否损坏",
                "evidence": [_evidence("db_check.json", "databases", errored)],
            }
        )
    else:
        checks.append(
            {
                "id": "database_corrupt",
                "title": "数据库文件损坏",
                "status": "clear",
                "conclusion": f"{len(databases)} 个数据库 quick_check 通过"
                if databases
                else "数据目录下没有发现 SQLite 数据库文件",
                "evidence": [_evidence("db_check.json", "databases", databases)],
            }
        )

    free = startup_check.get("data_free_bytes")
    probe_errors = [
        (probe.get("error") or {})
        for probe in (
            installation.get("data_dir_write_probe") or {},
            installation.get("logs_dir_write_probe") or {},
        )
    ]
    no_space = any(
        int(error.get("errno") or 0) == 28 or int(error.get("winerror") or 0) == 112
        for error in probe_errors
    )
    if no_space or (isinstance(free, int) and 0 <= free < _MIN_FREE_BYTES):
        shown = f"{free // (1024 * 1024)} MB" if isinstance(free, int) else "未知"
        checks.insert(
            0,
            {
                "id": "disk_space_low",
                "title": "磁盘剩余空间不足",
                "status": "detected",
                "conclusion": (
                    f"数据目录所在卷只剩 {shown}（低于 {_MIN_FREE_BYTES // (1024 * 1024)} MB）"
                    + ("，写入已失败（errno 28 / winerror 112）" if no_space else "")
                ),
                "evidence": [_evidence("startup_check.json", "data_free_bytes", free)],
            },
        )
    else:
        checks.insert(
            0,
            {
                "id": "disk_space_low",
                "title": "磁盘剩余空间不足",
                "status": "clear",
                "conclusion": "数据目录所在卷剩余空间充足"
                + (f"（{free // (1024 * 1024)} MB）" if isinstance(free, int) else ""),
                "evidence": [_evidence("startup_check.json", "data_free_bytes", free)],
            },
        )

    counts = {
        status: sum(1 for check in checks if check["status"] == status)
        for status in ("detected", "clear", "inconclusive")
    }
    # 顺序即处置优先级：headline 取首个 detected，必须和 SUMMARY.txt 的结论一致。
    checks.sort(key=lambda item: _check_priority(item.get("id")))
    for check in checks:
        if check["status"] != "clear":
            check["next_step"] = _NEXT_STEPS.get(
                check["id"], "生成诊断包并发送给技术支持，附上发生问题的时间点。"
            )
    detected = [check for check in checks if check["status"] == "detected"]
    inconclusive = [check for check in checks if check["status"] == "inconclusive"]
    if detected:
        headline = f"发现 {len(detected)} 个可定位的问题，建议先处理：{detected[0]['title']}"
    elif inconclusive:
        headline = f"未发现确定故障，但有 {len(inconclusive)} 项无法判定，需要按 checks 里的下一步补充验证"
    else:
        headline = "未发现已知故障"
    return {
        "note": "每条结论都对应包内文件与字段；服务未启动时同样可生成。",
        "offline": True,
        "headline": headline,
        "summary": counts,
        "checks": checks,
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _summary_value(value: Any, limit: int = 160) -> str:
    """把证据值压成一行，避免 SUMMARY.txt 里出现大段 JSON。"""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _summary_evidence(check: dict[str, Any], limit: int = 2) -> list[str]:
    """把 ``diagnosis.json`` 的 evidence 压成"文件 · 字段 = 值"的一行。

    只接受带 ``value`` 的条目：只指向文件、没有值的那种不算依据
    （"见 xxx 字段"读者仍然要自己去翻，等于没给结论）。
    """
    lines: list[str] = []
    # 纯指针（"见 xxx 字段"）不构成依据：没有值就等于没说，直接丢弃。
    items = [
        item
        for item in (check.get("evidence") or [])
        if isinstance(item, dict) and "value" in item
    ]
    for item in items[:limit]:
        file = item.get("file") or "?"
        field = item.get("field") or "?"
        lines.append(f"{file} · {field} = {_summary_value(item.get('value'))}")
    return lines


def _build_summary(
    diagnosis: dict[str, Any],
    startup_check: dict[str, Any],
    installation: dict[str, Any],
    ports: dict[str, Any],
    static_assets: dict[str, Any],
    db_check: dict[str, Any],
    bootstrap_text: str,
    event_text: str,
) -> str:
    """``SUMMARY.txt``：第一行一句人话结论，后面 3–5 条可从字段核对的依据。

    结论只由包内字段推出，不靠"看起来像"。规则是：先看硬字段（可写探测、
    剩余空间、端口占用、静态资源清单、quick_check），再看 ``diagnosis.json``
    的检测结论；都判不出来时明确写"无法确定"并指名该看哪个日志文件。
    """
    checks = {str(item.get("id")): item for item in diagnosis.get("checks") or []}

    def status(check_id: str) -> str:
        return str((checks.get(check_id) or {}).get("status") or "unknown")

    data_probe = installation.get("data_dir_write_probe") or {}
    logs_probe = installation.get("logs_dir_write_probe") or {}
    errors = [probe.get("error") or {} for probe in (data_probe, logs_probe)]
    free = startup_check.get("data_free_bytes")
    disk_full = (
        isinstance(free, int) and 0 <= free < _MIN_FREE_BYTES
    ) or any(
        int(error.get("errno") or 0) == 28 or int(error.get("winerror") or 0) == 112
        for error in errors
    )

    chosen: dict[str, Any] | None = None
    # 需要额外点名的证据（例如"两个目录各写一次探测的真实结果"）。
    extra_evidence: list[str] = []
    # 除唯一动作之外的建议：只进"依据"，不进"下一步"（用户一次只做一件事）。
    extra_actions: list[str] = []
    action = ""
    if disk_full:
        shown = f"{free // (1024 * 1024)} MB" if isinstance(free, int) else "未知"
        failed_write = status("directory_not_writable") == "detected" and any(
            int(error.get("errno") or 0) == 28 or int(error.get("winerror") or 0) == 112
            for error in errors
        )
        conclusion = (
            f"磁盘空间不足：数据目录所在卷只剩 {shown}（低于 {_MIN_FREE_BYTES // (1024 * 1024)} MB），"
            + ("写入已经失败（errno 28 / winerror 112）" if failed_write else "新建文件随时会失败")
        )
        action = "清理磁盘上数据目录所在卷的可用空间，然后重启鎏灏。"
        extra_actions.append("若无法清理，把数据目录换到其它剩余空间充足的卷后重启")
        chosen = checks.get("disk_space_low")
        # 磁盘满时目录写入探测可能仍然成功（只是剩余空间低），它不构成证据，
        # 真正的证据是 startup_check.json 的 data_free_bytes（已在第一条里）。
    elif status("directory_not_writable") == "detected":
        broken: list[str] = []
        failed_labels: list[str] = []
        for label, probe in (("数据目录", data_probe), ("日志目录", logs_probe)):
            if probe.get("writable"):
                continue
            failed_labels.append(label)
            error = probe.get("error") or {}
            reason = error.get("type") or "未知原因"
            code = error.get("winerror") or error.get("errno")
            stage = error.get("stage")
            kind = probe.get("path_kind")
            notes = [reason]
            if code:
                notes.append(f"错误码 {code}")
            if stage:
                notes.append(f"停在 {stage}")
            suffix = "，该路径存在但不是文件夹" if kind in ("file", "other") else ""
            broken.append(f"{label}（{'，'.join(notes)}{suffix}）")
        conclusion = (
            "、".join(failed_labels) + "不可写：" + "、".join(broken)
            + "，程序无法保存数据或日志"
        )
        if "数据目录" in failed_labels:
            action = "把数据目录改到 %LOCALAPPDATA%\\LiuHao\\data，然后重启鎏灏。"
        else:
            action = "把日志目录改到当前用户可写的位置，然后重启鎏灏。"
        extra_actions.extend(
            [
                "让管理员给当前目录授予写权限（右键属性 → 安全 → 编辑）",
                "检查安全软件是否拦截写入；日志目录不可写时把它同样换到可写位置",
            ]
        )
        probes_evidence = [
            f"installation.json · {field}.writable = {probe.get('writable')}"
            + (
                f"（{(probe.get('error') or {}).get('type')}，"
                f"winerror={(probe.get('error') or {}).get('winerror')}，"
                f"errno={(probe.get('error') or {}).get('errno')}"
                + (
                    f"，stage={(probe.get('error') or {}).get('stage')}"
                    if (probe.get('error') or {}).get("stage")
                    else ""
                )
                + "）"
                if probe.get("error")
                else ""
            )
            for field, probe in (
                ("data_dir_write_probe", data_probe),
                ("logs_dir_write_probe", logs_probe),
            )
        ]
        extra_evidence.extend(probes_evidence)
        chosen = checks.get("directory_not_writable")
    elif status("console_assets_missing") == "detected":
        missing = static_assets.get("missing") or []
        conclusion = (
            f"程序目录不完整：驾驶舱静态资源缺失 {len(missing)} 个"
            f"（{'、'.join(str(item) for item in missing[:5])}），打开会白屏"
        )
        action = "用同一个安装包覆盖安装一次（不要只复制可执行文件）。"
        extra_actions.append("源码运行时重新构建驾驶舱（console 目录下执行 npm run build）")
        chosen = checks.get("console_assets_missing")
    elif status("installation_incomplete") == "detected":
        missing = (installation.get("integrity") or {}).get("missing") or []
        conclusion = (
            f"程序目录不完整：缺少 {len(missing)} 个必需项"
            f"（{'、'.join(str(item) for item in missing[:5])}）"
        )
        action = "用同一个安装包覆盖安装一次（不要只复制可执行文件）。"
        extra_actions.append("若杀毒软件隔离了缺失文件，先从隔离区恢复再覆盖安装")
        chosen = checks.get("installation_incomplete")
    elif status("database_corrupt") == "detected":
        failed = [
            item for item in (db_check.get("databases") or []) if item.get("result") == "failed"
        ]
        names = "、".join(str(item.get("path")) for item in failed) or "未知库文件"
        conclusion = f"数据库文件损坏：{names} 的 quick_check 未通过"
        action = "把损坏的数据库文件改名成 .corrupt-日期 后重启鎏灏（会自动重建空库）。"
        extra_actions.append("改名前先把整个数据目录备份一份")
        chosen = checks.get("database_corrupt")
    elif status("port_conflict") == "detected":
        conflicts = [item for item in (ports.get("port_conflicts") or []) if item.get("owner")]
        first = conflicts[0] if conflicts else {}
        owner = first.get("owner") or {}
        port = first.get("port") or startup_check.get("port")
        conclusion = (
            f"网关没有启动：端口 {port} 被 {owner.get('process') or '未知进程'}"
            f"（PID {owner.get('pid')}）占用"
        )
        action = "在任务管理器结束占用该端口的进程（进程名和 PID 见 ports.json）。"
        extra_actions.append("或在设置里换一个端口启动")
        chosen = checks.get("port_conflict")
    elif status("model_key_invalid") == "detected":
        check = checks.get("model_key_invalid") or {}
        conclusion = str(check.get("conclusion") or "模型 Key 无效")
        quota = "quota_exhausted" in conclusion or "402" in conclusion or any(
            "quota_exhausted" in _summary_value(item.get("value"))
            for item in (check.get("evidence") or [])
            if isinstance(item, dict) and "value" in item
        )
        if quota:
            action = "给这把 Key 充值，或换成一把有额度的 Key 后重启鎏灏。"
            extra_actions.append("在「系统设置 → 模型管理」重新填 Key 并点「测试连接」")
        else:
            action = "打开「系统设置 → 模型管理」，重新填写 Key 并点「测试连接」。"
            extra_actions.append("额度耗尽（402 / quota_exhausted）则充值或换 Key")
        chosen = check
    elif status("model_network_unreachable") == "detected":
        conclusion = "模型服务连不上：端点 DNS/TCP 探测失败，或日志记录了不可达/超时"
        action = "在浏览器打开该模型的 base_url，确认这台机器能访问它。"
        extra_actions.append(
            "检查代理与防火墙；10061=拒绝连接，10060=超时，11001=DNS 解析失败"
        )
        chosen = checks.get("model_network_unreachable")
    elif status("service_not_running") == "detected":
        port = startup_check.get("port") or "候选端口"
        conclusion = f"网关没有启动：端口 {port} 上没有 /v1/health 响应，也没有发现网关进程"
        action = "打开 logs/liuhao.log 与 logs/launcher.log 的末尾 50 行，找最后一个 evt= 事件。"
        extra_actions.extend(
            [
                "点托盘菜单的「重新启动服务」试一次",
                "仍失败就生成诊断包发给技术支持（附上出错时间点）",
            ]
        )
        chosen = checks.get("service_not_running")
    else:
        inconclusive = [
            item for item in checks.values() if item.get("status") == "inconclusive"
        ]
        if inconclusive:
            titles = "、".join(str(item.get("title") or item.get("id")) for item in inconclusive[:3])
            conclusion = (
                f"无法确定：未发现确定故障，但有 {len(inconclusive)} 项无法判定（{titles}）；"
                "需要人工看 logs/liuhao.log 与 logs/error.log 最近的 evt= 事件"
            )
            action = "按 diagnosis.json 里对应 check 的 next_step 补做一次验证。"
        else:
            conclusion = (
                "本地检查一切正常（端口、目录、数据库、静态资源、模型配置都通过），"
                "但无法确定具体原因：需要人工看 logs/liuhao.log 最近的 evt=llm.call.failed 与 logs/error.log"
            )
            action = "复现一次问题后立刻重新生成诊断包（日志里会出现失败事件）。"

    first_evidence = (
        "startup_check.json · "
        f"data_writable={startup_check.get('data_writable')}, "
        f"log_writable={startup_check.get('log_writable')}, "
        f"port={startup_check.get('port')}, "
        f"port_bindable={startup_check.get('port_bindable')}, "
        f"port_state={startup_check.get('port_state')}, "
        f"db_open={startup_check.get('db_open')}, "
        f"console_index={startup_check.get('console_index')}"
        + (f", data_free_bytes={free}" if isinstance(free, int) else "")
    )
    evidence: list[str] = [first_evidence, *extra_evidence]
    if chosen:
        evidence.extend(_summary_evidence(chosen, limit=2))
    evidence.append(f"diagnosis.json · headline = {diagnosis.get('headline') or '-'}")
    detected = sum(1 for item in checks.values() if item.get("status") == "detected")
    inconclusive = sum(1 for item in checks.values() if item.get("status") == "inconclusive")
    evidence.append(
        f"diagnosis.json · 共 {len(checks)} 项检查：检出 {detected} 项、"
        f"无法判定 {inconclusive} 项、其余正常"
    )
    # 引导日志跨多次运行共享，里面可能躺着与本次无关的旧事件（例如上一轮演练）。
    # 只有"目录不可写"这个结论本来就要靠它佐证时才引用，避免拿旧证据误导。
    bootstrap_lines = [
        line
        for line in bootstrap_text.splitlines()
        if re.match(r"^\d{4}-\d{2}-\d{2} .* evt=", line.strip())
    ]
    want_bootstrap = status("directory_not_writable") == "detected"
    if want_bootstrap and bootstrap_lines:
        evidence.insert(1, f"bootstrap.log.txt · 最后一条 = {_summary_value(bootstrap_lines[-1])}")
    elif event_text and not event_text.startswith("没有发现"):
        tail = event_text.splitlines()[-1]
        evidence.append(f"recent_events.txt · 最近一条 = {_summary_value(tail)}")

    bullets = evidence[:4]
    if extra_actions:
        # 用户一次只做一个动作：唯一动作写在"下一步"，其余只作为依据出现。
        bullets.append("其余动作（按需再做）：" + "；".join(extra_actions))
    bullets = bullets[:5]
    source = str(chosen.get("id") or "none") if chosen else "none"
    # 证据可能来自 bootstrap.log / 日志尾部，里面仍有本机绝对路径：SUMMARY 是
    # 给人看的一段话，统一折成 `<本地路径>/<文件名>`（verify_bundle 会断言它干净）。
    conclusion = _mask_drive_paths(conclusion)
    action = _mask_drive_paths(action)
    bullets = [_mask_drive_paths(line) for line in bullets]
    return "\n".join(
        [
            conclusion,
            "",
            "依据：",
            *[f"- {line}" for line in bullets],
            "",
            f"下一步：{action}",
            "",
            # 机器可读的结论来源：verify_bundle 用它和 diagnosis.json 的首个
            # detected 检查比对，防止两份文件各说各话。
            f"# 结论来源：{source}",
            "",
        ]
    )


_README = BRAND_LOCKUP + """ · 一键诊断包
===========================

**先看 SUMMARY.txt。** 第一行是一句人话结论，后面是 3–5 条可核对的依据；
结论判不出来时会明确写"无法确定，需要人工看 logs/ 里的哪个文件"。

`<数据目录>` / `<日志目录>` 就是你机器上正在使用的两个目录：默认
`%LOCALAPPDATA%\\LiuHao\\data`（日志在其下的 `logs` 子目录），安装时改过目录的
以「关于」页显示的数据目录为准。例如 `<日志目录>/liuhao.log` 就是该目录下真实
存在的 `liuhao.log`，可以直接打开。其它路径写成 `<本地路径>/<最后两段>`，
按最后两段在你自己的磁盘上查找即可；相对写法（如 `logs/error.log`）同理。

这是由 `python -m src.desktop diagnose` 生成的排查材料，**服务未启动时同样可用**，仅包含：

  SUMMARY.txt      第一行结论 + 3–5 条依据 + 唯一一个「下一步」动作（先看这个）；
                   末行 `# 结论来源：<check_id>` 是机器可读标记，与 diagnosis.json 交叉校验
  manifest.json    每个成员的文件名、大小、sha256 与脱敏版本号
  about.json       版本 / 构建时间 / 数据目录 / 提交号
  environment.json 操作系统与 Python 版本 / 允许范围内的环境摘要
  installation.json 安装根目录 / 运行期 Python / 必需文件清单 / 数据与日志目录真实写入探测
  data_inventory.json 数据目录顶层清单与每个目录的占用大小（不读取文件内容）
  ports.json       常见端口占用情况、占用进程，以及"被别的进程占用"的冲突清单
  processes.json   与本产品相关的进程（PID / 名称 / 启动时间 / 脱敏后命令行）；gateway 字段只列真正的网关子进程，
                   gateway_for_port 只统计带 --port <本服务端口> 的网关（桌面壳生成时提供）
  ready_probe.json 对候选端口 /v1/ready 与 /v1/health 的逐项探测结果（含失败原因与耗时）
  providers.json   已配置模型的名称、provider 类型、模型名、地址、是否填了 Key（不含密钥）
  model_health.json 日志里的模型失败机器信号 + base_url 的 DNS/TCP 直连探测
  static_assets.json 驾驶舱 index.html 及其引用资源是否存在 / 服务可达时的 HEAD 状态
  db_check.json    数据目录下 SQLite 数据库的只读 quick_check 结论（不含任何行内容）
  startup_check.json 离线启动自检：数据/日志目录可写、端口可绑、各库打开状态、
                  驾驶舱 index.html 是否存在、模型与 Key 指纹，以及 actions（下一步做什么）
  bootstrap.log.txt 数据目录与日志目录都不可写时的独立引导日志（最近 200 行）
  recent_events.txt 最近 20 条 evt= 结构化事件（哪一步、为什么、下一步做什么）
  diagnosis.json   8 类常见故障的判定结论、证据定位（文件 + 字段）与下一步动作
  service.json     /v1/health 与 /v1/ready 的原始快照（不可达时如实记录）
  dependencies.json 已安装依赖的名称与版本
  logs/*.txt       最近日志片段的**尾部**（已脱敏，含轮转文件）
  """ + LOG_MEMBER_ALIAS_NOTE + """
  logs/index.json  每份日志的原始大小 / 保留大小 / 是否截断
  scan_rule_hit.json 未包含的文件上命中了哪条规则、几次（只在剔除时出现，不含内容）
  firewall.json    防火墙 / 局域网可达性只读检测结果（如当时采集）

本次是否完整
------------
__COMPLETENESS__
N 是包内文件数，M 是被剔除的数量；N + M 就是本次计划收集的文件总数。

日志上限：最多 8 份、每份只保留尾部 200 行且不超过 200KB，轮转文件
（如 liuhao.log.1）一并采集；超出部分不会进入诊断包，完整原文仍留在本机。

明确**不包含**（这是设计而非承诺）：
  * 任何数据库文件（.db / .sqlite / .sqlite3）及其 WAL/SHM
    —— 只以只读方式执行 quick_check，只记录"是否完好 + 错误摘要"，不复制任何内容
  * 任何密钥、token、完整性键、签名私钥、加密密钥文件
  * 人类身份登记表 / 凭据文件 / .env 原文
  * 完整环境变量转储

所有文本在写入前经过脱敏；写完后还会重新打开 zip 逐成员自检。
仍含明文凭据的文件不会进包，而是列入 REDACTED-MEMBERS.txt，其余成员照常打包。
"""


def _strip_plaintext_members(texts: dict[str, str]) -> list[dict[str, Any]]:
    """写 zip 前的最后一道闸：命中的成员从包里剔除，其余照常导出。

    用户最需要求助时不能无路可走，所以这里不做"命中即整体失败"：把仍含明文
    凭据的成员摘掉，返回 ``[{"name", "hits", "total"}]`` 供调用方写说明。
    规则表与 gateway 导出闸 / 验收脚本 / 发布闸同源（``src/common/leak_scan.py``），
    日志里只写成员名与条数，**不写**任何命中内容。
    """
    omitted: list[dict[str, Any]] = []
    for name in list(texts):
        hits = scan_text(texts[name])
        if not hits:
            continue
        total = sum(hits.values())
        omitted.append({"name": name, "hits": dict(hits), "total": total})
        logger.warning(
            "diagnostics.bundle.omitted member=%s hits=%d detail=%s",
            name,
            total,
            format_hits(hits),
        )
        del texts[name]
    return omitted


def _readme_text(omitted, *, included: int) -> str:
    """README 首页：剔除说明（有的话）+ 完整性自证那一行。

    ``included`` 是最终包内成员数（含 manifest.json 自身）；``included + len(omitted)``
    就是本次计划收集的文件总数，用户数一遍文件即可核对。
    """
    items = list(omitted)
    base = (
        omitted_readme(_README, items, marker="**先看 SUMMARY.txt。**")
        if items
        else _README
    )
    return base.replace("__COMPLETENESS__", completeness_line(included, len(items)), 1)


def create_bundle(
    out_path: str | Path | None = None,
    *,
    base_url: str | None = None,
    root: Path | None = None,
    log_dir: Path | None = None,
    ports: list[int] | None = None,
    probe_network: bool = True,
    omitted_out: list[dict[str, Any]] | None = None,
) -> Path:
    """生成诊断 zip，返回其绝对路径。生成后自动自检。

    ``probe_network=False`` 只跳过 base_url 的 DNS/TCP 直连探测，其余采集照旧；
    测试与离线场景可用，不会因为外网不可达而拖慢诊断。

    ``omitted_out`` 是可选出参：传入列表时，被剔除的成员（命中明文凭据）会以
    ``[{"name", "hits", "total"}]`` 追加进去，供界面提示"有 N 个文件未包含"。
    """
    base = Path(root or REPO_ROOT)
    runtime_data_dir = resolve_data_dir()
    # 日志目录与运行期日志落点保持同一契约：显式 ``LIUHAO_LOG_DIR`` 优先，
    # 否则 ``<data>/logs``。以前写死 ``<data>/logs``，隔离实例里诊断包会去探测
    # 一个根本没在用的目录，给出错误的"日志目录可写"结论。
    logs = Path(log_dir) if log_dir is not None else resolve_log_file("logs/liuhao.log").parent
    checked_ports = list(ports or DEFAULT_PORTS)
    service = _collect_service_snapshot(base_url)
    expected_port: int | None = None
    if service.get("base_url"):
        try:
            port = int(str(service["base_url"]).rstrip("/").rsplit(":", 1)[-1])
            if port not in checked_ports:
                checked_ports.append(port)
        except (TypeError, ValueError):
            pass
    if base_url:
        # 桌面壳明确告知"要诊断的是这个端口"：结论只对这个端口负责，
        # 不被同一台机器上其它网关的响应带偏。
        try:
            expected_port = int(str(base_url).rstrip("/").rsplit(":", 1)[-1])
            service["expected_port"] = expected_port
            # 排到最前：``_collect_ready_probe`` 只探测前 8 个候选端口。
            checked_ports = [expected_port] + [p for p in checked_ports if p != expected_port]
        except (TypeError, ValueError):
            expected_port = None

    environment = _collect_environment()
    # about.json 走白名单：``collect_about`` 里的 python_executable / repo_root /
    # diagnostics_dir 都是安装机绝对路径（含用户名），不进诊断包。
    about = collect_about(root)
    about_payload = {
        key: about.get(key)
        for key in ("app", "version", "commit", "build_time", "build_time_source", "python_version")
    }
    about_payload["data_dir"] = environment["data_dir"]
    about_payload["log_dir"] = environment["log_dir"]

    log_texts = _collect_logs(logs)
    log_retention = _collect_log_retention(logs)
    providers = _read_providers()
    installation = _collect_installation(
        base, runtime_data_dir, logs, about_payload.get("version")
    )
    ports_payload = _collect_ports(checked_ports, _listening_owners())
    static_assets = _collect_static_assets(base, service)
    model_health = _collect_model_health(providers, log_texts, probe_network)
    db_check = _collect_db_check(runtime_data_dir)
    startup_check = _collect_startup_check(
        runtime_data_dir,
        logs,
        base,
        expected_port or _configured_api_port(),
    )
    ready_probe = _collect_ready_probe(checked_ports)
    processes_payload = _collect_processes(expected_port)
    diagnosis = _build_diagnosis(
        installation,
        ports_payload,
        static_assets,
        model_health,
        db_check,
        ready_probe,
        processes_payload,
        service,
        startup_check,
    )
    payloads: dict[str, Any] = {
        "about.json": about_payload,
        "environment.json": environment,
        "installation.json": installation,
        "data_inventory.json": _collect_data_inventory(runtime_data_dir),
        "log_retention.json": log_retention,
        "ports.json": ports_payload,
        "processes.json": processes_payload,
        "ready_probe.json": ready_probe,
        "providers.json": {
            "registry": providers,
            "env_provider_hint": {
                key: value
                for key, value in environment["app_env"].items()
                if key in {"提供方类型", "提供方模型"}
            },
        },
        "model_health.json": model_health,
        "static_assets.json": static_assets,
        "db_check.json": db_check,
        "startup_check.json": startup_check,
        "diagnosis.json": diagnosis,
        "service.json": service,
        "dependencies.json": _collect_dependencies(),
    }
    if service.get("base_url"):
        try:
            from .firewall import inspect_firewall

            payloads["firewall.json"] = inspect_firewall(
                int(str(service["base_url"]).rstrip("/").rsplit(":", 1)[-1])
            )
        except (ImportError, OSError, ValueError) as exc:
            payloads["firewall.json"] = {"error": f"{type(exc).__name__}: {exc}"}

    texts: dict[str, str] = {}
    for name, payload in payloads.items():
        texts[name] = redact_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    texts.update(log_texts)
    bootstrap_text = redact_text(_collect_bootstrap_log())
    event_text = redact_text(_collect_recent_events(log_texts))
    texts["bootstrap.log.txt"] = bootstrap_text
    texts["recent_events.txt"] = event_text
    # SUMMARY.txt 放在最前，并在 README 里点名：用户拿到包只需要读这两份。
    texts["SUMMARY.txt"] = redact_text(
        _build_summary(
            diagnosis,
            startup_check,
            installation,
            ports_payload,
            static_assets,
            db_check,
            bootstrap_text,
            event_text,
        )
    )
    texts["README.txt"] = _README

    omitted = _strip_plaintext_members(texts)
    # README 只是说明，不构成"可用的诊断包"：只剩它（或干脆空了）就整体拒绝。
    # 判定条件与网关一致——真实诊断成员一个都留不下才拒绝。
    diagnostic_members = [name for name in texts if name != "README.txt"]
    if not diagnostic_members:
        names = "、".join(str(item["name"]) for item in omitted) or "全部成员"
        # 内部审计事件：只记成员名、规则名与条数，不记任何命中内容。
        log_bundle_blocked("desktop", omitted)
        raise RuntimeError(
            f"诊断包内所有文件都未通过密钥自检（{names}），没有可安全转发的文件。"
            "这是罕见兜底（通常是扫描规则误判或日志残留无法自动脱敏的片段），不是常见故障。"
            "下一步：先退出鎏灏，把数据目录 logs 下的 liuhao.log、error.log、"
            "desktop-gateway.log（以及 .log.1 轮转文件）改名备份——把 .log 改成 "
            ".log.bak-日期，不要直接删除，它们是排查证据；再重新打开并导出。"
            "如果仍被拦，请把这条提示截图发给技术支持。"
        )
    if omitted:
        texts[OMITTED_MEMBERS_NAME] = omitted_members_text(omitted)
        texts[SCAN_RULE_HIT_NAME] = json.dumps(
            scan_rule_hit_report(omitted), ensure_ascii=False, indent=2, sort_keys=True
        )
        # 说明文件是现生成的，仍然过一遍扫描，避免"介绍剔除的文件反而泄密"。
        for generated in (OMITTED_MEMBERS_NAME, SCAN_RULE_HIT_NAME):
            generated_hits = scan_text(texts[generated])
            if generated_hits:
                log_bundle_blocked("desktop", omitted, hits=generated_hits)
                raise RuntimeError(
                    f"说明文件 {generated} 仍含明文凭据（{format_hits(generated_hits)}），"
                    "已停止导出。"
                )
    # 完整性自证：N 是最终成员数（含下面要写的 manifest.json），
    # N + len(omitted) 就是计划收集的总数；README 最后生成，避免数字对不上。
    texts["README.txt"] = _readme_text(omitted, included=len(texts) + 1)
    readme_hits = scan_text(texts["README.txt"])
    if readme_hits:
        log_bundle_blocked(
            "desktop",
            [{"name": "README.txt", "hits": readme_hits, "total": sum(readme_hits.values())}],
        )
        raise RuntimeError(
            f"说明文件 README.txt 仍含明文凭据（{format_hits(readme_hits)}），已停止导出。"
        )
    if omitted_out is not None:
        omitted_out.extend(omitted)

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "redaction_version": REDACTION_VERSION,
        "files": {
            name: {"bytes": len(content.encode("utf-8")), "sha256": _sha256(content)}
            for name, content in sorted(texts.items())
        },
    }
    if omitted:
        manifest["omitted_members"] = [
            {"name": str(item["name"]), "total": int(item["total"])} for item in omitted
        ]
    texts["manifest.json"] = redact_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    target = _resolve_output(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(texts.items()):
                archive.writestr(name, content)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    violations = verify_bundle(target)
    if violations:
        target.unlink(missing_ok=True)
        raise RuntimeError("诊断包自检失败，已删除产物：" + "; ".join(violations))
    return target


def _resolve_output(out_path: str | Path | None) -> Path:
    if out_path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return diagnostics_dir() / f"liuhao-diagnostics-{stamp}.zip"
    candidate = Path(out_path).expanduser()
    if candidate.is_dir():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return candidate / f"liuhao-diagnostics-{stamp}.zip"
    return candidate


_SUMMARY_SOURCE_RE = re.compile(r"^#\s*结论来源：(\S+)\s*$", re.MULTILINE)

#: 本机绝对路径（``C:\...`` / ``D:/...``）。前面紧贴字母时不算（排除 ``http://``）。
_ABS_DRIVE_PATH_RE = re.compile(r"(?<![\w/])[A-Za-z]:[\\/][^\s\"'，,；;）)\]]*")


def _path_anchors() -> list[tuple[str, str]]:
    """当前实例正在使用的数据/日志目录，长的在前（先匹配更具体的）。

    这样 ``D:\\LiuHaoData\\data\\logs\\liuhao.log`` 会折成
    ``<日志目录>/liuhao.log`` 而不是笼统的 ``<本地路径>/logs/liuhao.log``：
    用户从「关于」页或默认位置就能直接打开，不需要再猜中间层级。
    """
    try:
        data = os.path.abspath(str(resolve_data_dir()))
        logs = os.path.abspath(str(resolve_log_file("logs/liuhao.log").parent))
    except Exception:  # noqa: BLE001 - 取不到锚点时退回"只保留末两段"
        return []
    items = [("<日志目录>", logs), ("<数据目录>", data)]
    return sorted(items, key=lambda item: len(item[1]), reverse=True)


def _drive_path_replacement(match: re.Match[str]) -> str:
    """折成本地占位路径，去掉盘符与用户目录，但保留用户能对上文件的线索。

    - 命中当前数据目录 / 日志目录：``<数据目录>/audit.db``、``<日志目录>/liuhao.log``；
    - 其它绝对路径：``<本地路径>/<最后两段>``（只留文件名会把 ``data/audit.db``
      与 ``logs/audit.db`` 折成同一个，反而让"打开哪个文件"失去意义）。
    """
    raw = match.group(0)
    target = os.path.abspath(raw)
    for token, base in _path_anchors():
        if os.path.normcase(target) == os.path.normcase(base):
            return token
        prefix = base.rstrip("\\/") + os.sep
        if os.path.normcase(target).startswith(os.path.normcase(prefix)):
            rest = target[len(prefix):].replace("\\", "/")
            return f"{token}/{rest}"
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    return f"<本地路径>/{'/'.join(parts[-2:])}" if parts else "<本地路径>"


def _mask_drive_paths(text: str) -> str:
    return _ABS_DRIVE_PATH_RE.sub(_drive_path_replacement, text)


def _verify_summary(summary: str, diagnosis: dict[str, Any] | None) -> list[str]:
    """``SUMMARY.txt`` 的完整性、泄漏与一致性断言。

    三件事：
      1. 第一行（结论句）必须存在且非空；
      2. 结论/依据里不得残留用户名、绝对路径、凭据；
      3. 结论来源必须与 ``diagnosis.json`` 的首个 detected 检查一致，
         否则 SUMMARY 的结论和 headline 的"建议先处理"互相矛盾。
    """
    violations: list[str] = []
    first = summary.splitlines()[0].strip() if summary.splitlines() else ""
    if not first:
        violations.append("SUMMARY.txt: 结论行缺失或为空（第一行必须是一句结论）")

    leaked = leak_violations(summary)
    if leaked or contains_secret(summary):
        labels = "、".join(leaked) or "疑似密钥"
        violations.append(f"SUMMARY.txt: 结论/依据仍含泄漏特征（{labels}）")
    if _ABS_DRIVE_PATH_RE.search(summary):
        violations.append(
            "SUMMARY.txt: 结论/依据含本机绝对路径"
            "（应写成「<本地路径>/文件名」或相对路径）"
        )
    residual = scan_text(summary)
    if residual:
        violations.append(f"SUMMARY.txt: 结论/依据仍含明文凭据（{format_hits(residual)}）")

    match = _SUMMARY_SOURCE_RE.search(summary)
    if not match:
        violations.append(
            "SUMMARY.txt: 缺少机器可读的结论来源行（应为 `# 结论来源：<check_id|none>`）"
        )
        return violations

    detected = [
        str(item.get("id"))
        for item in ((diagnosis or {}).get("checks") or [])
        if isinstance(item, dict) and item.get("status") == "detected"
    ]
    source = match.group(1)
    if source == "none":
        if detected:
            violations.append(
                "SUMMARY.txt: 结论说无法确定，但 diagnosis.json 检出 "
                f"{len(detected)} 项（{'、'.join(detected[:3])}）——与 headline 互相矛盾"
            )
        return violations
    if source not in detected:
        violations.append(
            f"SUMMARY.txt: 结论来源 {source} 在 diagnosis.json 里不是 detected 状态"
            "——与 headline 互相矛盾"
        )
        return violations
    if detected[0] != source:
        violations.append(
            f"SUMMARY.txt: 结论指向 {source}，但 diagnosis.json 的 headline 建议先处理 "
            f"{detected[0]}——两者互相矛盾"
        )
    return violations


def verify_bundle(path: str | Path) -> list[str]:
    """重新打开 zip 逐成员自检；返回问题列表（空表示通过）。"""
    violations: list[str] = []
    target = Path(path)
    summary_text: str | None = None
    diagnosis_payload: dict[str, Any] | None = None
    has_manifest = False
    try:
        with zipfile.ZipFile(target, "r") as archive:
            has_manifest = "manifest.json" in archive.namelist()
            for info in archive.infolist():
                name = info.filename
                if not name.endswith(_ALLOWED_SUFFIXES):
                    violations.append(f"{name}: 成员后缀不在白名单内")
                    continue
                lowered = name.lower()
                for token in _FORBIDDEN_NAME_TOKENS:
                    if token in lowered:
                        violations.append(f"{name}: 成员名含敏感标识 {token!r}")
                content = archive.read(name).decode("utf-8", "replace")
                if name == "SUMMARY.txt":
                    # SUMMARY 的两条完整性/泄漏断言由 _verify_summary 专门负责，
                    # 这里只记录内容，避免同一问题报两遍。
                    summary_text = content
                else:
                    leaked = leak_violations(content)
                    if leaked or contains_secret(content):
                        labels = "、".join(leaked) or "疑似密钥"
                        violations.append(f"{name}: 内容仍含泄漏特征（{labels}）")
                    residual = scan_text(content)
                    if residual:
                        violations.append(
                            f"{name}: 内容仍含明文凭据（{format_hits(residual)}）"
                        )
                if name == "diagnosis.json":
                    with contextlib.suppress(ValueError, TypeError):
                        parsed = json.loads(content)
                        if isinstance(parsed, dict):
                            diagnosis_payload = parsed
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"无法读取诊断包：{type(exc).__name__}: {exc}"]

    if summary_text is not None:
        violations.extend(_verify_summary(summary_text, diagnosis_payload))
    elif has_manifest:
        # 有 manifest 说明是本产品生成的包：没带 SUMMARY 就等于没带结论。
        violations.append("SUMMARY.txt: 缺少 SUMMARY.txt（诊断包必须带一句结论）")
    return violations


__all__ = [
    "REDACTION_VERSION",
    "contains_secret",
    "create_bundle",
    "leak_violations",
    "mask_user_path",
    "redact_text",
    "sanitize_text",
    "verify_bundle",
]
