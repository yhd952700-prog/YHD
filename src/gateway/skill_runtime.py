"""技能运行时：真正把技能脚本跑起来，并把结果收回来。

设计边界（与本目录设计稿一致，不许在文案上拔高）
------------------------------------------------
* Windows 上采用 **低完整性令牌 + Job Object**：低完整性拦写不拦读，也不拦
  网络；Job Object 负责内存 / CPU / 子进程数 / 超时。
* 受限令牌（restricted token）在本机实测启动即 ``0xC0000142``，不采用；
  AppContainer 能挡文件与网络，但需要给解释器与每一级路径打 ACL，0.3.0 不采用。
* 因此 ``isolation`` 字段只会是 ``low_integrity+job`` 或 ``job_only``，界面
  据此展示「受限执行」，不展示「已隔离」。

所有运行都在 ``<data>/skill-runs/<run_id>/`` 临时目录里，跑完保留 7 天，
不往仓库或 ``D:\\LiuHaoData`` 写测试残留。
"""

from __future__ import annotations

import json
import locale
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .skill_import import DEFAULT_LIMITS, normalize_runtime
from .skill_store import SkillStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #
INTERPRETERS = ("python", "node", "powershell")
_SCRIPT_SUFFIX = {
    "python": (".py",),
    "node": (".js", ".mjs", ".cjs"),
    "powershell": (".ps1",),
}
_FORBIDDEN_ENV = {
    "PATH", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "PSModulePath",
    "COMSPEC", "PATHEXT",
}
_MAX_PARAMS_BYTES = 16 * 1024
_MAX_APPROVALS = 500
_APPROVAL_TTL_DAYS = 90
_RUN_RETENTION_DAYS = 7
_LATEST_LIMIT = 5
_LATEST_TTL_HOURS = 24

_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")

_AUDIT_LOCK = threading.Lock()
_REGISTRY_LOCK = threading.RLock()
_APPROVAL_LOCK = threading.RLock()
_LATEST_LOCK = threading.RLock()


class SkillRuntimeError(RuntimeError):
    """运行时不可用或运行失败；消息面向用户。"""


