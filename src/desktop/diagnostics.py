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
import re
import sqlite3
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from . import LOG_DIR, REPO_ROOT, diagnostics_dir
from .about import collect_about
from .probe import DEFAULT_PORTS, discover_service, http_json, port_is_free

REDACTION_VERSION = "1"
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

#: 脱敏规则。顺序即优先级；`sk-` / Bearer / JWT / PEM / 键值对依次处理。
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
     "***PRIVATE-KEY-REDACTED***"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,})"), "sk-***REDACTED***"),
    (re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1***REDACTED***"),
    (re.compile(r"(?i)\b(eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,})"),
     "***JWT-REDACTED***"),
    (
        re.compile(
            r"(?i)((?:api[_-]?key|access[_-]?key|secret[_-]?key|integrity[_-]?key|"
            r"signing[_-]?key|private[_-]?key|token|password|passwd)\s*[\"']?\s*[=:]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;\"'）)]+)"
        ),
        r"\1***REDACTED***",
    ),
    (re.compile(r"(?i)\b(AI_PROVIDER_KEY\s*=\s*)([^\s\"']+)"), r"\1***REDACTED***"),
)


def redact_text(text: str) -> str:
    """把常见密钥形态替换为固定占位串（幂等）。"""
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def contains_secret(text: str) -> bool:
    """写入后自检用：文本里是否仍有疑似密钥形态（已脱敏占位符不算）。"""
    for pattern, _ in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            if "REDACTED" in match.group(0):
                continue
            return True
    return False


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


def _read_providers(root: Path) -> dict[str, Any]:
    """只读打开模型配置库，**只取名称/类型/模型/地址**，从不解密密钥。"""
    db_path = Path(os.environ.get("LIUHAO_MODEL_CONFIG_DB") or root / "data" / "model_registry.sqlite3")
    if not db_path.is_file():
        return {"source": str(db_path), "available": False, "models": []}
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
        try:
            rows = conn.execute(
                "SELECT name, provider, model, base_url, enabled, is_default FROM ai_model_configs"
            ).fetchall()
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        return {
            "source": str(db_path),
            "available": False,
            "models": [],
            "error": f"{type(exc).__name__}: {exc}",
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
    return {"source": str(db_path), "available": True, "models": models}


def _collect_environment(root: Path) -> dict[str, Any]:
    allowed_env = {
        key: os.environ.get(key)
        for key in ("APP_ENV", "AI_PROVIDER_TYPE", "AI_PROVIDER_MODEL", "OLLAMA_BASE_URL")
        if os.environ.get(key)
    }
    if allowed_env.get("OLLAMA_BASE_URL"):
        allowed_env["OLLAMA_BASE_URL"] = _sanitize_url(allowed_env["OLLAMA_BASE_URL"])
    return {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "repo_root": str(root),
        "data_dir": str(root / "data"),
        "log_dir": str(root / "logs"),
        "app_env": allowed_env,
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
    logs = Path(log_dir) if log_dir is not None else (LOG_DIR if root is None else base / "logs")
    checked_ports = list(ports or DEFAULT_PORTS)
    service = _collect_service_snapshot(base_url)
    if service.get("base_url"):
        try:
            port = int(str(service["base_url"]).rstrip("/").rsplit(":", 1)[-1])
            if port not in checked_ports:
                checked_ports.append(port)
        except (TypeError, ValueError):
            pass

    payloads: dict[str, Any] = {
        "about.json": collect_about(root),
        "environment.json": _collect_environment(base),
        "ports.json": _collect_ports(checked_ports),
        "providers.json": {
            "registry": _read_providers(base),
            "env_provider_hint": {
                key: value
                for key, value in _collect_environment(base)["app_env"].items()
                if key in {"AI_PROVIDER_TYPE", "AI_PROVIDER_MODEL"}
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
                if contains_secret(content):
                    violations.append(f"{name}: 内容仍匹配疑似密钥规则")
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"无法读取诊断包：{type(exc).__name__}: {exc}"]
    return violations


__all__ = ["REDACTION_VERSION", "contains_secret", "create_bundle", "redact_text", "verify_bundle"]
