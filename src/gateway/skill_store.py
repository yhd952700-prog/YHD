"""技能注册表的落盘实现（JSON + 原子写）。

诚实边界
--------
* 技能是**产品面登记实体**，不是内核插件：插件由 importlib 导入真实 Python
  模块（见 ``src/kernels/plugin``），无法由用户在界面上"填个名字"创建。技能
  记录只声明自己需要哪些**既有**执行器能力（``capabilities``），由
  ``src/gateway/skills.py`` 在读取时与真实能力目录、员工默认能力做核对。
* 本模块只负责"存得下、读得回、不写坏"；风险判定、占用判定一律不在这里，
  避免把同一条规则写两遍后互相漂移。

存储约定与 ``EmployeeStore`` 一致：路径经 ``resolve_store_path`` 解析（优先
``LIUHAO_WORKSPACE_ROOT``，其次 ``LIUHAO_DATA_DIR``，最后仓库根），写入先写
同目录临时文件再 ``os.replace``，读不出来时**不做空库假象**（``strict=True``
直接抛 ``SkillStoreError``）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

#: 同进程内所有 ``SkillStore`` 实例共享的写锁（每个请求都 new 一个 store）。
_PROCESS_WRITE_LOCK = threading.Lock()

#: Windows 上 ``os.replace`` 与读取瞬时冲突时的短重试次数。
_READ_ATTEMPTS = 3

#: 技能名与说明的长度上限（防止有人把整篇文档塞进名字里）。
_NAME_LIMIT = 80
_DESCRIPTION_LIMIT = 2000

#: 一条记录最多声明多少能力（够用即可，超出多半是误用）。
_CAPABILITY_LIMIT = 50

#: 导入报告最多保留多少条（防止一份超大 frontmatter 把记录撑爆）。
_IMPORT_REPORT_LIMIT = 50

_ALLOWED_SOURCES = ("user", "imported", "learned")

_WS_RE = re.compile(r"\s+")


class SkillStoreError(RuntimeError):
    """技能库读/写失败：调用方必须当成"本次操作没有落盘"，不能返回成功。"""


class SkillNotFound(LookupError):
    """技能 id 不存在。"""


class SkillConflict(ValueError):
    """与既有记录冲突（例如同名技能已存在）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_name(value: Any) -> str:
    """收敛技能名：去首尾空白、合并连续空白，并校验长度。"""
    name = _WS_RE.sub(" ", str(value or "")).strip()
    if not name:
        raise ValueError("技能名称不能为空。")
    if len(name) > _NAME_LIMIT:
        raise ValueError(f"技能名称最多 {_NAME_LIMIT} 个字符。")
    return name