class SkillRunRefused(SkillRuntimeError):
    """闸门拒绝了本次运行（未授权 / 包被改动 / 脚本不存在）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def data_root() -> str:
    return os.path.dirname(os.path.abspath(SkillStore().path))


def packages_dir() -> str:
    return os.path.join(data_root(), "skills")


def runs_dir() -> str:
    return os.path.join(data_root(), "skill-runs")


def audit_dir() -> str:
    return os.path.join(data_root(), "skill-audit")


def registry_path() -> str:
    return os.path.join(data_root(), "skill_packages.json")


def approvals_path() -> str:
    return os.path.join(data_root(), "skill_approvals.json")


def latest_path() -> str:
    return os.path.join(runs_dir(), "latest.json")


def _atomic_write(path: str, payload: Any) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    import tempfile

    handle = None
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(prefix=".skill-rt-", suffix=".tmp", dir=directory)
        handle = os.fdopen(fd, "w", encoding="utf-8")
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_path, path)
        tmp_path = ""
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover
                pass
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:  # pragma: no cover
                pass


def _load_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------- #
# 技能包登记表（不碰 skill_store 的 JSON，保持旧记录格式）
# ---------------------------------------------------------------------- #
class SkillPackageRegistry:
    """``<data>/skill_packages.json``：装到磁盘的技能包与运行时声明。"""

    VERSION = 1

    def load(self) -> Dict[str, Dict[str, Any]]:
        with _REGISTRY_LOCK:
            try:
                raw = _load_json(registry_path(), {"version": self.VERSION, "packages": {}})
            except (OSError, json.JSONDecodeError) as exc:
                raise SkillRuntimeError(
                    f"技能包登记表读取失败：{registry_path()}（{exc}）。"
                ) from exc
            if not isinstance(raw, dict) or not isinstance(raw.get("packages"), dict):
                raise SkillRuntimeError(
                    f"技能包登记表格式不可识别：{registry_path()}。"
                )
            return {
                str(key): value
                for key, value in raw["packages"].items()
                if isinstance(value, dict)
            }

    def get(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return self.load().get(skill_id)

    def upsert(self, skill_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        with _REGISTRY_LOCK:
            packages = self.load()
            record = dict(entry)
            record["skill_id"] = skill_id
            record["updated_at"] = _now()
            record.setdefault("installed_at", record["updated_at"])
            packages[skill_id] = record
            _atomic_write(registry_path(), {"version": self.VERSION, "packages": packages})
            return dict(record)

    def remove(self, skill_id: str) -> Optional[Dict[str, Any]]:
        with _REGISTRY_LOCK:
            packages = self.load()
            removed = packages.pop(skill_id, None)
            if removed is not None:
                _atomic_write(
                    registry_path(), {"version": self.VERSION, "packages": packages}
                )
            return removed


# ---------------------------------------------------------------------- #
# 执行授权
# ---------------------------------------------------------------------- #
class SkillApprovalStore:
    """首次运行必须 Boss 点头；``always`` 记住，包或脚本一变就失效。"""

    VERSION = 1

    def _load(self) -> List[Dict[str, Any]]:
        try:
            raw = _load_json(approvals_path(), {"version": self.VERSION, "approvals": []})
        except (OSError, json.JSONDecodeError):
            return []
        rows = raw.get("approvals") if isinstance(raw, dict) else []
        return [row for row in rows if isinstance(row, dict)]

    def _save(self, rows: List[Dict[str, Any]]) -> None:
        _atomic_write(
            approvals_path(),
            {"version": self.VERSION, "approvals": rows[-_MAX_APPROVALS:]},
        )

    @staticmethod
    def _key(skill_id: str, entrypoint: str, package_hash: str, script_sha: str) -> Dict[str, str]:
        return {
            "skill_id": skill_id,
            "entrypoint": entrypoint,
            "package_hash": package_hash,
            "script_sha256": script_sha,
        }

    def _match(
        self,
        row: Dict[str, Any],
        key: Dict[str, str],
        *,
        approved_only: bool = True,
        require_uses: bool = True,
    ) -> bool:
        if any(str(row.get(field) or "") != value for field, value in key.items()):
            return False
        if approved_only and row.get("status") != "approved":
            return False
        if require_uses and int(row.get("uses_remaining", 0) or 0) == 0:
            # -1 表示"始终允许"，不算用尽。
            return False
        expires = _parse_iso(row.get("expires_at"))
        if expires is not None and expires < datetime.now(timezone.utc):
            return False
        return True

    def find_approved(
        self, skill_id: str, entrypoint: str, package_hash: str, script_sha: str
    ) -> Optional[Dict[str, Any]]:
        key = self._key(skill_id, entrypoint, package_hash, script_sha)
        with _APPROVAL_LOCK:
            for row in reversed(self._load()):
                if self._match(row, key):
                    return dict(row)
            return None

    def approved_entrypoints(
        self, skill_id: str, package_hash: str, entrypoints: List[Dict[str, Any]]
    ) -> List[str]:
        out: List[str] = []
        for item in entrypoints:
            if self.find_approved(
                skill_id,
                str(item.get("id") or ""),
                package_hash,
                str(item.get("script_sha256") or ""),
            ):
                out.append(str(item.get("id") or ""))
        return out

    def request(
        self,
        *,
        skill_id: str,
        skill_name: str,
        entrypoint: str,
        package_hash: str,
        script_sha256: str,
        script: str,
        interpreter: str,
        limits: Dict[str, Any],
        network: bool,
        requester: str,
        employee: Optional[str] = None,
    ) -> Dict[str, Any]:
        key = self._key(skill_id, entrypoint, package_hash, script_sha256)
        with _APPROVAL_LOCK:
            rows = self._load()
            now = datetime.now(timezone.utc)
            for row in rows:
                if (
                    row.get("status") == "pending"
                    and self._match(row, key, approved_only=False, require_uses=False)
                ):
                    return dict(row)
            record = {
                "approval_id": f"appr_{uuid.uuid4().hex[:12]}",
                **key,
                "skill_name": skill_name,
                "script": script,
                "interpreter": interpreter,
                "limits": dict(limits or {}),
                "network": bool(network),
                "status": "pending",
                "requested_by": requester,
                "requested_at": _now(),
                "employee": employee or "",
                "uses_remaining": 0,
                "approved_by": "",
                "approved_at": "",
                "expires_at": "",
            }
            rows.append(record)
            self._save(rows)
            return dict(record)

    def approve(self, approval_id: str, *, mode: str, approved_by: str) -> Dict[str, Any]:
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in ("once", "always"):
            raise SkillRunRefused("授权方式只能是 once（仅本次）或 always（始终允许）。")
        with _APPROVAL_LOCK:
            rows = self._load()
            for row in rows:
                if str(row.get("approval_id") or "") != approval_id:
                    continue
                if row.get("status") != "pending":
                    raise SkillRunRefused("这条授权已经处理过了，请刷新后重新发起运行。")
                row["status"] = "approved"
                row["mode"] = clean_mode
                row["uses_remaining"] = 1 if clean_mode == "once" else -1
                row["approved_by"] = approved_by
                row["approved_at"] = _now()
                if clean_mode == "always":
                    row["expires_at"] = (
                        datetime.now(timezone.utc) + timedelta(days=_APPROVAL_TTL_DAYS)
                    ).isoformat().replace("+00:00", "Z")
                self._save(rows)
                return dict(row)
            raise SkillRunRefused("没有找到这条授权申请，可能已过期或已被处理。")

    def reject(self, approval_id: str, *, rejected_by: str) -> Dict[str, Any]:
        with _APPROVAL_LOCK:
            rows = self._load()
            for row in rows:
                if str(row.get("approval_id") or "") != approval_id:
                    continue
                row["status"] = "rejected"
                row["rejected_by"] = rejected_by
                row["rejected_at"] = _now()
                row["uses_remaining"] = 0
                self._save(rows)
                return dict(row)
            raise SkillRunRefused("没有找到这条授权申请。")

    def consume(self, approval_id: str) -> None:
        with _APPROVAL_LOCK:
            rows = self._load()
            changed = False
            for row in rows:
                if str(row.get("approval_id") or "") != approval_id:
                    continue
                if int(row.get("uses_remaining", 0) or 0) > 0:
                    row["uses_remaining"] = int(row["uses_remaining"]) - 1
                    changed = True
            if changed:
                self._save(rows)

    def remembered(self, skill_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出仍然有效的「记住的授权」；``skill_id`` 为空表示全部技能。"""
        now = datetime.now(timezone.utc)
        out: List[Dict[str, Any]] = []
        with _APPROVAL_LOCK:
            for row in self._load():
                if row.get("status") != "approved" or row.get("mode") != "always":
                    continue
                if int(row.get("uses_remaining", 0) or 0) == 0:
                    continue
                expires = _parse_iso(row.get("expires_at"))
                if expires is not None and expires < now:
                    continue
                if skill_id and str(row.get("skill_id") or "") != skill_id:
                    continue
                out.append(dict(row))
        return out

    def revoke(self, approval_id: str, *, revoked_by: str) -> Dict[str, Any]:
        """撤销一条已记住的授权（一次性授权已被消费，无需撤销）。"""
        with _APPROVAL_LOCK:
            rows = self._load()
            for row in rows:
                if str(row.get("approval_id") or "") != approval_id:
                    continue
                row["status"] = "revoked"
                row["revoked_by"] = revoked_by
                row["revoked_at"] = _now()
                row["uses_remaining"] = 0
                self._save(rows)
                return dict(row)
            raise SkillRunRefused("没有找到这条授权，可能已经清除了。")

    def revoke_all(self, *, revoked_by: str, skill_id: Optional[str] = None) -> int:
        """清除某个技能（或全部技能）记住的授权，返回清除条数。"""
        cleared = 0
        now = datetime.now(timezone.utc)
        with _APPROVAL_LOCK:
            rows = self._load()
            for row in rows:
                if row.get("status") != "approved" or row.get("mode") != "always":
                    continue
                if int(row.get("uses_remaining", 0) or 0) == 0:
                    continue
                expires = _parse_iso(row.get("expires_at"))
                if expires is not None and expires < now:
                    continue
                if skill_id and str(row.get("skill_id") or "") != skill_id:
                    continue
                row["status"] = "revoked"
                row["revoked_by"] = revoked_by
                row["revoked_at"] = _now()
                row["uses_remaining"] = 0
                cleared += 1
            if cleared:
                self._save(rows)
        return cleared

    def pending(self, skill_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with _APPROVAL_LOCK:
            return [
                dict(row)
                for row in self._load()
                if row.get("status") == "pending"
                and (not skill_id or str(row.get("skill_id") or "") == skill_id)
            ]


# ---------------------------------------------------------------------- #
# 审计
# ---------------------------------------------------------------------- #
def append_audit(event: Dict[str, Any]) -> None:
    payload = {"ts": _now(), **event}
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _AUDIT_LOCK:
        os.makedirs(audit_dir(), exist_ok=True)
        path = os.path.join(audit_dir(), f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())


def read_audit(skill_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not os.path.isdir(audit_dir()):
        return rows
    files = sorted(
        name for name in os.listdir(audit_dir()) if name.startswith("audit-")
    )
    for name in reversed(files):
        path = os.path.join(audit_dir(), name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("skill_id") or "") != skill_id:
                continue
            rows.append(row)
            if len(rows) >= max(1, min(limit, 500)):
                return rows
    return rows


# ---------------------------------------------------------------------- #
# 最近运行结果（供 skill_prompt 注入员工上下文）
# ---------------------------------------------------------------------- #
def _update_latest(result: Dict[str, Any]) -> None:
    if result.get("status") not in ("ok", "failed", "timeout", "error"):
        return
    with _LATEST_LOCK:
        try:
            raw = _load_json(latest_path(), {"version": 1, "skills": {}})
        except (OSError, json.JSONDecodeError):
            raw = {"version": 1, "skills": {}}
        skills = raw.get("skills") if isinstance(raw, dict) else {}
        if not isinstance(skills, dict):
            skills = {}
        skill_id = str(result.get("skill_id") or "")
        rows = [
            row for row in (skills.get(skill_id) or [])
            if isinstance(row, dict)
        ]
        rows.append(
            {
                "run_id": result.get("run_id"),
                "entrypoint_id": result.get("entrypoint_id"),
                "status": result.get("status"),
                "ok": result.get("status") == "ok",
                "summary": str(result.get("summary") or "")[:1200],
                "finished_at": result.get("finished_at") or _now(),
                "employee": result.get("employee") or "",
            }
        )
        cutoff = datetime.now(timezone.utc) - timedelta(hours=_LATEST_TTL_HOURS)
        kept: List[Dict[str, Any]] = []
        for row in rows:
            stamp = _parse_iso(row.get("finished_at"))
            if stamp is not None and stamp < cutoff:
                continue
            kept.append(row)
        skills[skill_id] = kept[-_LATEST_LIMIT:]
        _atomic_write(latest_path(), {"version": 1, "skills": skills})


def latest_results(skill_ids: List[str]) -> List[Dict[str, Any]]:
    try:
        raw = _load_json(latest_path(), {"version": 1, "skills": {}})
    except (OSError, json.JSONDecodeError):
        return []
    skills = raw.get("skills") if isinstance(raw, dict) else {}
    if not isinstance(skills, dict):
        return []
    wanted = set(skill_ids)
    rows: List[Dict[str, Any]] = []
    for skill_id, items in skills.items():
        if skill_id not in wanted or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                rows.append({**item, "skill_id": skill_id})
    rows.sort(key=lambda row: str(row.get("finished_at") or ""), reverse=True)
    return rows


# ---------------------------------------------------------------------- #
# Windows 进程原语
# ---------------------------------------------------------------------- #
if os.name == "nt":  # pragma: no branch - Windows 是目标平台
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(wintypes.BYTE)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _JOB_BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOB_EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOB_BASIC_LIMIT),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOB_BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_MANDATORY_LABEL(ctypes.Structure):
        _fields_ = [("Label", _SID_AND_ATTRIBUTES)]

    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _CREATE_SUSPENDED = 0x00000004
    _CREATE_NO_WINDOW = 0x08000000
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _STARTF_USESTDHANDLES = 0x00000100
    _TOKEN_ALL_ACCESS = 0xF01FF
    _TOKEN_INTEGRITY_LEVEL = 25
    _SECURITY_IMPERSONATION = 2
    _TOKEN_PRIMARY = 1

    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_SECURITY_ATTRIBUTES), wintypes.DWORD, wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.CreateProcessW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, ctypes.c_wchar_p,
        ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    _advapi32.DuplicateTokenEx.restype = wintypes.BOOL
    _advapi32.DuplicateTokenEx.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, ctypes.c_int,
        ctypes.c_int, ctypes.POINTER(wintypes.HANDLE),
    ]
    _advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSidToSidW.argtypes = [
        ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)
    ]
    _advapi32.SetTokenInformation.restype = wintypes.BOOL
    _advapi32.SetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _advapi32.GetLengthSid.restype = wintypes.DWORD
    _advapi32.GetLengthSid.argtypes = [ctypes.c_void_p]
    _advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    _advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_wchar_p, ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]

    # ---- 目录低完整性标签：让低完整性进程能在运行目录里新建文件 ----
    _SE_FILE_OBJECT = 1
    _LABEL_SECURITY_INFORMATION = 0x10
    _ACL_REVISION = 2
    _SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
    _OBJECT_INHERIT_ACE = 0x1
    _CONTAINER_INHERIT_ACE = 0x2
    _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP = 0x1
    _LOW_INTEGRITY_SID = "S-1-16-4096"

    class _ACL(ctypes.Structure):
        _fields_ = [
            ("AclRevision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("AclSize", wintypes.WORD),
            ("AceCount", wintypes.WORD),
            ("Sbz2", wintypes.WORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _SYSTEM_MANDATORY_LABEL_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    _advapi32.InitializeAcl.restype = wintypes.BOOL
    _advapi32.InitializeAcl.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD
    ]
    _advapi32.AddAce.restype = wintypes.BOOL
    _advapi32.AddAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    class _LUID(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class _LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", _LUID), ("Attributes", wintypes.DWORD)]

    class _TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [
            ("PrivilegeCount", wintypes.DWORD),
            ("Privileges", _LUID_AND_ATTRIBUTES * 1),
        ]

    _advapi32.LookupPrivilegeValueW.restype = wintypes.BOOL
    _advapi32.LookupPrivilegeValueW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(_LUID)
    ]
    _advapi32.AdjustTokenPrivileges.restype = wintypes.BOOL
    _advapi32.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE, wintypes.BOOL, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, ctypes.c_void_p,
    ]

    def _enable_privilege(name: str) -> bool:
        """临时启用一个当前令牌里已有的特权（例如 SeSecurityPrivilege）。"""
        handle = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_ALL_ACCESS, ctypes.byref(handle)
        ):
            return False
        try:
            luid = _LUID()
            if not _advapi32.LookupPrivilegeValueW(
                None, name, ctypes.byref(luid)
            ):
                return False
            privileges = _TOKEN_PRIVILEGES()
            privileges.PrivilegeCount = 1
            privileges.Privileges[0].Luid = luid
            privileges.Privileges[0].Attributes = 0x2  # SE_PRIVILEGE_ENABLED
            if not _advapi32.AdjustTokenPrivileges(
                handle, False, ctypes.byref(privileges), 0, None, None
            ):
                return False
            return ctypes.get_last_error() == 0
        finally:
            _kernel32.CloseHandle(handle)

    def _apply_low_label(path: str) -> bool:
        """给运行目录打 Low 强制完整性标签（含继承），失败返回 ``False``。

        为什么需要它：低完整性进程对中完整性目录**没有写权限**（no-write-up）。
        没有这一步，脚本连自己的 ``workspace`` 都写不进去。打标签后新建的文件
        会继承 Low 标签，低完整性进程可读写；目录的 DACL 不变。

        注意：完整性标签存在 SACL 里，设置 SACL 需要 ``SeSecurityPrivilege``；
        Administrator 令牌默认带着但**未启用**，这里先启用再设置。
        """
        _enable_privilege("SeSecurityPrivilege")
        sid = ctypes.c_void_p()
        try:
            if not _advapi32.ConvertStringSidToSidW(
                _LOW_INTEGRITY_SID, ctypes.byref(sid)
            ):
                return False
            sid_len = _advapi32.GetLengthSid(sid)
            ace_size = (
                ctypes.sizeof(_SYSTEM_MANDATORY_LABEL_ACE)
                + sid_len
                - ctypes.sizeof(wintypes.DWORD)
            )
            acl_size = ctypes.sizeof(_ACL) + ace_size
            acl_buffer = ctypes.create_string_buffer(acl_size)
            acl = ctypes.cast(acl_buffer, ctypes.c_void_p)
            if not _advapi32.InitializeAcl(acl, acl_size, _ACL_REVISION):
                return False
            ace_buffer = ctypes.create_string_buffer(ace_size)
            ace = ctypes.cast(
                ace_buffer, ctypes.POINTER(_SYSTEM_MANDATORY_LABEL_ACE)
            )
            ace.contents.Header.AceType = _SYSTEM_MANDATORY_LABEL_ACE_TYPE
            ace.contents.Header.AceFlags = (
                _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE
            )
            ace.contents.Header.AceSize = ace_size
            ace.contents.Mask = _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP
            sid_offset = _SYSTEM_MANDATORY_LABEL_ACE.SidStart.offset
            ctypes.memmove(
                ctypes.addressof(ace.contents) + sid_offset, sid, sid_len
            )
            if not _advapi32.AddAce(
                acl, _ACL_REVISION, 0, ctypes.cast(ace, ctypes.c_void_p), ace_size
            ):
                return False
            status = _advapi32.SetNamedSecurityInfoW(
                path,
                _SE_FILE_OBJECT,
                _LABEL_SECURITY_INFORMATION,
                None,
                None,
                None,
                acl,
            )
            return status == 0
        except Exception:  # noqa: BLE001 - 标签失败时上层会退回普通令牌
            return False
        finally:
            if sid:
                _kernel32.LocalFree(sid)

    def _low_integrity_token() -> wintypes.HANDLE:
        """复制当前进程令牌并把完整性级别降到 Low（S-1-16-4096）。"""
        base = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_ALL_ACCESS, ctypes.byref(base)
        ):
            raise OSError(ctypes.get_last_error(), "OpenProcessToken failed")
        try:
            duplicated = wintypes.HANDLE()
            if not _advapi32.DuplicateTokenEx(
                base, _TOKEN_ALL_ACCESS, None, _SECURITY_IMPERSONATION,
                _TOKEN_PRIMARY, ctypes.byref(duplicated),
            ):
                raise OSError(ctypes.get_last_error(), "DuplicateTokenEx failed")
            sid = ctypes.c_void_p()
            if not _advapi32.ConvertStringSidToSidW("S-1-16-4096", ctypes.byref(sid)):
                _kernel32.CloseHandle(duplicated)
                raise OSError(ctypes.get_last_error(), "ConvertStringSidToSidW failed")
            label = _TOKEN_MANDATORY_LABEL()
            label.Label.Sid = sid
            label.Label.Attributes = 0x20
            size = ctypes.sizeof(_TOKEN_MANDATORY_LABEL) + _advapi32.GetLengthSid(sid)
            if not _advapi32.SetTokenInformation(
                duplicated, _TOKEN_INTEGRITY_LEVEL, ctypes.byref(label), size
            ):
                _kernel32.CloseHandle(duplicated)
                raise OSError(ctypes.get_last_error(), "SetTokenInformation failed")
            return duplicated
        finally:
            _kernel32.CloseHandle(base)

    def _create_job(limits: Dict[str, Any]) -> wintypes.HANDLE:
        job = _kernel32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        info = _JOB_EXTENDED_LIMIT()
        flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = int(limits["memory_mb"]) * 1024 * 1024
        info.JobMemoryLimit = int(limits["job_memory_mb"]) * 1024 * 1024
        info.BasicLimitInformation.ActiveProcessLimit = int(limits["max_processes"])
        info.BasicLimitInformation.PerProcessUserTimeLimit = (
            int(limits["cpu_seconds"]) * 10_000_000
        )
        flags |= (
            _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_PROCESS_TIME
        )
        info.BasicLimitInformation.LimitFlags = flags
        if not _kernel32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            _kernel32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        return job

    def _job_peak_memory(job: wintypes.HANDLE) -> int:
        info = _JOB_EXTENDED_LIMIT()
        returned = wintypes.DWORD(0)
        if _kernel32.QueryInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
            ctypes.sizeof(info), ctypes.byref(returned),
        ):
            return int(info.PeakJobMemoryUsed or 0)
        return 0

    def _job_cpu_ms(job: wintypes.HANDLE) -> int:
        info = _JOB_BASIC_ACCOUNTING()
        returned = wintypes.DWORD(0)
        if _kernel32.QueryInformationJobObject(
            job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(info),
            ctypes.sizeof(info), ctypes.byref(returned),
        ):
            return int((info.TotalUserTime + info.TotalKernelTime) / 10_000)
        return 0

    def _create_inheritable_file(path: str, mode: str) -> wintypes.HANDLE:
        access = 0x80000000 if mode == "r" else 0x40000000
        sa = _SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(_SECURITY_ATTRIBUTES)
        sa.bInheritHandle = True
        handle = _kernel32.CreateFileW(
            path, access, 1 | 2, ctypes.byref(sa), 3 if mode == "r" else 2, 0x80, None
        )
        if not handle or handle == wintypes.HANDLE(-1).value:
            raise OSError(ctypes.get_last_error(), f"CreateFileW failed: {path}")
        return handle

    def _environment_block(env: Dict[str, str]) -> ctypes.Array:
        text = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0\0"
        return ctypes.create_unicode_buffer(text, len(text) + 1)

    def _launch(
        *,
        exe: str,
        argv: List[str],
        cwd: str,
        env: Dict[str, str],
        stdin_path: str,
        stdout_path: str,
        stderr_path: str,
        job: wintypes.HANDLE,
        token: Optional[wintypes.HANDLE],
    ) -> Tuple[_PROCESS_INFORMATION, str]:
        stdin_handle = _create_inheritable_file(stdin_path, "r")
        stdout_handle = _create_inheritable_file(stdout_path, "w")
        stderr_handle = _create_inheritable_file(stderr_path, "w")
        si = _STARTUPINFOW()
        si.cb = ctypes.sizeof(_STARTUPINFOW)
        si.dwFlags = _STARTF_USESTDHANDLES
        si.hStdInput = stdin_handle
        si.hStdOutput = stdout_handle
        si.hStdError = stderr_handle
        pi = _PROCESS_INFORMATION()
        env_buffer = _environment_block(env)
        command_line = subprocess.list2cmdline([exe] + argv)
        flags = _CREATE_SUSPENDED | _CREATE_NO_WINDOW | _CREATE_UNICODE_ENVIRONMENT
        used = "low_integrity"
        ok = False
        if token is not None:
            ok = bool(
                _advapi32.CreateProcessAsUserW(
                    token, exe, command_line, None, None, True, flags,
                    ctypes.cast(env_buffer, ctypes.c_void_p), cwd,
                    ctypes.byref(si), ctypes.byref(pi),
                )
            )
        last_error = ctypes.get_last_error()
        if not ok:
            used = "normal_token"
            ok = bool(
                _kernel32.CreateProcessW(
                    exe, command_line, None, None, True, flags,
                    ctypes.cast(env_buffer, ctypes.c_void_p), cwd,
                    ctypes.byref(si), ctypes.byref(pi),
                )
            )
            last_error = ctypes.get_last_error()
        for handle in (stdin_handle, stdout_handle, stderr_handle):
            _kernel32.CloseHandle(handle)
        if not ok:
            raise OSError(last_error, f"创建技能进程失败（WinError {last_error}）。")
        if not _kernel32.AssignProcessToJobObject(job, pi.hProcess):
            error = ctypes.get_last_error()
            _kernel32.TerminateJobObject(job, 1)
            _kernel32.CloseHandle(pi.hProcess)
            _kernel32.CloseHandle(pi.hThread)
            raise OSError(error, f"把技能进程挂到 Job Object 失败（WinError {error}）。")
        _kernel32.ResumeThread(pi.hThread)
        return pi, used

    def _wait(pi: _PROCESS_INFORMATION, job: wintypes.HANDLE, timeout_s: int) -> Tuple[int, bool]:
        wait = _kernel32.WaitForSingleObject(pi.hProcess, int(timeout_s) * 1000)
        timed_out = wait == 0x102  # WAIT_TIMEOUT
        if timed_out:
            _kernel32.TerminateJobObject(job, 1460)
            _kernel32.WaitForSingleObject(pi.hProcess, 5000)
        code = wintypes.DWORD(0)
        _kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
        return int(code.value), timed_out

    def _close(handle: Any) -> None:
        try:
            if handle:
                _kernel32.CloseHandle(handle)
        except Exception:  # pragma: no cover
            pass

