"""一键诊断包：白名单收集 + 强制脱敏 + 生成后自检。

设计要点（全部是硬约束）：
  * **白名单生成**：zip 里的每个成员都由本模块在内存中构造
    （``manifest.json`` / ``README.txt`` / ``*.json`` / ``logs/*.txt``），
    从不遍历目录、从不打包数据库文件。因此密钥、token、完整性键、
    身份表、数据库原文都不可能被"顺手"带进去。
  * **写入前脱敏**：所有文本先过 :func:`redact_text`，再进 zip。
  * **写入后自检**：:func:`verify_bundle` 重新打开 zip 逐成员扫描，
    一旦发现疑似密钥就删除产物并抛错（fail-closed），不留"看起来正常"的包。
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from ..common.redaction import (
    REDACTION_VERSION,
    contains_secret,
    leak_violations,
    mask_user_path,
    redact_text,
    sanitize_text,
)
from ..logging_config import resolve_data_dir
from . import REPO_ROOT, diagnostics_dir
from .about import collect_about
from .probe import DEFAULT_PORTS, discover_service, http_json, port_is_free

_MAX_LOG_FILES = 5
_MAX_LOG_LINES = 200
_MAX_LOG_BYTES = 200_000
_MAX_DEPENDENCIES = 500

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


def _collect_logs(log_dir: Path) -> dict[str, str]:
    if not log_dir.is_dir():
        return {}
    logs: dict[str, str] = {}
    try:
        files = sorted((p for p in log_dir.glob("*.log") if p.is_file()), key=lambda p: p.stat().st_mtime,
                       reverse=True)
    except OSError:
        return logs
    for path in files[:_MAX_LOG_FILES]:
        logs[f"logs/{path.name}.txt"] = _tail_log(path)
    return logs


def _collect_ports(ports: list[int]) -> dict[str, Any]:
    return {
        "checked": ports,
        "entries": [{"port": port, "free": port_is_free(port)} for port in ports],
    }


def _read_providers() -> dict[str, Any]:
    """只读打开模型配置库，**只取名称/类型/模型/地址**，从不解密密钥。"""
    db_path = Path(
        os.environ.get("LIUHAO_MODEL_CONFIG_DB")
        or resolve_data_dir() / "model_registry.sqlite3"
    )
    if not db_path.is_file():
        return {"source": db_path.name, "available": False, "models": []}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT name, provider, model, base_url, enabled, is_default FROM ai_model_configs"
            ).fetchall()
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


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_README = """鎏灏 LIUHAO X · 一键诊断包
===========================

这是由 `python -m src.desktop diagnose` 生成的排查材料，仅包含：

  manifest.json    每个成员的文件名、大小、sha256 与脱敏版本号
  about.json       版本 / 构建时间 / 数据目录 / 提交号
  environment.json 操作系统与 Python 版本 / 允许范围内的环境摘要
  ports.json       常见端口（8080 / 5173 / 8443）占用情况
  providers.json   已配置模型的名称、provider 类型、模型名、地址（不含密钥）
  service.json     /v1/health 与 /v1/ready 的原始快照（不可达时如实记录）
  dependencies.json 已安装依赖的名称与版本
  logs/*.txt       最近日志片段的**尾部**（已脱敏）
  firewall.json    防火墙 / 局域网可达性只读检测结果（如当时采集）

明确**不包含**（这是设计而非承诺）：
  * 任何数据库文件（.db / .sqlite / .sqlite3）及其 WAL/SHM
  * 任何密钥、token、完整性键、签名私钥、加密密钥文件
  * 人类身份登记表 / 凭据文件 / .env 原文
  * 完整环境变量转储

所有文本在写入前经过脱敏；写完后还会重新打开 zip 逐成员自检，
发现疑似密钥就删除产物并报错（fail-closed）。
"""


def create_bundle(
    out_path: str | Path | None = None,
    *,
    base_url: str | None = None,
    root: Path | None = None,
    log_dir: Path | None = None,
    ports: list[int] | None = None,
) -> Path:
    """生成诊断 zip，返回其绝对路径。生成后自动自检。"""
    base = Path(root or REPO_ROOT)
    runtime_data_dir = resolve_data_dir()
    logs = Path(log_dir) if log_dir is not None else runtime_data_dir / "logs"
    checked_ports = list(ports or DEFAULT_PORTS)
    service = _collect_service_snapshot(base_url)
    if service.get("base_url"):
        try:
            port = int(str(service["base_url"]).rstrip("/").rsplit(":", 1)[-1])
            if port not in checked_ports:
                checked_ports.append(port)
        except (TypeError, ValueError):
            pass

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

    payloads: dict[str, Any] = {
        "about.json": about_payload,
        "environment.json": environment,
        "ports.json": _collect_ports(checked_ports),
        "providers.json": {
            "registry": _read_providers(),
            "env_provider_hint": {
                key: value
                for key, value in environment["app_env"].items()
                if key in {"提供方类型", "提供方模型"}
            },
        },
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

    texts: dict[str, str] = {"README.txt": _README}
    for name, payload in payloads.items():
        texts[name] = redact_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    for name, content in _collect_logs(logs).items():
        texts[name] = content

    manifest = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "redaction_version": REDACTION_VERSION,
        "files": {
            name: {"bytes": len(content.encode("utf-8")), "sha256": _sha256(content)}
            for name, content in sorted(texts.items())
        },
    }
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


def verify_bundle(path: str | Path) -> list[str]:
    """重新打开 zip 逐成员自检；返回问题列表（空表示通过）。"""
    violations: list[str] = []
    target = Path(path)
    try:
        with zipfile.ZipFile(target, "r") as archive:
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
                leaked = leak_violations(content)
                if leaked or contains_secret(content):
                    labels = "、".join(leaked) or "疑似密钥"
                    violations.append(f"{name}: 内容仍含泄漏特征（{labels}）")
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"无法读取诊断包：{type(exc).__name__}: {exc}"]
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