def normalize_description(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > _DESCRIPTION_LIMIT:
        raise ValueError(f"技能说明最多 {_DESCRIPTION_LIMIT} 个字符。")
    return text


def normalize_capabilities(values: Any) -> List[str]:
    """能力 id 列表：去空、去重、保序，超过上限直接报错而不是悄悄截断。"""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise ValueError("capabilities 必须是字符串列表。")
    cleaned: List[str] = []
    for item in values:
        cap = str(item or "").strip()
        if not cap or cap in cleaned:
            continue
        cleaned.append(cap)
    if len(cleaned) > _CAPABILITY_LIMIT:
        raise ValueError(f"单个技能最多声明 {_CAPABILITY_LIMIT} 项能力。")
    return cleaned


def _normalize_str_list(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []
    out: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def normalize_dependencies(values: Any) -> List[Dict[str, str]]:
    """收敛技能的依赖声明（V7 包格式）：``[{name, version}]``，形状不对的条目丢弃。

    解析期的严格校验在 ``skill_import.normalize_dependencies``；这里是落盘前
    的最后一道形状收敛，保证注册表里永远只有干净结构。
    """
    if not isinstance(values, (list, tuple)):
        return []
    out: List[Dict[str, str]] = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(
            {"name": name, "version": str(item.get("version") or "").strip()}
        )
    return out[:20]


class SkillStore:
    """技能清单的读写。实例很轻，每个请求 new 一个即可（无进程级缓存）。"""

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
            "liuhao_skills.json",
            os.path.join(repo_root, ".liuhao_skills.json"),
            workspace=True,
        )

    # ------------------------------------------------------------------ #
    # Low-level IO
    # ------------------------------------------------------------------ #
    def _empty_document(self) -> Dict[str, Any]:
        return {"version": self.VERSION, "skills": {}}

    def _load_document(self, *, strict: bool = True) -> Dict[str, Any]:
        """读取整份文档；读不懂就抛错，绝不返回"空库"假装没数据。"""
        if not os.path.exists(self.path):
            return self._empty_document()
        last_error: Optional[BaseException] = None
        for _ in range(_READ_ATTEMPTS):
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                if not isinstance(raw, dict) or not isinstance(
                    raw.get("skills"), dict
                ):
                    raise SkillStoreError(
                        f"技能库格式不可识别：{self.path}（期望 version/skills 结构）。"
                    )
                return raw
            except (OSError, json.JSONDecodeError) as exc:  # 瞬时占用 / 半截文件
                last_error = exc
                continue
        raise SkillStoreError(
            f"技能库读取失败：{self.path}（{last_error}）。本次操作未执行。"
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
                prefix=".skills-", suffix=".tmp", dir=directory
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
            raise SkillStoreError(
                f"技能库写入失败：{self.path}（{exc}）。本次修改没有落盘。"
            ) from exc
        finally:
            if handle is not None:
                try:
                    handle.close()
                except OSError:  # pragma: no cover - 关闭失败无处可退
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:  # pragma: no cover
                    pass

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    def list_records(self) -> List[Dict[str, Any]]:
        document = self._load_document()
        records = [rec for rec in document["skills"].values() if isinstance(rec, dict)]
        records.sort(key=lambda rec: str(rec.get("created_at") or ""))
        return records

    def get(self, skill_id: str) -> Dict[str, Any]:
        document = self._load_document()
        record = document["skills"].get(skill_id)
        if not isinstance(record, dict):
            raise SkillNotFound(skill_id)
        return record

    def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        target = normalize_name(name).casefold()
        for record in self.list_records():
            if str(record.get("name") or "").casefold() == target:
                return record
        return None

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def create(
        self,
        *,
        name: Any,
        description: Any = "",
        source: str = "user",
        version: Any = "",
        author: Any = "",
        dependencies: Any = None,
        capabilities: Any = None,
        declared_permissions: Iterable[str] = (),
        declared_risk: Any = "",
        content: Optional[str] = None,
        origin: Optional[str] = None,
        # 技能页透明化 §5.4：新建技能默认**关**（安装后由人显式启用）；
        # 与 _install_parsed(enabled=False) 一致。显式传 enabled=True 的
        # 调用方（测试）不受影响。
        enabled: bool = False,
        fmt: str = "manual",
        license: Any = "",
        allowed_tools: Any = None,
        metadata: Any = None,
        raw_frontmatter: Any = None,
        has_scripts: bool = False,
        script_refs: Any = None,
        files: Any = None,
        name_source: str = "",
        import_report: Any = None,
        execution_allowed: bool = False,
    ) -> Dict[str, Any]:
        if source not in _ALLOWED_SOURCES:
            raise ValueError(f"未知来源：{source}")
        clean_name = normalize_name(name)
        clean_description = normalize_description(description)
        clean_caps = normalize_capabilities(capabilities)
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                for record in document["skills"].values():
                    if not isinstance(record, dict):
                        continue
                    if str(record.get("name") or "").casefold() == clean_name.casefold():
                        raise SkillConflict(
                            f"已经存在同名技能「{record.get('name')}」，请换个名字或直接编辑它。"
                        )
                stamp = _now()
                skill_id = f"skill_{uuid.uuid4().hex[:12]}"
                report_rows = import_report if isinstance(import_report, list) else []
                record = {
                    "id": skill_id,
                    "name": clean_name,
                    "description": clean_description,
                    "source": source,
                    "version": str(version or "").strip(),
                    "author": str(author or "").strip()[:80],
                    "dependencies": normalize_dependencies(dependencies),
                    "capabilities": clean_caps,
                    "declared_permissions": _normalize_str_list(declared_permissions),
                    "declared_risk": str(declared_risk or "").strip().lower(),
                    "content": content,
                    "origin": origin,
                    "enabled": bool(enabled),
                    "created_at": stamp,
                    "updated_at": stamp,
                    # ---- 兼容层元数据（旧记录没有这些键，读取时按默认值兜底）----
                    "format": str(fmt or "manual"),
                    "license": str(license or "").strip(),
                    "allowed_tools": _normalize_str_list(allowed_tools),
                    "metadata": metadata if isinstance(metadata, dict) else {},
                    "raw_frontmatter": (
                        raw_frontmatter if isinstance(raw_frontmatter, dict) else {}
                    ),
                    "has_scripts": bool(has_scripts),
                    "script_refs": _normalize_str_list(script_refs),
                    "files": _normalize_str_list(files),
                    "name_source": str(name_source or ""),
                    "import_report": [
                        row for row in report_rows[:_IMPORT_REPORT_LIMIT]
                        if isinstance(row, dict)
                    ],
                    # 拍板结论：本版任何技能都不执行脚本，这个字段恒为 False。
                    "execution_allowed": False,
                }
                document["skills"][skill_id] = record
                self._write_document(document)
                return dict(record)

    def update(
        self,
        skill_id: str,
        *,
        name: Any = None,
        description: Any = None,
    ) -> Dict[str, Any]:
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                record = document["skills"].get(skill_id)
                if not isinstance(record, dict):
                    raise SkillNotFound(skill_id)
                if name is not None:
                    clean_name = normalize_name(name)
                    for other_id, other in document["skills"].items():
                        if other_id == skill_id or not isinstance(other, dict):
                            continue
                        if (
                            str(other.get("name") or "").casefold()
                            == clean_name.casefold()
                        ):
                            raise SkillConflict(
                                f"已经存在同名技能「{other.get('name')}」，请换个名字。"
                            )
                    record["name"] = clean_name
                if description is not None:
                    record["description"] = normalize_description(description)
                record["updated_at"] = _now()
                document["skills"][skill_id] = record
                self._write_document(document)
                return dict(record)

    def update_package_fields(
        self,
        skill_id: str,
        *,
        version: Any = None,
        author: Any = None,
        dependencies: Any = None,
        description: Any = None,
    ) -> Dict[str, Any]:
        """升级后同步包级字段（版本/作者/依赖/说明）。

        升级换的是包内容，注册表里的版本与依赖声明必须跟着换，否则列表里
        显示的还是旧版本、依赖核对也会对着旧声明做。只动这四个字段，
        名字/开关/绑定一律不碰。
        """
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                record = document["skills"].get(skill_id)
                if not isinstance(record, dict):
                    raise SkillNotFound(skill_id)
                if version is not None:
                    record["version"] = str(version or "").strip()
                if author is not None:
                    record["author"] = str(author or "").strip()[:80]
                if dependencies is not None:
                    record["dependencies"] = normalize_dependencies(dependencies)
                if description is not None:
                    record["description"] = normalize_description(description)
                record["updated_at"] = _now()
                document["skills"][skill_id] = record
                self._write_document(document)
                return dict(record)

    def set_metadata(self, skill_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """合并更新一条技能的 metadata（学习循环改审核状态用）。

        只动 metadata 一个键，不碰名字/开关/内容；key 必须是非空字符串。
        """
        if not isinstance(updates, dict):
            raise ValueError("metadata 更新必须是 dict")
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                record = document["skills"].get(skill_id)
                if not isinstance(record, dict):
                    raise SkillNotFound(skill_id)
                metadata = record.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                for key, value in updates.items():
                    if not isinstance(key, str) or not key.strip():
                        raise ValueError("metadata 的 key 必须是非空字符串")
                    metadata[key] = value
                record["metadata"] = metadata
                record["updated_at"] = _now()
                document["skills"][skill_id] = record
                self._write_document(document)
                return dict(record)

    def delete(self, skill_id: str) -> Dict[str, Any]:
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                record = document["skills"].get(skill_id)
                if not isinstance(record, dict):
                    raise SkillNotFound(skill_id)
                del document["skills"][skill_id]
                self._write_document(document)
                return dict(record)

    def set_enabled(self, skill_id: str, enabled: bool) -> Dict[str, Any]:
        with _PROCESS_WRITE_LOCK:
            with self._lock:
                document = self._load_document()
                record = document["skills"].get(skill_id)
                if not isinstance(record, dict):
                    raise SkillNotFound(skill_id)
                record["enabled"] = bool(enabled)
                record["updated_at"] = _now()
                document["skills"][skill_id] = record
                self._write_document(document)
                return dict(record)