else:  # pragma: no cover - 非 Windows 的兜底，仅用于开发机
    def _low_integrity_token() -> Any:
        return None

    def _create_job(limits: Dict[str, Any]) -> Any:
        return None

    def _job_peak_memory(job: Any) -> int:
        return 0

    def _job_cpu_ms(job: Any) -> int:
        return 0

    def _close(handle: Any) -> None:
        return None

    def _launch(**kwargs: Any) -> Tuple[Any, str]:
        raise SkillRuntimeError("技能脚本执行目前只支持 Windows。")


# ---------------------------------------------------------------------- #
# 解释器与命令
# ---------------------------------------------------------------------- #
def resolve_interpreter(kind: str) -> Optional[str]:
    if kind == "python":
        return sys.executable
    if kind == "node":
        return shutil.which("node")
    if kind == "powershell":
        root = os.environ.get("SystemRoot") or r"C:\Windows"
        candidate = os.path.join(
            root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
        )
        return candidate if os.path.exists(candidate) else None
    return None


_PY_RUNNER = r"""
import json, os, runpy, sys
script = sys.argv[1]
work = os.path.abspath(sys.argv[2])
input_path = sys.argv[3]
with open(input_path, "r", encoding="utf-8") as fh:
    params = json.load(fh)
os.environ["LIUHAO_SKILL_PARAMS"] = json.dumps(params, ensure_ascii=False)
os.environ["LIUHAO_RUN_DIR"] = work
allow_network = os.environ.get("LIUHAO_ALLOW_NETWORK") == "1"
work_prefix = work.rstrip("\\/") + os.sep
def _hook(event, args):
    if event == "open":
        path, mode = args[0], args[1]
        if isinstance(path, str) and mode and any(ch in mode for ch in "wax+"):
            full = os.path.abspath(path)
            if not (full == work or full.startswith(work_prefix)):
                raise PermissionError("技能只允许写运行目录：" + full)
    if not allow_network and event in ("socket.connect", "socket.getaddrinfo"):
        raise PermissionError("该技能未声明联网，本次运行禁止网络访问。")
    if event in ("ctypes.dlopen", "ctypes.dlsym"):
        raise PermissionError("技能运行禁止加载原生库。")
sys.addaudithook(_hook)
sys.argv = [script] + sys.argv[4:]
runpy.run_path(script, run_name="__main__")
"""


