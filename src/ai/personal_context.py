"""KAREN — Personal Context / Personal Intelligence (MASTER-SPEC §22).

十源 DNA 中 KAREN 的落点：在「对话历史」（``conversation_store.py``）与
「个人记忆」（``knowledge/memory.py`` 与 Memory Kernel）之上，提供一层
**结构化的用户画像聚合层**，补上此前缺失的：

    User Profile / User Preferences / Behavior & Task & Relationship Context

能力对齐 §22 的五个词条：

    Personalization           — ``summarize()`` 生成可注入 system prompt 的画像摘要
    Continuity                — 复用 ConversationStore 的多轮历史（跨重启）
    Preference Awareness      — ``set_preference`` / ``get_preference`` 结构化偏好
    Contextual Suggestions    — ``suggest_context()`` 基于画像+偏好给出上下文建议
    User Assistance           — ``get_profile()`` 提供完整画像快照供上层消费

「必须通过 Memory Permission」（§22）：画像写入在结构化落盘（SQLite）的同时，
**写穿下沉到 Memory Kernel**（``PERSISTENT`` tier + ``L1`` scope + ``personal``
tag），因此画像数据自动进入 memory kernel 的 scope 隔离与 ``@kernel_action``
审计体系——而非绕过权限另存一份。下沉为尽力而为（memory kernel 不可用时不影响
画像层的结构化读写）。

存储约定对齐 ``ConversationStore`` / ``AuditStore``：SQLite + WAL + 全局单例 +
D 盘默认路径；``principal_id`` 是隔离维度（与 conversation_store 一致，支持多用户）。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..common.data_paths import resolve_store_path
from .observability import observe

logger = logging.getLogger(__name__)

# 默认落在项目根目录，而非写死某台机器的绝对路径（写死会在 CI 工作区造出
# 名为 "D:" 的目录，导致 upload-artifact 因路径含冒号失败）。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_LEGACY_DB_PATH = os.path.join(_PROJECT_ROOT, "personal_context.db")


def _default_db_path() -> str:
    """画像库落盘路径：``PERSONAL_CONTEXT_DB_PATH`` > ``LIUHAO_DATA_DIR`` > 历史默认。"""
    return resolve_store_path(
        "personal_context.db",
        _LEGACY_DB_PATH,
        env_var="PERSONAL_CONTEXT_DB_PATH",
    )


DEFAULT_DB_PATH = _default_db_path()


@dataclass
class Preference:
    """一条结构化偏好（Preference Awareness）。"""

    key: str
    value: Any
    confidence: float = 0.5   # 0.0-1.0，偏好置信度
    source: str = "explicit"  # 来源：explicit（用户明说）/ inferred（推断）
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "confidence": self.confidence,
            "source": self.source,
            "updated_at": self.updated_at,
        }


@dataclass
class UserProfile:
    """一个用户的完整画像快照（§22 User Profile）。"""

    principal_id: str
    display_name: Optional[str] = None
    preferences: Dict[str, Preference] = field(default_factory=dict)
    facts: Dict[str, Any] = field(default_factory=dict)          # 事实性画像（城市/角色/语言…）
    interests: List[str] = field(default_factory=list)
    expertise: List[str] = field(default_factory=list)
    relationships: Dict[str, str] = field(default_factory=dict)  # 关系上下文（who → relation）
    behavior_patterns: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "display_name": self.display_name,
            "preferences": {k: v.to_dict() for k, v in self.preferences.items()},
            "facts": dict(self.facts),
            "interests": list(self.interests),
            "expertise": list(self.expertise),
            "relationships": dict(self.relationships),
            "behavior_patterns": list(self.behavior_patterns),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class PersonalContextManager:
    """结构化用户画像管理器（按 principal_id 隔离）。

    持久化到 SQLite（对齐 ``ConversationStore``）；同时把偏好/事实写穿下沉到
    Memory Kernel（``personal`` tag），以满足 §22「必须通过 Memory Permission」。
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        memory_kernel: Any = None,
    ) -> None:
        self._db_path = db_path or _default_db_path()
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        # memory kernel 引用（可选注入，便于测试替换；None 则懒加载全局单例）。
        self._memory_kernel = memory_kernel
        self._init_db()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _init_db(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # 取舍：这里只存可重建的偏好上下文（置信度、来源均可重新学习），
        # 因此用 NORMAL 换取写入吞吐。进程被杀不会丢已提交数据（WAL 可恢复），
        # 但**突然断电**可能丢最近几次提交；若以后改为存不可重建的用户设置，
        # 必须提升到 FULL。
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_preferences (
                principal_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT 'explicit',
                updated_at REAL NOT NULL,
                PRIMARY KEY (principal_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_facts (
                principal_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'explicit',
                updated_at REAL NOT NULL,
                PRIMARY KEY (principal_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_lists (
                principal_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                item TEXT NOT NULL,
                PRIMARY KEY (principal_id, kind, item)
            );
            CREATE TABLE IF NOT EXISTS profile_relationships (
                principal_id TEXT NOT NULL,
                who TEXT NOT NULL,
                relation TEXT NOT NULL,
                PRIMARY KEY (principal_id, who)
            );
            CREATE TABLE IF NOT EXISTS profile_meta (
                principal_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY (principal_id, key)
            );
            CREATE TABLE IF NOT EXISTS profile_pending_facts (
                id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'assistant',
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_profile_pending_principal
                ON profile_pending_facts(principal_id, status, created_at);
            """
        )
        self._conn.commit()

    def _memory(self) -> Any:
        """懒加载全局 memory kernel（供写穿下沉）。"""
        if self._memory_kernel is None:
            from src.kernels.memory import get_memory_kernel

            self._memory_kernel = get_memory_kernel()
        return self._memory_kernel

    def _sync_to_memory(self, principal_id: str, key: str, value: Any) -> bool:
        """写穿下沉到 memory kernel，并把结果回给调用方（MEM-011）。

        写穿失败不再静默：返回 ``False`` 并记 warning，调用方可在回执/审计里
        标 ``kernel_synced=false``。画像层本身仍不阻断（SQLite 已提交），但
        "数据库有、记忆没有" 从此能在日志与返回值里被看见。
        """
        try:
            from src.kernels.memory import MemoryScope, MemoryTier

            self._memory().store(
                key=f"profile:{principal_id}:{key}",
                value=value,
                tier=MemoryTier.PERSISTENT,
                scope=MemoryScope.L1,
                tags={"personal", "profile"},
            )
            return True
        except Exception as exc:  # noqa: BLE001 - 内核不可用不能拖垮画像写入
            logger.warning(
                "profile kernel write-through failed principal=%s key=%s: %s",
                principal_id,
                key,
                exc,
                exc_info=True,
            )
            return False

    # ------------------------------------------------------------------ #
    # Preference Awareness
    # ------------------------------------------------------------------ #
    @observe("personal.set_preference")
    def set_preference(
        self,
        principal_id: str,
        key: str,
        value: Any,
        confidence: float = 0.5,
        source: str = "explicit",
    ) -> Preference:
        """设置/覆盖一条偏好。"""
        now = time.time()
        pref = Preference(key=key, value=value, confidence=confidence,
                          source=source, updated_at=now)
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO profile_preferences "
                "(principal_id, key, value_json, confidence, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (principal_id, key, json.dumps(value, ensure_ascii=False, default=str),
                 confidence, source, now),
            )
            self._conn.commit()
        self._sync_to_memory(principal_id, f"preference:{key}", value)
        return pref

    def get_preference(self, principal_id: str, key: str) -> Optional[Preference]:
        """读取一条偏好（不存在返回 None）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT key, value_json, confidence, source, updated_at "
                "FROM profile_preferences WHERE principal_id = ? AND key = ?",
                (principal_id, key),
            ).fetchone()
        if row is None:
            return None
        return Preference(
            key=row[0],
            value=json.loads(row[1]),
            confidence=row[2],
            source=row[3],
            updated_at=row[4],
        )

    def get_all_preferences(self, principal_id: str) -> Dict[str, Preference]:
        """返回该主体全部偏好（按 key 排序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json, confidence, source, updated_at "
                "FROM profile_preferences WHERE principal_id = ? ORDER BY key",
                (principal_id,),
            ).fetchall()
        return {
            r[0]: Preference(key=r[0], value=json.loads(r[1]), confidence=r[2],
                             source=r[3], updated_at=r[4])
            for r in rows
        }

    # ------------------------------------------------------------------ #
    # User Profile (facts)
    # ------------------------------------------------------------------ #
    def record_fact(
        self,
        principal_id: str,
        key: str,
        value: Any,
        source: str = "explicit",
    ) -> None:
        """记录一条事实性画像（城市/角色/语言等）。"""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO profile_facts "
                "(principal_id, key, value_json, source, updated_at) VALUES (?, ?, ?, ?, ?)",
                (principal_id, key, json.dumps(value, ensure_ascii=False, default=str),
                 source, now),
            )
            self._conn.commit()
        self._sync_to_memory(principal_id, f"fact:{key}", value)

    def get_facts(self, principal_id: str) -> Dict[str, Any]:
        """返回该主体全部事实。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM profile_facts "
                "WHERE principal_id = ? ORDER BY key",
                (principal_id,),
            ).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    def get_fact(self, principal_id: str, key: str) -> Optional[Any]:
        """读取单条事实。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM profile_facts WHERE principal_id = ? AND key = ?",
                (principal_id, key),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def delete_fact(self, principal_id: str, key: str) -> bool:
        """删除单条事实，并删穿 memory kernel 里的 PERSISTENT 副本。

        顺序：本地先 DELETE 但**不提交** → 再删穿内核 → 两边都成功才 ``commit``。
        内核拒绝（``PolicyDeferredError`` / ``PolicyDeniedError``，如武装 HIGH
        后需人工主权）时 ``rollback`` 并原样上抛，画像层保持原样——用户看到的
        是一次「需要审批 / 被拒绝」，而不是「报错但数据已经删了一半」。

        为什么选「两边都成功才提交」而不是把内核门控前移：内核策略拒绝是这条
        路径上唯一可预期的失败点，SQLite 未提交的 DELETE 天然可回滚，因此用本地
        事务包裹就能让该失败路径两侧完全一致，无需引入「补偿写回」这类会再失败
        一次的路径。残余风险只剩「内核已删、SQLite 提交失败」（如磁盘满），跨
        两种存储无法真正两阶段提交。
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM profile_facts WHERE principal_id = ? AND key = ?",
                (principal_id, key),
            )
            if not cursor.rowcount:
                # DELETE 已隐式开启事务，回滚掉以免留下空事务占用写锁。
                self._conn.rollback()
                return False
            try:
                self._delete_kernel_key(f"profile:{principal_id}:fact:{key}")
            except Exception:
                self._conn.rollback()
                raise
            self._conn.commit()
            return True

    def delete_preference(self, principal_id: str, key: str) -> bool:
        """删除单条偏好，并删穿 memory kernel 里的 PERSISTENT 副本。

        与 :meth:`delete_fact` 相同的两侧一致策略：两边都成功才提交。
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM profile_preferences WHERE principal_id = ? AND key = ?",
                (principal_id, key),
            )
            if not cursor.rowcount:
                self._conn.rollback()
                return False
            try:
                self._delete_kernel_key(f"profile:{principal_id}:preference:{key}")
            except Exception:
                self._conn.rollback()
                raise
            self._conn.commit()
            return True

    def _delete_kernel_key(self, key: str) -> int:
        """精确删穿内核单条 key（删除单条事实/偏好必须用它）。

        前缀删会把同前缀的兄弟 key 一起删掉（``...:fact:city`` 命中
        ``...:fact:city_code``），对用户是静默丢数据，所以单条删除走精确匹配。
        """
        from src.kernels._crosscutting import PolicyDeniedError

        try:
            return int(self._memory().delete_by_key(key))
        except PolicyDeniedError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"内核记忆删除失败：{exc}") from exc

    def _delete_kernel_prefix(self, prefix: str) -> int:
        """删穿内核；失败抛错（调用方必须把删除失败如实告诉用户）。

        与 :meth:`_delete_kernel_key` 同一口径：策略类异常原样上抛，让网关按类型
        映射成 409（需人工审批）或 403（被拒绝），而不是被包成 ``RuntimeError``
        落进 500。:meth:`clear` 已保证「内核成功才提交 SQLite」，因此这两类拒绝
        到达用户时两侧数据都保持原样。
        """
        from src.kernels._crosscutting import PolicyDeniedError

        try:
            return int(self._memory().delete_by_key_prefix(prefix))
        except PolicyDeniedError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"内核记忆删除失败：{exc}") from exc

    # ------------------------------------------------------------------ #
    # Pending facts（模型写入长期事实必须先待确认）
    # ------------------------------------------------------------------ #
    def propose_fact(
        self,
        principal_id: str,
        key: str,
        value: Any,
        source: str = "assistant",
    ) -> Dict[str, Any]:
        """把模型提议的事实写进待审区，不直接进入长期记忆。"""
        pending_id = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO profile_pending_facts "
                "(id, principal_id, key, value_json, source, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (
                    pending_id,
                    principal_id,
                    str(key),
                    json.dumps(value, ensure_ascii=False, default=str),
                    source,
                    now,
                ),
            )
            self._conn.commit()
        return {
            "id": pending_id,
            "key": str(key),
            "value": value,
            "status": "pending",
            "created_at": now,
        }

    def list_pending_facts(
        self, principal_id: str, status: str = "pending"
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, key, value_json, source, created_at, status "
                "FROM profile_pending_facts "
                "WHERE principal_id = ? AND status = ? ORDER BY created_at",
                (principal_id, status),
            ).fetchall()
        return [
            {
                "id": row[0],
                "key": row[1],
                "value": json.loads(row[2]),
                "source": row[3],
                "created_at": row[4],
                "status": row[5],
            }
            for row in rows
        ]

    def _resolve_pending(self, principal_id: str, pending_id: str, status: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, key, value_json, status FROM profile_pending_facts "
                "WHERE id = ? AND principal_id = ?",
                (pending_id, principal_id),
            ).fetchone()
            if row is None or row[3] != "pending":
                return None
            self._conn.execute(
                "UPDATE profile_pending_facts SET status = ? WHERE id = ?",
                (status, pending_id),
            )
            self._conn.commit()
        return {"id": row[0], "key": row[1], "value": json.loads(row[2])}

    def approve_pending_fact(self, principal_id: str, pending_id: str) -> Optional[Dict[str, Any]]:
        """用户确认后才真正写入长期事实（来源标记 confirmed）。"""
        item = self._resolve_pending(principal_id, pending_id, "approved")
        if item is None:
            return None
        self.record_fact(principal_id, item["key"], item["value"], source="confirmed")
        return {**item, "status": "approved"}

    def reject_pending_fact(self, principal_id: str, pending_id: str) -> Optional[Dict[str, Any]]:
        item = self._resolve_pending(principal_id, pending_id, "rejected")
        if item is None:
            return None
        return {**item, "status": "rejected"}

    # ------------------------------------------------------------------ #
    # Interests / Expertise / Relationships / Behavior
    # ------------------------------------------------------------------ #
    def add_interest(self, principal_id: str, item: str) -> None:
        self._add_list_item(principal_id, "interest", item)

    def add_expertise(self, principal_id: str, item: str) -> None:
        self._add_list_item(principal_id, "expertise", item)

    def add_behavior_pattern(self, principal_id: str, item: str) -> None:
        self._add_list_item(principal_id, "behavior", item)

    def _add_list_item(self, principal_id: str, kind: str, item: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO profile_lists (principal_id, kind, item) VALUES (?, ?, ?)",
                (principal_id, kind, item),
            )
            self._conn.commit()

    def _list_items(self, principal_id: str, kind: str) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT item FROM profile_lists WHERE principal_id = ? AND kind = ? "
                "ORDER BY item",
                (principal_id, kind),
            ).fetchall()
        return [r[0] for r in rows]

    def add_relationship(self, principal_id: str, who: str, relation: str) -> None:
        """记录一条关系上下文（who → relation）。"""
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO profile_relationships "
                "(principal_id, who, relation) VALUES (?, ?, ?)",
                (principal_id, who, relation),
            )
            self._conn.commit()

    def _relationships(self, principal_id: str) -> Dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT who, relation FROM profile_relationships "
                "WHERE principal_id = ? ORDER BY who",
                (principal_id,),
            ).fetchall()
        return {r[0]: r[1] for r in rows}

    def set_display_name(self, principal_id: str, name: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO profile_meta (principal_id, key, value_json) "
                "VALUES (?, ?, ?)",
                (principal_id, "display_name", json.dumps(name, ensure_ascii=False)),
            )
            self._conn.commit()

    def _display_name(self, principal_id: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM profile_meta WHERE principal_id = ? AND key = 'display_name'",
                (principal_id,),
            ).fetchone()
        return json.loads(row[0]) if row is not None else None

    # ------------------------------------------------------------------ #
    # 聚合：Profile / Summarize / Suggest
    # ------------------------------------------------------------------ #
    @observe("personal.get_profile")
    def get_profile(self, principal_id: str) -> UserProfile:
        """返回该主体的完整画像快照。"""
        return UserProfile(
            principal_id=principal_id,
            display_name=self._display_name(principal_id),
            preferences=self.get_all_preferences(principal_id),
            facts=self.get_facts(principal_id),
            interests=self._list_items(principal_id, "interest"),
            expertise=self._list_items(principal_id, "expertise"),
            relationships=self._relationships(principal_id),
            behavior_patterns=self._list_items(principal_id, "behavior"),
        )

    def has_profile(self, principal_id: str) -> bool:
        """该主体是否已存在任何画像内容。

        供调用方（如对话链路注入 system prompt）判断「要不要加画像段」——
        ``summarize()`` 对空画像也会返回兜底说明串，不能直接当判据。
        """
        profile = self.get_profile(principal_id)
        return bool(
            profile.display_name
            or profile.facts
            or profile.preferences
            or profile.interests
            or profile.expertise
            or profile.relationships
            or profile.behavior_patterns
        )

    def summarize(self, principal_id: str, max_items: int = 8) -> str:
        """生成画像摘要（Personalization —— 可注入 system prompt）。

        按「称呼 → 背景 → 偏好 → 专长/关注 → 关系」组织，偏好按置信度降序取前
        ``max_items``。
        """
        profile = self.get_profile(principal_id)
        parts: List[str] = []

        if profile.display_name:
            parts.append(f"称呼：{profile.display_name}")

        if profile.facts:
            facts = "；".join(
                f"{k}={v}" for k, v in list(profile.facts.items())[:max_items]
            )
            parts.append(f"背景：{facts}")

        if profile.preferences:
            prefs = sorted(
                profile.preferences.values(),
                key=lambda p: (-p.confidence, p.key),
            )[:max_items]
            parts.append("偏好：" + "；".join(f"{p.key}={p.value}" for p in prefs))

        tags: List[str] = []
        if profile.expertise:
            tags.append("专长：" + "、".join(profile.expertise[:max_items]))
        if profile.interests:
            tags.append("关注：" + "、".join(profile.interests[:max_items]))
        if tags:
            parts.append("；".join(tags))

        if profile.relationships:
            rels = "；".join(
                f"{w}:{r}" for w, r in list(profile.relationships.items())[:max_items]
            )
            parts.append(f"关系：{rels}")

        if not parts:
            return f"（{principal_id} 暂无个人画像）"
        return "；".join(parts)

    def suggest_context(self, principal_id: str, query: str, limit: int = 5) -> List[str]:
        """Contextual Suggestions —— 基于画像与偏好给出上下文建议。

        简单诚实的实现：把 query 与画像中的偏好键/值、事实键/值、兴趣、专长做
        子串匹配，返回命中的上下文提示（不伪装成 LLM 推理）。
        """
        profile = self.get_profile(principal_id)
        q = query.lower()
        suggestions: List[str] = []

        for p in profile.preferences.values():
            if q in str(p.key).lower() or q in str(p.value).lower():
                suggestions.append(f"偏好 {p.key}={p.value}")

        for k, v in profile.facts.items():
            if q in str(k).lower() or q in str(v).lower():
                suggestions.append(f"背景 {k}={v}")

        for item in profile.interests:
            if q in item.lower():
                suggestions.append(f"关注领域 {item}")

        for item in profile.expertise:
            if q in item.lower():
                suggestions.append(f"专长 {item}")

        return suggestions[:limit]

    # ------------------------------------------------------------------ #
    # 维护
    # ------------------------------------------------------------------ #
    def clear(self, principal_id: str) -> int:
        """清空该主体的画像，并同步删穿 memory kernel，返回内核删除条数。

        MEM-002：只清 SQLite 会让 PERSISTENT 副本继续被 ``search_memory``
        检索到（用户以为删了、其实没删）。这里先做内核删穿，再提交 SQLite；
        任一失败都回滚 SQLite 并抛错，绝不“假成功”。
        """
        kernel_deleted = 0
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM profile_preferences WHERE principal_id = ?", (principal_id,)
                )
                self._conn.execute(
                    "DELETE FROM profile_facts WHERE principal_id = ?", (principal_id,)
                )
                self._conn.execute(
                    "DELETE FROM profile_lists WHERE principal_id = ?", (principal_id,)
                )
                self._conn.execute(
                    "DELETE FROM profile_relationships WHERE principal_id = ?", (principal_id,)
                )
                self._conn.execute(
                    "DELETE FROM profile_meta WHERE principal_id = ?", (principal_id,)
                )
                self._conn.execute(
                    "DELETE FROM profile_pending_facts WHERE principal_id = ?",
                    (principal_id,),
                )
                kernel_deleted = self._delete_kernel_prefix(f"profile:{principal_id}:")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return kernel_deleted

    def stats(self, principal_id: Optional[str] = None) -> Dict[str, Any]:
        """返回画像存储统计（给定 principal 时只看该主体）。"""
        with self._lock:
            if principal_id is not None:
                n_prefs = self._conn.execute(
                    "SELECT COUNT(*) FROM profile_preferences WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()[0]
                n_facts = self._conn.execute(
                    "SELECT COUNT(*) FROM profile_facts WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()[0]
                n_lists = self._conn.execute(
                    "SELECT COUNT(*) FROM profile_lists WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()[0]
                n_rels = self._conn.execute(
                    "SELECT COUNT(*) FROM profile_relationships WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()[0]
            else:
                n_prefs = self._conn.execute("SELECT COUNT(*) FROM profile_preferences").fetchone()[0]
                n_facts = self._conn.execute("SELECT COUNT(*) FROM profile_facts").fetchone()[0]
                n_lists = self._conn.execute("SELECT COUNT(*) FROM profile_lists").fetchone()[0]
                n_rels = self._conn.execute("SELECT COUNT(*) FROM profile_relationships").fetchone()[0]
        return {
            "preferences": n_prefs,
            "facts": n_facts,
            "list_items": n_lists,
            "relationships": n_rels,
        }


# ---------------------------------------------------------------------- #
# 全局单例
# ---------------------------------------------------------------------- #
_personal_context: Optional[PersonalContextManager] = None


@observe("personal.get_personal_context")
def get_personal_context() -> PersonalContextManager:
    """获取或创建全局个人画像管理器实例。"""
    global _personal_context
    if _personal_context is None:
        _personal_context = PersonalContextManager()
    return _personal_context
