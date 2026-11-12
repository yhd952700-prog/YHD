"""员工 ↔ 技能的真实绑定表（``liuhao_skill_bindings.json``）。

为什么单独一张表
----------------
过去"这个技能被谁用"是拿员工 ``default_capabilities`` 与技能声明的能力求
交集**推导**出来的。推导会把"员工有 file_write 能力"误读成"他在用这个技能"，
既不能证明绑定关系，也无法记录是谁、什么时候绑的。

本模块提供真实的多对多绑定：每条记录 ``(employee, skill_id, created_at,
created_by)``。读取失败时**不做空表假象**（``strict=True`` 直接抛错），因为
把"读不出来"当成"没人用"会误删正在被员工使用的技能。

存储约定与 ``SkillStore`` 一致：``resolve_store_path`` + 原子写 + 进程内写锁。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

_PROCESS_WRITE_LOCK = threading.Lock()
_READ_ATTEMPTS = 3

_EMPLOYEE_LIMIT = 120
_ACTOR_LIMIT = 120


class SkillBindingError(RuntimeError):
    """绑定表读/写失败：调用方必须当成"本次操作没有落盘"。"""


class SkillBindingConflict(ValueError):
    """绑定请求本身不合法（例如员工不存在）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clean_name(value: Any, label: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise SkillBindingConflict(f"{label}不能为空。")
    if len(text) > limit:
        raise SkillBindingConflict(f"{label}最多 {limit} 个字符。")
    return text


class SkillBindingStore:
    """绑定表读写。实例很轻，每个请求 new 一个即可。"""

    VERSION = 1

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or self._default_store_path()
        self._lock = threading.RLock()

    @staticmethod
    def _default_store_path() -> str:
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        return resolve_store_path(
            "liuhao_skill_bindings.json",
            os.path.join(repo_root, ".liuhao_skill_bindings.json"),
            workspace=True,
        )

    # ------------------------------------------------------------------ #
    # Low-level IO
    # ------------------------------------------------------------------ #
    def _empty_document(self) -> Dict[str, Any]:
        return {"version": self.VERSION, "bindings": []}

    def _load_document(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return self._empty_document()
        last_error: Optional[BaseException] = None
        for _ in range(_READ_ATTEMPTS):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                if not isinstance(raw, dict) or not isinstance(
                    raw.get("bindings"), list
                ):
                    raise SkillBindingError(
                        f"技能绑定表格式不可识别：{self.path}"
                        "（期望 version/bindings 结构）。"
                    )
                return raw
            except (OSError, json.JSONDecodeError) as exc:  # 瞬时占用 / 半截文件
                last_error = exc
                continue
        raise SkillBindingError(
            f"技能绑定表读取失败：{self.path}（{last_error}）。本次操作未执行。"
        )

    def _write_document(self, document: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        document["version"] = self.VERSION
        document["updated_at"] = _now()
        payload = json.dumps(document, ensure_ascii=False, indent=2)
        handle = None
        tmp_path = ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                prefix=".skill-bindings-", suffix=".tmp", dir=directory
            )
            handle = os.fdopen(fd, "w", encoding="utf-8")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(tmp_path, self.path)
            tmp_path = ""
        except OSError as exc:
            raise SkillBindingError(
                f"技能绑定表写入失败：{self.path}（{exc}）。本次修改没有落盘。"
            ) from exc
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

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def list_all(self) -> List[Dict[str, Any]]:
        document = self._load_document()
        rows = [row for row in document["bindings"] if isinstance(row, dict)]
        rows.sort(key=lambda row: str(row.get("created_at") or ""))
        return rows

    def employees_for_skill(self, skill_id: str) -> List[str]:
        names: List[str] = []
        for row in self.list_all():
            if str(row.get("skill_id") or "") != skill_id:
                continue
            employee = str(row.get("employee") or "").strip()
            if employee and employee not in names:
                names.append(employee)
        return sorted(names)

    def skills_for_employee(self, employee: str) -> List[str]:
        target = str(employee or "").strip().casefold()
        if not target:
            return []
        ids: List[str] = []
        for row in self.list_all():
            if str(row.get("employee") or "").strip().casefold() != target:
                continue
            skill_id = str(row.get("skill_id") or "").strip()
            if skill_id and skill_id not in ids:
                ids.append(skill_id)
        return ids

    def binding(self, skill_id: str, employee: str) -> Optional[Dict[str, Any]]:
        target = str(employee or "").strip().casefold()
        for row in self.list_all():
            if (
                str(row.get("skill_id") or "") == skill_id
                and str(row.get("employee") or "").strip().casefold() == target
            ):
                return row
        return None

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def bind(
        self, *, skill_id: str, employee: Any, actor: str = ""
    ) -> Dict[str, Any]:
        """幂等绑定：已存在时原样返回旧记录（不改 created_at/created_by）。"""
        clean_skill = _clean_name(skill_id, "技能 id", 128)
        clean_employee = _clean_name(employee, "员工名", _EMPLOYEE_LIMIT)
        clean_actor = str(actor or "").strip()[:_ACTOR_LIMIT]
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                for row in document["bindings"]:
                    if not isinstance(row, dict):
                        continue
                    if (
                        str(row.get("skill_id") or "") == clean_skill
                        and str(row.get("employee") or "").strip().casefold()
                        == clean_employee.casefold()
                    ):
                        return dict(row)
                record = {
                    "id": f"bind_{uuid.uuid4().hex[:12]}",
                    "skill_id": clean_skill,
                    "employee": clean_employee,
                    "created_at": _now(),
                    "created_by": clean_actor or "unknown",
                }
                document["bindings"].append(record)
                self._write_document(document)
                return dict(record)

    def unbind(self, *, skill_id: str, employee: str) -> Optional[Dict[str, Any]]:
        target = str(employee or "").strip().casefold()
        if not target:
            return None
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                kept: List[Dict[str, Any]] = []
                removed: Optional[Dict[str, Any]] = None
                for row in document["bindings"]:
                    if not isinstance(row, dict):
                        continue
                    if (
                        str(row.get("skill_id") or "") == skill_id
                        and str(row.get("employee") or "").strip().casefold() == target
                    ):
                        removed = dict(row)
                        continue
                    kept.append(row)
                if removed is None:
                    return None
                document["bindings"] = kept
                self._write_document(document)
                return removed

    def remove_skill(self, skill_id: str) -> int:
        """技能被删除时清掉它的全部绑定，返回清掉的条数。"""
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                kept = [
                    row
                    for row in document["bindings"]
                    if isinstance(row, dict)
                    and str(row.get("skill_id") or "") != skill_id
                ]
                removed = len(document["bindings"]) - len(kept)
                if removed:
                    document["bindings"] = kept
                    self._write_document(document)
                return removed

    def rename_employee(self, old_name: str, new_name: str) -> int:
        """员工改名后同步绑定，避免绑定指向一个不存在的名字。"""
        old = str(old_name or "").strip()
        new = str(new_name or "").strip()
        if not old or not new or old == new:
            return 0
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                changed = 0
                for row in document["bindings"]:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("employee") or "").strip().casefold() == old.casefold():
                        row["employee"] = new
                        row["updated_at"] = _now()
                        changed += 1
                if changed:
                    self._write_document(document)
                return changed