def build_command(kind: str, script_path: str, run_dir: str, input_path: str) -> List[str]:
    exe = resolve_interpreter(kind)
    if not exe:
        raise SkillRuntimeError(f"本机没有可用的 {kind} 解释器，无法运行该技能。")
    if kind == "python":
        # -I：隔离模式（忽略 PYTHON* 环境变量、不把 cwd 放进 sys.path）。
        # -X utf8：-I 会顺带忽略 PYTHONIOENCODING，所以显式开 UTF-8 模式，
        #          否则中文输出在 cp936 控制台下会变成乱码。
        return [
            exe, "-I", "-X", "utf8", "-c", _PY_RUNNER, script_path, run_dir,
            input_path,
        ]
    if kind == "node":
        return [
            exe, "--permission",
            f"--allow-fs-read={run_dir}",
            f"--allow-fs-read={os.path.dirname(script_path)}",
            f"--allow-fs-write={run_dir}",
            script_path, input_path,
        ]
    return [
        exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", script_path, input_path,
    ]


def _env_for_run(
    run_dir: str, skill_id: str, entrypoint_id: str, extra: Dict[str, str]
) -> Dict[str, str]:
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    env = {
        "SYSTEMROOT": system_root,
        "WINDIR": system_root,
        "PATH": os.path.join(system_root, "System32") + ";" + system_root,
        "TEMP": run_dir,
        "TMP": run_dir,
        "HOME": run_dir,
        "USERPROFILE": run_dir,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "LIUHAO_SKILL_ID": skill_id,
        "LIUHAO_ENTRYPOINT": entrypoint_id,
        "LIUHAO_RUN_DIR": run_dir,
    }
    for key, value in (extra or {}).items():
        if str(key).upper() in _FORBIDDEN_ENV:
            continue
        env[str(key)] = str(value)
    return env


