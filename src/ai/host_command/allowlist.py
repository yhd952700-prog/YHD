"""防诱导 §1.3 — 主机命令白名单落盘（Owner 专属）。

文件位置：数据目录下的 ``host_command_allowlist.json``（与
``authority_mode.json``、``host_command_confirm.json`` 同一目录、同一种
落盘方式：``resolve_store_path`` + 原子写 + 文件锁）。**不进代码仓库、
不进前端、不随安装包分发默认值之外的条目；出厂内容是空的**——A/B 档
的种子清单只在文档里列，不预置进文件；Joseph 在设置页逐项开，
开一项、审计记一项。

条目格式（与 :class:`HostCommandCapability` 的结构化字段一一对应，
没有整串 ``command_glob``——注册时带它直接报错）：

    {
      "capability_id": "git-status",
      "description": "查看 git 状态",
      "program": "git",
      "subcommands": ["status"],
      "arg_patterns": [],
      "forbidden_args": ["-f", "--force"],
      "allow_shell": false,
      "allowed_cwds": ["C:\\Jarvis\\workspace"],
      "max_seconds": 30,
      "adapter": null,
      "require_shell": null,
      "requires_approval": false
    }

规则：

* 白名单只管“能跑什么”，不管“要不要问”——进了白名单还不等于能直接
  跑，四件大事永远走审批（§3.1），白名单也救不了。
* 改动下一次命令才生效，不追溯已发出、还没执行的命令；已批过的批准
  不受白名单变更影响（批准是一次性的、指纹全量的）。
* 版本与回滚：每次保存先把旧文件留一份带时间戳的副本在数据目录
  （只留最近 5 份）；改错了 Owner 能一键回到上一份。
* 文件损坏 / 条目不合法时按空名单处理（fail-closed：默认 DENY），
  并记一条 warning——绝不因为读不懂文件就放行。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...common.atomic_write import atomic_write_text, exclusive_file_lock
from .capability import HostCommandCapability

logger = logging.getLogger(__name__)

ALLOWLIST_FILENAME = "host_command_allowlist.json"
ALLOWLIST_FILE_ENV = "LIUHAO_HOST_COMMAND_ALLOWLIST_FILE"
ALLOWLIST_MAX_ENTRIES = 50
ALLOWLIST_MAX_VERSIONS = 5
ALLOWLIST_VERSIONS_DIRNAME = "host_command_allowlist.versions"

#: 万能壳：默认不在白名单里；真要用，必须单独列条目并锁死后面的
#: 参数模板（subcommands 与 arg_patterns 都必须非空）。
_SHELL_HOSTS = frozenset({"powershell", "pwsh", "cmd", "cmd.exe"})

#: 条目允许的字段（未知字段直接报错——拼错字段名时静默接受是最危险的）。
#: ``command_glob`` 是已退役字段：出现（非空）由上面的显式检查报错，
#: 这里把它列为已知字段，避免它被“未知字段”先拦掉、报错信息走偏。
_ENTRY_FIELDS = frozenset({
    "capability_id", "description", "program", "subcommands", "arg_patterns",
    "forbidden_args", "allow_shell", "allowed_cwds", "max_seconds",
    "adapter", "require_shell", "requires_approval", "command_glob",
})

#: 程序名里不许出现的字符（单 token，不含 shell 元字符与空白）。
_PROGRAM_BAD = re.compile(r"[\s;|&`$<>(){}!\"']")

_lock = threading.Lock()


def allowlist_path() -> str:
    """白名单文件路径（数据目录；环境变量只覆盖路径）。"""
    from ...common.data_paths import repo_root, resolve_store_path

    return resolve_store_path(
        ALLOWLIST_FILENAME,
        repo_root() / "data" / ALLOWLIST_FILENAME,
        env_var=ALLOWLIST_FILE_ENV,
    )


def _versions_dir() -> Path:
    return Path(allowlist_path()).parent / ALLOWLIST_VERSIONS_DIRNAME


def validate_allowlist_entry(
    entry: object,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """校验一条白名单条目；合法返回 ``(规范化条目, None)``。

    规范化后的条目只含白名单字段；``command_glob`` 出现即报错（整串
    fnmatch 已退役，见 capability.py）。
    """
    if not isinstance(entry, dict):
        return None, "条目必须是对象"
    if entry.get("command_glob") is not None:
        return None, "command_glob 已退役：请改写成 program + subcommands + arg_patterns"
    unknown = sorted(set(entry) - _ENTRY_FIELDS)
    if unknown:
        return None, f"未知字段：{', '.join(unknown)}"

    capability_id = entry.get("capability_id")
    if not isinstance(capability_id, str) or not capability_id.strip():
        return None, "capability_id 必填（非空字符串）"
    program = entry.get("program")
    if not isinstance(program, str) or not program.strip():
        return None, "program 必填（程序名，如 git / notepad）"
    program = program.strip()
    if _PROGRAM_BAD.search(program):
        return None, f"program 不能含空白或 shell 元字符：{program!r}"

    subcommands = entry.get("subcommands")
    if subcommands is not None:
        if not isinstance(subcommands, list) or not all(
            isinstance(s, str) and s.strip() for s in subcommands
        ):
            return None, "subcommands 须是字符串列表"
        subcommands = [s.strip() for s in subcommands]
    arg_patterns = entry.get("arg_patterns")
    if arg_patterns is not None:
        if not isinstance(arg_patterns, list) or not all(
            isinstance(p, str) and p for p in arg_patterns
        ):
            return None, "arg_patterns 须是字符串列表"
        for pattern in arg_patterns:
            if "**" in pattern:
                return None, "arg_patterns 里不许用 **（它会匹配多个 token）"
    forbidden_args = entry.get("forbidden_args")
    if forbidden_args is not None:
        if not isinstance(forbidden_args, list) or not all(
            isinstance(a, str) and a for a in forbidden_args
        ):
            return None, "forbidden_args 须是字符串列表"
    allowed_cwds = entry.get("allowed_cwds")
    if allowed_cwds is not None:
        if not isinstance(allowed_cwds, list) or not all(
            isinstance(c, str) and c.strip() for c in allowed_cwds
        ):
            return None, "allowed_cwds 须是字符串列表"
        allowed_cwds = [c.strip() for c in allowed_cwds]
    max_seconds = entry.get("max_seconds")
    if max_seconds is not None:
        if not isinstance(max_seconds, (int, float)) or max_seconds <= 0:
            return None, "max_seconds 须是正数"
    adapter = entry.get("adapter")
    if adapter is not None and (not isinstance(adapter, str) or not adapter.strip()):
        return None, "adapter 须是非空字符串"
    require_shell = entry.get("require_shell")
    if require_shell is not None and not isinstance(require_shell, bool):
        return None, "require_shell 须是 true/false"
    allow_shell = entry.get("allow_shell", False)
    if not isinstance(allow_shell, bool):
        return None, "allow_shell 须是 true/false"
    requires_approval = entry.get("requires_approval", False)
    if not isinstance(requires_approval, bool):
        return None, "requires_approval 须是 true/false"
    description = entry.get("description", "")
    if not isinstance(description, str):
        return None, "description 须是字符串"

    # 万能壳必须锁死参数模板（§2.2 第 3 条）。
    if program.lower() in _SHELL_HOSTS:
        if not subcommands or not arg_patterns:
            return None, (
                f"{program} 是万能壳：必须单独列条目并锁死参数模板"
                "（subcommands 与 arg_patterns 都不能为空）"
            )

    return {
        "capability_id": capability_id.strip(),
        "description": description,
        "program": program,
        "subcommands": subcommands,
        "arg_patterns": arg_patterns,
        "forbidden_args": forbidden_args,
        "allow_shell": allow_shell,
        "allowed_cwds": allowed_cwds,
        "max_seconds": max_seconds,
        "adapter": adapter.strip() if adapter else None,
        "require_shell": require_shell,
        "requires_approval": requires_approval,
    }, None


def entries_to_capabilities(
    entries: List[Dict[str, Any]],
) -> List[HostCommandCapability]:
    """规范化条目 → 能力对象（调用前须已校验过）。"""
    caps: List[HostCommandCapability] = []
    for item in entries:
        caps.append(
            HostCommandCapability(
                capability_id=item["capability_id"],
                description=item.get("description", ""),
                program=item.get("program"),
                subcommands=item.get("subcommands"),
                arg_patterns=item.get("arg_patterns"),
                forbidden_args=item.get("forbidden_args"),
                allow_shell=bool(item.get("allow_shell", False)),
                allowed_cwds=item.get("allowed_cwds"),
                max_seconds=item.get("max_seconds"),
                adapter=item.get("adapter"),
                require_shell=item.get("require_shell"),
                requires_approval=bool(item.get("requires_approval", False)),
            )
        )
    return caps


def load_allowlist() -> Tuple[List[Dict[str, Any]], bool]:
    """读白名单文件；返回 ``(条目列表, 是否损坏)``。

    文件不存在 → 空名单（出厂状态），不算损坏。文件损坏或条目不合法 →
    空名单 + damaged=True（fail-closed：默认 DENY），并记 warning。
    """
    path = Path(allowlist_path())
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], False
    except OSError as exc:
        logger.warning("host-command allowlist unreadable (%s): %s", path, exc)
        return [], True
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        logger.warning("host-command allowlist corrupt (%s): %s", path, exc)
        return [], True
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        logger.warning("host-command allowlist has bad shape (%s)", path)
        return [], True
    entries: List[Dict[str, Any]] = []
    for item in payload["entries"]:
        normalized, error = validate_allowlist_entry(item)
        if error is not None or normalized is None:
            logger.warning(
                "host-command allowlist entry invalid (%s): %s", path, error
            )
            return [], True
        entries.append(normalized)
    return entries, False


def load_capabilities() -> List[HostCommandCapability]:
    """落盘白名单 → 能力对象；损坏时返回空列表（默认 DENY）。"""
    entries, _damaged = load_allowlist()
    return entries_to_capabilities(entries)


def _prune_versions() -> None:
    """只留最近 ALLOWLIST_MAX_VERSIONS 份旧版本。"""
    directory = _versions_dir()
    if not directory.is_dir():
        return
    files = sorted(directory.glob("*.json"), key=lambda p: p.name, reverse=True)
    for stale in files[ALLOWLIST_MAX_VERSIONS:]:
        try:
            stale.unlink()
        except OSError:
            pass


def list_versions() -> List[Dict[str, Any]]:
    """旧版本清单（按时间倒序）：文件名、时间、条目数。"""
    directory = _versions_dir()
    if not directory.is_dir():
        return []
    versions: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name, reverse=True):
        payload: Dict[str, Any] = {}
        count = -1
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
                count = len(payload.get("entries", []))
        except (OSError, ValueError):
            pass
        versions.append(
            {
                "version": path.name,
                "updated_at": payload.get("updated_at"),
                "updated_by": payload.get("updated_by"),
                "count": count,
            }
        )
    return versions


def save_allowlist(entries: List[Dict[str, Any]], *, principal: str) -> Dict[str, Any]:
    """原子落盘整张白名单（调用前须已逐条校验过）。

    保存前把当前文件留一份带时间戳的旧版本（只留最近 5 份）。返回
    ``{"count", "updated_at", "backup_version"}``（backup_version 是
    本次留档的旧版本文件名；首次保存、没有旧文件时为 None）。
    """
    path = Path(allowlist_path())
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    payload = {
        "version": 1,
        "updated_at": now.isoformat(),
        "updated_by": principal,
        "entries": entries,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    backup_name: Optional[str] = None
    with _lock:
        with exclusive_file_lock(f"{path}.lock", description="主机命令白名单"):
            if path.exists():
                directory = _versions_dir()
                directory.mkdir(parents=True, exist_ok=True)
                backup = directory / f"{stamp}.json"
                try:
                    backup.write_text(
                        path.read_text(encoding="utf-8"), encoding="utf-8"
                    )
                    backup_name = backup.name
                except OSError as exc:
                    logger.warning("host-command allowlist backup failed: %s", exc)
                _prune_versions()
            atomic_write_text(path, text)
    return {
        "count": len(entries),
        "updated_at": payload["updated_at"],
        "backup_version": backup_name,
    }


def rollback_allowlist(version: str, *, principal: str) -> List[Dict[str, Any]]:
    """回到某一旧版本（当前版本先留档，再恢复旧文件）。

    ``version`` 是 list_versions() 里的文件名。恢复后返回新生效的条目。
    """
    directory = _versions_dir()
    backup = directory / version
    if not backup.is_file() or backup.suffix != ".json":
        raise FileNotFoundError(f"旧版本不存在：{version}")
    try:
        payload = json.loads(backup.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
    except (OSError, ValueError) as exc:
        raise ValueError(f"旧版本文件损坏：{version} ({exc})") from exc
    normalized: List[Dict[str, Any]] = []
    for item in entries:
        entry, error = validate_allowlist_entry(item)
        if error is not None or entry is None:
            raise ValueError(f"旧版本里有不合法条目：{error}")
        normalized.append(entry)
    save_allowlist(normalized, principal=principal)
    return normalized