def _truncate(path: str, limit: int) -> Tuple[str, int]:
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            raw = handle.read(limit + 1 if size > limit else limit)
    except OSError:
        return "", 0
    truncated = max(0, size - limit)
    payload = raw[:limit]
    try:
        return payload.decode("utf-8"), truncated
    except UnicodeDecodeError:
        # PowerShell 5.1 在重定向时按控制台代码页输出；退回本机编码，
        # 总比把中文显示成问号强。Python/Node 走 UTF-8 分支。
        try:
            return payload.decode(locale.getpreferredencoding(False) or "utf-8",
                                  errors="replace"), truncated
        except LookupError:  # pragma: no cover
            return payload.decode("utf-8", errors="replace"), truncated


def _parse_result(stdout: str, exit_code: int) -> Tuple[str, Any, str]:
    text = stdout.strip()
    if text:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                summary = str(payload.get("summary") or "").strip()
                if not summary:
                    summary = json.dumps(payload, ensure_ascii=False)[:2000]
                return summary[:4000], payload, "json"
        except json.JSONDecodeError:
            pass
    summary = text[-4000:] if text else (
        "脚本没有输出内容。" if exit_code == 0 else f"脚本退出码 {exit_code}，没有 stdout。"
    )
    return summary, None, "text"


# ---------------------------------------------------------------------- #
# 执行入口
# ---------------------------------------------------------------------- #
def run_package(
    *,
    skill_id: str,
    skill_name: str,
    package: Dict[str, Any],
    entrypoint_id: str,
    params: Dict[str, Any],
    requester: str,
    employee: Optional[str] = None,
    approval: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    runtime = package.get("runtime") or {}
    entrypoints = runtime.get("entrypoints") or []
    entry = next(
        (item for item in entrypoints if str(item.get("id") or "") == entrypoint_id),
        None,
    )
    if entry is None:
        raise SkillRunRefused(f"技能没有名为「{entrypoint_id}」的运行入口。")
    install_dir = str(package.get("install_dir") or "")
    if not install_dir or not os.path.isdir(install_dir):
        raise SkillRunRefused("技能包目录不存在，请重新安装该技能。")
    rel_script = str(entry.get("script") or "")
    script_path = os.path.abspath(os.path.join(install_dir, rel_script))
    prefix = os.path.abspath(install_dir).rstrip("\\/") + os.sep
    if not script_path.startswith(prefix) or not os.path.isfile(script_path):
        raise SkillRunRefused("技能脚本不在包目录内或已被删除，拒绝运行。")
    expected = str(entry.get("script_sha256") or "")
    actual = _sha256_file(script_path)
    if expected and actual != expected:
        raise SkillRunRefused(
            "技能脚本与安装时记录的哈希不一致（可能被改动），已拒绝运行，请重新安装。"
        )

    limits = {**DEFAULT_LIMITS, **(runtime.get("limits") or {})}
    params_bytes = json.dumps(params, ensure_ascii=False).encode("utf-8")
    if len(params_bytes) > _MAX_PARAMS_BYTES:
        raise SkillRunRefused(
            f"运行参数超过 {_MAX_PARAMS_BYTES // 1024} KB 上限，请精简后再试。"
        )

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_root = os.path.join(runs_dir(), run_id)
    run_dir = os.path.join(run_root, "workspace")
    os.makedirs(run_dir, exist_ok=True)
    # 低完整性进程默认写不进中完整性目录；给运行目录打 Low 标签才能写。
    workspace_writable = _apply_low_label(run_dir)
    if not workspace_writable:
        logger.warning(
            "skill runtime: low integrity label on %s failed; falling back to normal token",
            run_dir,
        )
    input_path = os.path.join(run_root, "input.json")
    stdout_path = os.path.join(run_root, "stdout.txt")
    stderr_path = os.path.join(run_root, "stderr.txt")
    with open(input_path, "w", encoding="utf-8") as handle:
        json.dump(params, handle, ensure_ascii=False)

    cwd = run_dir if str(entry.get("cwd") or "workspace") != "skill" else install_dir
    env = _env_for_run(
        run_dir, skill_id, entrypoint_id, dict(entry.get("env") or {})
    )
    env["LIUHAO_ALLOW_NETWORK"] = "1" if entry.get("network") else "0"

    started = time.monotonic()
    audit_base = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "run_id": run_id,
        "entrypoint_id": entrypoint_id,
        "script": rel_script,
        "script_sha256": actual,
        "interpreter": entry.get("interpreter"),
        "package_hash": package.get("package_hash"),
        "requester": requester,
        "employee": employee or "",
        "approval_id": (approval or {}).get("approval_id", ""),
        "approval_mode": (approval or {}).get("mode", ""),
        "params_keys": sorted(str(key) for key in params),
        "params_bytes": len(params_bytes),
    }
    append_audit({"event": "start", **audit_base})

    result = {
        "run_id": run_id,
        "skill_id": skill_id,
        "skill_name": skill_name,
        "entrypoint_id": entrypoint_id,
        "status": "error",
        "exit_code": None,
        "timed_out": False,
        "duration_ms": 0,
        "peak_memory_bytes": 0,
        "cpu_ms": 0,
        "isolation": "unavailable",
        "limits": limits,
        "stdout": "",
        "stderr": "",
        "summary": "",
        "result": None,
        "truncated_bytes": 0,
        "employee": employee or "",
        "created_at": _now(),
        "finished_at": "",
    }
    job = None
    token = None
    pi = None
    try:
        argv = build_command(
            str(entry.get("interpreter")), script_path, run_dir, input_path
        )
        exe = resolve_interpreter(str(entry.get("interpreter")))
        if not exe:
            raise SkillRuntimeError(
                f"本机没有可用的 {entry.get('interpreter')} 解释器。"
            )
        # 关闭权限继承的额外静态参数只接受清单里的固定值，不接受用户输入。
        fixed_args = [str(arg) for arg in (entry.get("args") or [])]
        if fixed_args:
            argv = argv + fixed_args
        job = _create_job(limits)
        try:
            token = _low_integrity_token() if workspace_writable else None
        except Exception as exc:  # noqa: BLE001 - 低完整性不可用时降级，但如实记录
            logger.warning("skill runtime: low integrity token unavailable: %s", exc)
            token = None
        pi, used = _launch(
            exe=exe,
            argv=argv[1:],
            cwd=cwd,
            env=env,
            stdin_path=input_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            job=job,
            token=token,
        )
        result["isolation"] = "low_integrity+job" if used == "low_integrity" else "job_only"
        exit_code, timed_out = _wait(pi, job, int(limits["timeout_seconds"]))
        result["exit_code"] = exit_code
        result["timed_out"] = timed_out
        result["peak_memory_bytes"] = _job_peak_memory(job)
        result["cpu_ms"] = _job_cpu_ms(job)
        stdout, out_truncated = _truncate(stdout_path, int(limits["max_output_bytes"]))
        stderr, err_truncated = _truncate(stderr_path, 8192)
        result["truncated_bytes"] = out_truncated + err_truncated
        result["stdout"] = stdout
        result["stderr"] = stderr
        summary, payload, _kind = _parse_result(stdout, exit_code)
        result["summary"] = summary
        result["result"] = payload
        if timed_out:
            result["status"] = "timeout"
        elif exit_code == 0:
            result["status"] = "ok"
        else:
            result["status"] = "failed"
    except Exception as exc:  # noqa: BLE001 - 运行失败要如实返回，不假装成功
        result["status"] = "error"
        result["summary"] = f"{type(exc).__name__}: {exc}"
        logger.warning("skill runtime: run %s failed: %s", run_id, exc)
        try:
            append_audit(
                {"event": "finish", **audit_base, "status": "error",
                 "error": f"{type(exc).__name__}: {exc}"}
            )
        except Exception:  # pragma: no cover
            logger.exception("skill runtime: finish audit failed")
    finally:
        if pi is not None:
            _close(getattr(pi, "hProcess", None))
            _close(getattr(pi, "hThread", None))
        _close(token)
        _close(job)

    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    result["finished_at"] = _now()
    result["stdout"] = result.get("stdout") or ""
    result["stderr"] = result.get("stderr") or ""
    try:
        _atomic_write(os.path.join(run_root, "run.json"), result)
        _update_latest(result)
    except OSError:
        logger.exception("skill runtime: failed to persist run %s", run_id)
    try:
        append_audit(
            {
                "event": "finish",
                **audit_base,
                "status": result["status"],
                "exit_code": result["exit_code"],
                "timed_out": result["timed_out"],
                "duration_ms": result["duration_ms"],
                "peak_memory_bytes": result["peak_memory_bytes"],
                "output_bytes": len(result["stdout"].encode("utf-8")),
                "truncated_bytes": result["truncated_bytes"],
                "stdout_head": result["stdout"][:2000],
                "stderr_head": result["stderr"][:2000],
            }
        )
    except OSError:
        logger.exception("skill runtime: finish audit failed for %s", run_id)
    if approval and str(approval.get("mode") or "") == "once":
        try:
            SkillApprovalStore().consume(str(approval.get("approval_id")))
        except Exception:  # pragma: no cover
            logger.exception("skill runtime: consume approval failed")
    return result


def read_run(run_id: str) -> Optional[Dict[str, Any]]:
    if not re.match(r"^run_[a-f0-9]{12}$", str(run_id or "")):
        return None
    path = os.path.join(runs_dir(), run_id, "run.json")
    try:
        return _load_json(path, None)
    except (OSError, json.JSONDecodeError):
        return None


def _sha256_file(path: str) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str) -> str:
    """公开的脚本哈希计算（供 ``skills.py`` 的安装校验复用）。"""
    return _sha256_file(path)


# ---------------------------------------------------------------------- #
# 给 skills.py 用的组合门面
# ---------------------------------------------------------------------- #
class SkillRuntime:
    """把登记表、授权、执行、审计收在一个门面里，路由层只调这里。"""

    def __init__(self) -> None:
        self.registry = SkillPackageRegistry()
        self.approvals = SkillApprovalStore()

    def package(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return self.registry.get(skill_id)

    def save_package(self, skill_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        return self.registry.upsert(skill_id, entry)

    def remove_package(self, skill_id: str) -> Optional[Dict[str, Any]]:
        return self.registry.remove(skill_id)

    def execution_status(self, record: Dict[str, Any]) -> Dict[str, Any]:
        package = self.package(str(record.get("id") or ""))
        if not package or not (package.get("runtime") or {}).get("entrypoints"):
            return {
                "state": "no_runtime",
                "allowed": False,
                "approved_entrypoints": [],
                "detail": "该技能没有声明可执行入口，只能作为资料注入。",
            }
        if not record.get("enabled"):
            return {
                "state": "disabled",
                "allowed": False,
                "approved_entrypoints": [],
                "detail": "技能未启用，运行接口会拒绝执行。",
            }
        entrypoints = (package.get("runtime") or {}).get("entrypoints") or []
        approved = self.approvals.approved_entrypoints(
            str(record.get("id") or ""),
            str(package.get("package_hash") or ""),
            entrypoints,
        )
        if approved:
            return {
                "state": "approved",
                "allowed": True,
                "approved_entrypoints": approved,
                "detail": "已有执行授权；脚本或包变化后需要重新授权。",
            }
        return {
            "state": "pending_approval",
            "allowed": False,
            "approved_entrypoints": [],
            "detail": "首次运行需要 Boss 明确授权。",
        }

    def run(
        self,
        *,
        record: Dict[str, Any],
        entrypoint_id: str,
        params: Dict[str, Any],
        requester: str,
        employee: Optional[str] = None,
    ) -> Tuple[str, Dict[str, Any]]:
        """返回 ``(状态, 载荷)``；状态是 ``pending_approval`` 或 ``finished``。"""
        skill_id = str(record.get("id") or "")
        package = self.package(skill_id)
        if not package:
            raise SkillRunRefused("技能没有可执行包，请重新安装后再运行。")
        runtime = package.get("runtime") or {}
        entry = next(
            (item for item in (runtime.get("entrypoints") or [])
             if str(item.get("id") or "") == entrypoint_id),
            None,
        )
        if entry is None:
            raise SkillRunRefused(f"技能没有名为「{entrypoint_id}」的运行入口。")
        if not record.get("enabled"):
            raise SkillRunRefused("技能未启用，不能运行。请先在技能中心启用。")
        package_hash = str(package.get("package_hash") or "")
        approval = self.approvals.find_approved(
            skill_id,
            entrypoint_id,
            package_hash,
            str(entry.get("script_sha256") or ""),
        )
        if approval is None:
            pending = self.approvals.request(
                skill_id=skill_id,
                skill_name=str(record.get("name") or ""),
                entrypoint=entrypoint_id,
                package_hash=package_hash,
                script_sha256=str(entry.get("script_sha256") or ""),
                script=str(entry.get("script") or ""),
                interpreter=str(entry.get("interpreter") or ""),
                limits=dict(runtime.get("limits") or {}),
                network=bool(entry.get("network")),
                requester=requester,
                employee=employee,
            )
            append_audit(
                {
                    "event": "approval_requested",
                    "skill_id": skill_id,
                    "skill_name": record.get("name"),
                    "entrypoint_id": entrypoint_id,
                    "approval_id": pending.get("approval_id"),
                    "requester": requester,
                    "script": entry.get("script"),
                    "script_sha256": entry.get("script_sha256"),
                }
            )
            return "pending_approval", pending
        result = run_package(
            skill_id=skill_id,
            skill_name=str(record.get("name") or ""),
            package=package,
            entrypoint_id=entrypoint_id,
            params=params,
            requester=requester,
            employee=employee,
            approval=approval,
        )
        return "finished", result
