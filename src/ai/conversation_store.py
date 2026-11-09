"""会话历史持久化 — 跨进程重启恢复对话上下文。

为什么需要它：memory kernel 是进程内内存态（重启即失），audit kernel 虽 SQLite
落盘但 details 里只存元数据（chars_in/chars_out）不存对话文本。因此"鎏灏重启后
还记得上一段对话"这件事此前不成立。

本模块提供一个最小、诚实的会话存储：把「谁(principal)在第几轮(turn)说了什么
(role/content)」落盘，让 ``LiuHaoAssistant`` 重启后能恢复多轮上下文。

设计对齐 audit kernel 的 ``AuditStore``：SQLite + WAL + 全局单例 + D 盘默认路径，
不另起一套存储约定。principal 即隔离维度，天然支持后续多用户/session 隔离。
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.atomic_write import describe_storage_failure, is_database_busy
from ..common.data_paths import resolve_store_path
from .observability import observe
from .audit import audited
from .context_budget import SUMMARY_MAX_CHARS, deterministic_summary

logger = logging.getLogger(__name__)


class ConversationStoreError(RuntimeError):
    """会话历史读写失败（磁盘满 / 只读 / 权限 / 被占用）的中文可行动错误。

    不继承 ``sqlite3.Error``：调用方（对话入口）只需捕获这一个类型，就能把
    "历史没保存"翻译成 503 中文提示，而不是把英文 ``sqlite3.OperationalError``
    直接抛给用户。
    """

# 默认落在项目根目录，而不是写死某一台机器的绝对路径：保留"数据不入库内"
# 的约定，同时保证在任何机器上 checkout 后都能直接运行（写死路径会在 CI 的
# 工作区里造出名为 "D:" 的目录，upload-artifact 因路径含冒号而失败）。
_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# 断电级持久化：见 memory/store.py 的说明。会话追加同样低频，默认 FULL，
# 保证已经返回成功的轮次在断电后仍可恢复；可用环境变量降级。
_SYNC_LEVELS = {"OFF", "NORMAL", "FULL", "EXTRA"}
_CONVERSATION_SYNCHRONOUS = (
    os.environ.get("LIUHAO_CONVERSATION_SYNCHRONOUS") or "FULL"
).strip().upper()
if _CONVERSATION_SYNCHRONOUS not in _SYNC_LEVELS:
    _CONVERSATION_SYNCHRONOUS = "FULL"
_LEGACY_DB_PATH = os.path.join(_PROJECT_ROOT, "conversation_store.db")

# 并发写入：读-改-写冲突是「数据库正忙」的瞬时状态，必须显式等待而不是
# 依赖 sqlite3.connect 的隐式 5s；再叠加有界重试覆盖备份/导入等长写者。
_CONVERSATION_BUSY_TIMEOUT_MS = int(
    os.environ.get("LIUHAO_CONVERSATION_BUSY_TIMEOUT_MS") or "15000"
)
_CONVERSATION_LOCK_RETRY_ATTEMPTS = 3
_CONVERSATION_LOCK_RETRY_BASE_SECONDS = 1.5


def _default_db_path() -> str:
    """会话库落盘路径：``CONVERSATION_DB_PATH`` > ``LIUHAO_DATA_DIR`` > 历史默认。

    必须按调用时求值：测试/隔离脚本常常在模块导入之后才设置环境变量，若在
    导入期算成常量，再设 ``LIUHAO_DATA_DIR`` 也回不到数据目录。
    """
    return resolve_store_path(
        "conversation_store.db",
        _LEGACY_DB_PATH,
        env_var="CONVERSATION_DB_PATH",
    )


DEFAULT_DB_PATH = _default_db_path()

# ─── 全文搜索（FTS5）────────────────────────────────────────────
#
# 为什么不是逐行 LIKE：50 万行时全表 LIKE 实测 613ms，且随行数线性增长。
# 为什么不是 trigram：实测 trigram 对 2 字中文（"合同"）召回为 0（它要求查询
# 至少 3 个字符），而 2 字关键词是中文最常用的检索单位。
#
# 采用 **CJK 双字（bigram）+ 拉丁词** 的索引文本，FTS5 用 unicode61 分词：
# * 中文 run "签合同预算" → token "签合 合同 同预 预算"，查询 "合同" 是一个
#   精确 token，"预算客户" 变成相邻 bigram 短语，可命中任意长度的中文子串；
# * 拉丁词原样保留（"budget" 仍是一个 token，符合英文检索直觉）；
# * 单字中文（"钱"）在 bigram 索引里没有对应 token，明确回退逐行扫描并提示
#   "搜索变慢"，而不是静默返回空结果。
# * 索引表用 ``content=''`` + ``contentless_delete=1``：只存倒排表不存原文，
#   50k 行实测索引 22MB（存原文的版本是 36MB+），且支持按 rowid 删除。
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_FTS_TOKEN_RE = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+|[A-Za-z0-9_]+"
)

#: 建索引按块推进：单次请求/单次后台循环最多处理这么多行，
#: 让首次搜索能明确汇报进度而不是"卡住不动"。
SEARCH_INDEX_CHUNK = 20000
#: 搜索降级（FTS5 不可用 / 单字查询）时的固定提示，界面必须原样展示。
SCAN_DEGRADED_NOTICE = (
    "当前环境不支持全文索引，搜索改为逐行扫描，数据量大时会明显变慢。"
)
SHORT_QUERY_NOTICE = "单字查询用不了索引，改为逐行扫描，会明显变慢。"


def _fts_index_text(text: str) -> str:
    """把原文转成 FTS5 索引文本（CJK 双字 + 拉丁词，小写化）。"""
    parts: List[str] = []
    for match in _FTS_TOKEN_RE.finditer(text or ""):
        run = match.group(0)
        if _CJK_CHAR_RE.match(run):
            if len(run) == 1:
                parts.append(run)
            else:
                parts.append(" ".join(run[i:i + 2] for i in range(len(run) - 1)))
        else:
            parts.append(run.lower())
    return " ".join(parts)


def _fts_query(text: str) -> Optional[str]:
    """把用户查询转成 FTS5 MATCH 表达式；``None`` 表示必须走逐行扫描。"""
    terms: List[str] = []
    for token in (text or "").split():
        pieces: List[str] = []
        for match in _FTS_TOKEN_RE.finditer(token):
            run = match.group(0)
            if _CJK_CHAR_RE.match(run):
                if len(run) == 1:
                    # bigram 索引里没有单字 token，硬查只会得到空结果。
                    return None
                pieces.extend(run[i:i + 2] for i in range(len(run) - 1))
            else:
                pieces.append(run.lower())
        if pieces:
            terms.append('"' + " ".join(pieces) + '"')
    return " AND ".join(terms) if terms else None


def _query_terms(text: str) -> List[str]:
    """摘要用的查询词（保留中文、英文、数字，忽略其它标点）。"""
    return [m.group(0) for m in _FTS_TOKEN_RE.finditer(text or "")]


def _snippet(content: str, query: str, width: int = 60) -> str:
    """从正文里截一段带上下文的摘要；找不到时就给开头一段。"""
    raw = str(content or "")
    lowered = raw.lower()
    hit = -1
    hit_len = 0
    for term in _query_terms(query):
        index = lowered.find(term.lower())
        if index >= 0 and (hit < 0 or index < hit):
            hit, hit_len = index, len(term)
    if hit < 0:
        head = raw[: width * 2].replace("\n", " ")
        return head + ("…" if len(raw) > width * 2 else "")
    start = max(0, hit - width)
    end = min(len(raw), hit + hit_len + width)
    fragment = raw[start:end].replace("\n", " ")
    return ("…" if start > 0 else "") + fragment + ("…" if end < len(raw) else "")


def _session_id_from_key(principal_id: str, principal: Optional[str]) -> str:
    """把持久化键还原成会话名（新键 liuhao-{principal}-{session}，旧键 liuhao-{session}）。"""
    raw = str(principal_id or "")
    if principal:
        prefix = f"liuhao-{principal}-"
        if raw.startswith(prefix):
            return raw[len(prefix):]
    if raw.startswith("liuhao-"):
        return raw[len("liuhao-"):]
    return raw


class ConversationStore:
    """持久化多轮对话历史（按 principal 隔离）。

    每条记录一行（自增 ``seq`` 保证插入顺序即恢复顺序），同一条消息只追加
    不改写，历史不可变、可完整重放。
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _default_db_path()
        self._lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        # FTS5 能力探测结果：不可用时搜索降级为逐行扫描，并在响应里注明。
        self._fts_available = False
        self._fts_mode = "unavailable"
        self._fts_error = ""
        self._init_db()

    @property
    def db_path(self) -> str:
        """实际落盘的库路径（账号备份按此路径取一致快照）。"""
        return self._db_path

    def _init_db(self) -> None:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(f"PRAGMA synchronous={_CONVERSATION_SYNCHRONOUS}")
            # 显式等待：不要依赖 sqlite3.connect 的隐式 5s，否则备份/导入等
            # 长写者一多就立刻把 "database is locked" 抛给用户。
            self._conn.execute(
                f"PRAGMA busy_timeout={_CONVERSATION_BUSY_TIMEOUT_MS}"
            )
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    turn INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                CREATE INDEX IF NOT EXISTS idx_conv_principal
                    ON conversation_turns(principal_id, turn, seq);
                CREATE TABLE IF NOT EXISTS conversation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    principal_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL DEFAULT '',
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
                );
                """
            )
            # MEM-007：租户边界必须是精确归属列，而不是 principal_id 的前缀包含
            #（``prefix`` 会命中 ``prefix-child``）。老库补列 + 回填由
            # ``_backfill_owner_principals`` 在首次搜索时完成。
            columns = {
                row[1]
                for row in self._conn.execute(
                    "PRAGMA table_info(conversation_turns)"
                ).fetchall()
            }
            if "owner_principal" not in columns:
                self._conn.execute(
                    "ALTER TABLE conversation_turns "
                    "ADD COLUMN owner_principal TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_conv_owner "
                "ON conversation_turns(owner_principal, turn, seq)"
            )
            self._conn.commit()
            self._init_search_index()
        except (sqlite3.Error, OSError) as exc:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None
            raise ConversationStoreError(
                "无法打开对话历史库"
                + describe_storage_failure(exc, self._db_path).removeprefix("无法写入")
            ) from exc

    # ------------------------------------------------------------------ #
    # 全文搜索索引
    # ------------------------------------------------------------------ #
    def _init_search_index(self) -> None:
        """探测并创建 FTS5 索引表（三级降级，绝不影响主库可用性）。

        1. ``content='' + contentless_delete=1``：只存倒排表（SQLite 3.43+）；
        2. 普通 FTS5 表（存原文，体积更大，但同样支持按 rowid 删除）；
        3. FTS5 不可用（编译时未启用）：``self._fts_available=False``，
           搜索自动降级为逐行扫描，并在接口里明确提示"搜索变慢"。
        """
        assert self._conn is not None
        candidates = (
            (
                "fts5-bigram-contentless",
                "CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5("
                "body, content='', contentless_delete=1, tokenize='unicode61')",
            ),
            (
                "fts5-bigram-stored",
                "CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5("
                "body, tokenize='unicode61')",
            ),
        )
        for mode, ddl in candidates:
            try:
                self._conn.execute(ddl)
                self._conn.commit()
            except sqlite3.Error as exc:
                self._fts_error = str(exc)
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                continue
            self._fts_available = True
            self._fts_mode = mode
            self._fts_error = ""
            return
        self._fts_available = False
        self._fts_mode = "unavailable"

    def _indexed_seq(self) -> int:
        """已经连续建好索引的最大 seq（0 = 还没建过）。"""
        assert self._conn is not None
        try:
            row = self._conn.execute(
                "SELECT value FROM conversation_meta WHERE key = 'fts_indexed_seq'"
            ).fetchone()
        except sqlite3.Error:
            return 0
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def _set_indexed_seq(self, seq: int) -> None:
        """记录索引进度（与索引写入同一事务）。"""
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO conversation_meta (key, value) VALUES ('fts_indexed_seq', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(seq),),
        )

    def _index_rows(self, rows: Sequence[Tuple[int, str]]) -> None:
        """把若干 (seq, content) 写进 FTS 表；调用方持锁并已提交主表写入。"""
        if not self._fts_available or not rows:
            return
        assert self._conn is not None
        try:
            marker = self._indexed_seq()
            self._conn.executemany(
                "INSERT INTO conversation_fts (rowid, body) VALUES (?, ?)",
                [(seq, _fts_index_text(text)) for seq, text in rows],
            )
            last = rows[-1][0]
            first = rows[0][0]
            # 只有"接着上一条"才推进进度；否则留给 ensure_search_index 回填，
            # 避免把中间未建索引的旧行误判为已完成。空库首次写入时，
            # 前面没有任何历史行，也可以直接把进度推到本条。
            continuous = marker > 0 and marker == first - 1
            if not continuous and marker == 0:
                prior = self._conn.execute(
                    "SELECT 1 FROM conversation_turns WHERE seq < ? LIMIT 1",
                    (first,),
                ).fetchone()
                continuous = prior is None
            if continuous:
                self._set_indexed_seq(last)
            self._conn.commit()
        except sqlite3.Error as exc:
            # 索引失败不能连累已经提交成功的对话正文：记下原因，
            # 下次 ensure_search_index 会重建这一段。
            self._fts_error = str(exc)
            try:
                self._conn.rollback()
            except sqlite3.Error:
                pass

    def ensure_search_index(self, limit: int = SEARCH_INDEX_CHUNK) -> Dict[str, Any]:
        """推进索引建设（最多 ``limit`` 行），返回进度；可重复调用。"""
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) FROM conversation_turns"
            ).fetchone()
            total = int(row[0]) if row and row[0] is not None else 0
            if not self._fts_available:
                return {
                    "available": False,
                    "mode": "unavailable",
                    "indexed": 0,
                    "index_total": total,
                    "ready": False,
                    "notice": SCAN_DEGRADED_NOTICE,
                }
            marker = self._indexed_seq()
            if total == 0 or marker >= total:
                return {
                    "available": True,
                    "mode": self._fts_mode,
                    "indexed": total,
                    "index_total": total,
                    "ready": True,
                    "notice": "",
                }
            rows = self._conn.execute(
                "SELECT seq, content FROM conversation_turns WHERE seq > ? "
                "ORDER BY seq ASC LIMIT ?",
                (marker, max(1, int(limit))),
            ).fetchall()
            if not rows:
                return {
                    "available": True,
                    "mode": self._fts_mode,
                    "indexed": marker,
                    "index_total": total,
                    "ready": True,
                    "notice": "",
                }
            first, last = int(rows[0][0]), int(rows[-1][0])
            try:
                # 幂等：这一段可能已被实时写入（append）索引过，先删后插。
                self._conn.execute(
                    "DELETE FROM conversation_fts WHERE rowid BETWEEN ? AND ?",
                    (first, last),
                )
                self._conn.executemany(
                    "INSERT INTO conversation_fts (rowid, body) VALUES (?, ?)",
                    [(int(seq), _fts_index_text(text)) for seq, text in rows],
                )
                self._set_indexed_seq(last)
                self._conn.commit()
            except sqlite3.Error as exc:
                self._fts_error = str(exc)
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                return {
                    "available": True,
                    "mode": self._fts_mode,
                    "indexed": marker,
                    "index_total": total,
                    "ready": False,
                    "notice": "搜索索引建立失败：" + str(exc),
                }
            return {
                "available": True,
                "mode": self._fts_mode,
                "indexed": last,
                "index_total": total,
                "ready": last >= total,
                "notice": "",
            }

    def build_search_index(self, max_seconds: Optional[float] = None) -> Dict[str, Any]:
        """把索引建到底（后台预热 / 验收脚本用）。"""
        import time as _time

        started = _time.monotonic()
        status = self.ensure_search_index()
        while not status["ready"] and status.get("available"):
            if max_seconds is not None and _time.monotonic() - started >= max_seconds:
                break
            before = status["indexed"]
            status = self.ensure_search_index()
            if status["indexed"] <= before:
                break
        return status

    def search_status(self) -> Dict[str, Any]:
        """搜索能力与索引进度（供界面展示"索引建立中 x%"）。"""
        assert self._conn is not None
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(seq) FROM conversation_turns"
            ).fetchone()
            total = int(row[0]) if row and row[0] is not None else 0
            marker = self._indexed_seq() if self._fts_available else 0
        return {
            "available": self._fts_available,
            "mode": self._fts_mode,
            "indexed": min(marker, total),
            "index_total": total,
            "ready": (not self._fts_available) or marker >= total,
        }

    # ------------------------------------------------------------------ #
    # 写入 / 读取
    # ------------------------------------------------------------------ #
    def _rollback_quietly(self) -> None:
        """失败后必须回滚，否则半条记录会被下一次成功提交连带写进库。"""
        try:
            self._conn.rollback()
        except sqlite3.Error:
            pass

    def _retry_on_busy(self, operation) -> Any:
        """对 ``database is locked/busy`` 做有界重试（3 次，1.5s 起指数退避）。

        只重试瞬时锁冲突；磁盘满、只读、权限错误立即上抛，避免把不可恢复的
        故障拖成三倍延迟。每次失败先回滚，防止重试把已插入的行重复写入。
        """
        delay = _CONVERSATION_LOCK_RETRY_BASE_SECONDS
        for attempt in range(1, _CONVERSATION_LOCK_RETRY_ATTEMPTS + 1):
            try:
                return operation()
            except sqlite3.Error as exc:
                self._rollback_quietly()
                if (
                    not is_database_busy(exc)
                    or attempt >= _CONVERSATION_LOCK_RETRY_ATTEMPTS
                ):
                    raise
                logger.warning(
                    "conversation write busy (attempt %d/%d), retry in %.1fs: %s",
                    attempt,
                    _CONVERSATION_LOCK_RETRY_ATTEMPTS,
                    delay,
                    exc,
                )
                time.sleep(delay)
                delay *= 2

    def _write_failure_message(self, exc: BaseException) -> str:
        if is_database_busy(exc):
            # 锁冲突不是磁盘/权限问题：不给英文原文，也不引导用户去清盘。
            return (
                "本轮对话暂时未能保存：数据库正忙，请稍后重试"
                "（无需清理磁盘或修改目录权限）。已成功写入的历史数据不受影响。"
            )
        detail = describe_storage_failure(exc, self._db_path)
        return "本轮对话未能保存" + detail.removeprefix("无法写入")

    @observe("conversation.append")
    @audited("p5.conversation.append", module="src.ai.conversation_store")
    def append(
        self,
        principal_id: str,
        turn: int,
        role: str,
        content: str,
        owner_principal: str = "",
    ) -> None:
        """追加一条对话消息（user 或 assistant）。"""
        with self._lock:
            def _write():
                cursor = self._conn.execute(
                    "INSERT INTO conversation_turns "
                    "(principal_id, turn, role, content, owner_principal) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (principal_id, turn, role, content, str(owner_principal or "")),
                )
                self._conn.commit()
                return cursor

            try:
                cursor = self._retry_on_busy(_write)
            except (sqlite3.Error, OSError) as exc:
                raise ConversationStoreError(self._write_failure_message(exc)) from exc
            seq = cursor.lastrowid
            if seq is not None:
                self._index_rows([(int(seq), content)])

    @observe("conversation.append_turn")
    @audited("p5.conversation.append", module="src.ai.conversation_store")
    def append_turn(
        self,
        principal_id: str,
        turn: int,
        user_content: str,
        assistant_content: str,
        owner_principal: str = "",
    ) -> None:
        """一次写入一轮问答（user + assistant 两行）。

        与调用两次 :meth:`append` 相比，这里只有**一次事务提交、一条审计**：
        一轮对话本来就是原子语义（两行要么一起可见、要么都不写），拆成两次
        ``append`` 既多一次 ``FULL`` 同步落盘，也多一条冗余审计事件。

        动作名沿用 ``p5.conversation.append``，审计面看到的仍是"追加了一条会话
        记录"，不引入新的动作类型。
        """
        with self._lock:
            def _write():
                seqs = []
                for role, content in (
                    ("user", user_content),
                    ("assistant", assistant_content),
                ):
                    cursor = self._conn.execute(
                        "INSERT INTO conversation_turns "
                        "(principal_id, turn, role, content, owner_principal) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            principal_id,
                            turn,
                            role,
                            content,
                            str(owner_principal or ""),
                        ),
                    )
                    seqs.append(int(cursor.lastrowid))
                self._conn.commit()
                return seqs

            try:
                seqs = self._retry_on_busy(_write)
            except (sqlite3.Error, OSError) as exc:
                raise ConversationStoreError(self._write_failure_message(exc)) from exc
            self._index_rows(
                [(seqs[0], user_content), (seqs[1], assistant_content)]
            )

    @observe("conversation.load")
    def load(self, principal_id: str, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """按插入顺序返回该主体的历史消息 ``[{role, content}, ...]``。

        返回的列表可直接作为多轮上下文的历史片段。``limit`` 为 None 时全量。
        """
        query = (
            "SELECT role, content FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq ASC"
        )
        params: List[Any] = [principal_id]
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [{"role": role, "content": content} for role, content in rows]

    def load_turns(self, principal_id: str) -> List[Dict[str, Any]]:
        """按顺序返回带时间戳的完整消息（导出「人可读对话」用）。

        ``load`` 只给 role/content，是为了直接喂给模型；导出需要让人看出
        每句话是什么时候说的，所以单独给一个带 ``turn`` / ``created_at``
        的读取口，不改动既有调用方契约。
        """
        query = (
            "SELECT turn, role, content, created_at FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq ASC"
        )
        with self._lock:
            rows = self._conn.execute(query, (principal_id,)).fetchall()
        return [
            {
                "turn": int(turn),
                "role": str(role),
                "content": str(content),
                "created_at": float(created_at or 0),
            }
            for turn, role, content, created_at in rows
        ]

    def load_recent(self, principal_id: str, limit: int) -> List[Dict[str, str]]:
        """返回最近 ``limit`` 条历史消息（保持顺序）。"""
        query = (
            "SELECT role, content FROM conversation_turns "
            "WHERE principal_id = ? ORDER BY seq DESC LIMIT ?"
        )
        with self._lock:
            rows = self._conn.execute(query, (principal_id, limit)).fetchall()
        # 倒序取回后正序返回，保证时间顺序。
        rows = list(reversed(rows))
        return [{"role": role, "content": content} for role, content in rows]

    def get_summary(self, principal_id: str) -> str:
        """读取该主体已持久化的「超出窗口」分层摘要（无则空串）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT summary FROM conversation_summaries WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
        return str(row[0]) if row and row[0] else ""

    def summarize_overflow(self, principal_id: str, keep_messages: int) -> int:
        """把超过最近 ``keep_messages`` 条的消息增量折进持久化摘要。

        MEM-005：``load_recent`` 只取最近 240 条，更早的消息若不留摘要就会永久
        丢失（实测「预算三百元」在第 1 轮、第 301 轮已检索不到）。这里按
        ``last_seq`` 增量推进，摘要带 ``[#seq]`` 可回溯，且第二句关键约束会进
        「关键事实」层（见 ``deterministic_summary``）。
        """
        keep = max(1, int(keep_messages or 1))
        max_summary_chars = SUMMARY_MAX_CHARS * 3
        with self._lock:
            try:
                max_row = self._conn.execute(
                    "SELECT MAX(seq) FROM conversation_turns WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()
                max_seq = int(max_row[0]) if max_row and max_row[0] is not None else 0
                if max_seq <= keep:
                    return 0
                state = self._conn.execute(
                    "SELECT summary, last_seq FROM conversation_summaries "
                    "WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()
                previous = str(state[0]) if state else ""
                last_seq = int(state[1]) if state else 0
                cutoff = max_seq - keep
                rows = self._conn.execute(
                    "SELECT seq, role, content FROM conversation_turns "
                    "WHERE principal_id = ? AND seq > ? AND seq <= ? "
                    "ORDER BY seq ASC",
                    (principal_id, last_seq, cutoff),
                ).fetchall()
                if not rows:
                    return 0
                piece = deterministic_summary(
                    [
                        {"seq": int(seq), "role": str(role), "content": str(content)}
                        for seq, role, content in rows
                    ]
                )
                summary = piece if not previous else f"{previous}；{piece}"
                if len(summary) > max_summary_chars:
                    half = max_summary_chars // 2
                    summary = (
                        summary[:half]
                        + "\n…（中间摘要已折叠，保留最早关键事实与最近进展）…\n"
                        + summary[-half:]
                    )
                self._conn.execute(
                    "INSERT INTO conversation_summaries "
                    "(principal_id, summary, last_seq, updated_at) "
                    "VALUES (?, ?, ?, strftime('%s', 'now')) "
                    "ON CONFLICT(principal_id) DO UPDATE SET "
                    "summary = excluded.summary, last_seq = excluded.last_seq, "
                    "updated_at = excluded.updated_at",
                    (principal_id, summary, int(rows[-1][0])),
                )
                self._conn.commit()
                return len(rows)
            except (sqlite3.Error, OSError) as exc:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                logger.warning("conversation summarize failed: %s", exc)
                return 0

    def get_last_turn(self, principal_id: str) -> int:
        """返回该主体已进行到的轮数（无记录返回 0）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(turn) FROM conversation_turns WHERE principal_id = ?",
                (principal_id,),
            ).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def clear(self, principal_id: str) -> None:
        """清空该主体的对话历史（对应 ``reset`` 语义）。"""
        with self._lock:
            try:
                if self._fts_available:
                    # 索引与正文同一口径清理，避免搜索还能翻出已清空的会话。
                    self._conn.execute(
                        "DELETE FROM conversation_fts WHERE rowid IN "
                        "(SELECT seq FROM conversation_turns WHERE principal_id = ?)",
                        (principal_id,),
                    )
                self._conn.execute(
                    "DELETE FROM conversation_turns WHERE principal_id = ?", (principal_id,)
                )
                self._conn.commit()
            except (sqlite3.Error, OSError) as exc:
                raise ConversationStoreError(
                    "清空对话历史失败"
                    + describe_storage_failure(exc, self._db_path).removeprefix("无法写入")
                ) from exc

    def has_rows(self, principal_id: str) -> bool:
        """该主体是否有任何已落盘的对话消息（用于新旧键择优）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversation_turns WHERE principal_id = ? LIMIT 1",
                (principal_id,),
            ).fetchone()
        return row is not None

    def list_session_ids(
        self,
        principal: Optional[str],
        known_principals: Sequence[str] = (),
        allow_legacy: bool = False,
    ) -> List[str]:
        """从持久库解析出会话名（重启后仍有列表，不再依赖进程内存）。

        键口径兼容两代：

        * 新键 ``liuhao-{principal}-{session}``（当前版本）；
        * 旧键 ``liuhao-{session}``（0.1.0；``session`` 往往自带 ``liuhao-``
          前缀，故旧行形如 ``liuhao-liuhao-session-xxx``）。

        ``allow_legacy`` 只对根主体/全局视图开放：旧键没有主体段，无法区分
        归属；把它开放给普通成员会暴露别人的会话名。``known_principals`` 用于
        排除"其实是别人新键"的行（例如 ``liuhao-alice-chat``）。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT principal_id FROM conversation_turns"
            ).fetchall()
        ids: set[str] = set()
        others = [
            f"{item}-" for item in known_principals
            if item and item != principal
        ]
        for (raw,) in rows:
            principal_id = str(raw or "")
            if not principal_id.startswith("liuhao-"):
                continue
            rest = principal_id[len("liuhao-"):]
            if not rest:
                continue
            if principal is None:
                ids.add(rest)
                continue
            composite = f"{principal}-"
            if rest.startswith(composite):
                session_id = rest[len(composite):]
                if session_id:
                    ids.add(session_id)
                continue
            if not allow_legacy:
                continue
            if any(rest.startswith(prefix) for prefix in others):
                continue
            ids.add(rest)
        return sorted(ids)

    def list_sessions_page(
        self,
        principal: Optional[str],
        known_principals: Sequence[str] = (),
        allow_legacy: bool = False,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """会话列表分页（``limit=None`` 保持旧的全量语义）。"""
        ids = self.list_session_ids(principal, known_principals, allow_legacy)
        total = len(ids)
        start = max(0, int(offset or 0))
        if limit is None:
            page = ids[start:]
        else:
            page = ids[start:start + max(0, int(limit))]
        return {
            "sessions": page,
            "total": total,
            "has_more": start + len(page) < total,
            "limit": limit,
            "offset": start,
        }

    def load_page(
        self, principal_id: str, limit: Optional[int] = None, offset: int = 0
    ) -> Dict[str, Any]:
        """单会话历史分页：``offset`` 从**最近一条**往前数。

        这样默认看到的是最新一轮（聊天界面刷新后落在底部），"加载更早"
        传 ``offset=已加载条数`` 即可，不会因为新消息追加而重复/跳过。
        """
        with self._lock:
            total = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM conversation_turns WHERE principal_id = ?",
                    (principal_id,),
                ).fetchone()[0]
            )
            start = max(0, int(offset or 0))
            if limit is None:
                history = self.load(principal_id)
                page = history[start:]
            else:
                rows = self._conn.execute(
                    "SELECT role, content FROM conversation_turns "
                    "WHERE principal_id = ? ORDER BY seq DESC LIMIT ? OFFSET ?",
                    (principal_id, max(1, int(limit)), start),
                ).fetchall()
                page = [
                    {"role": role, "content": content}
                    for role, content in reversed(rows)
                ]
        return {
            "history": page,
            "total": total,
            "has_more": start + len(page) < total,
            "limit": limit,
            "offset": start,
        }

    def _run_search(
        self,
        join: str,
        where: str,
        params: List[Any],
        sql: str,
        limit: int,
        offset: int,
    ) -> Tuple[int, List[Any]]:
        """执行一次"总数 + 当前页"查询；调用方持锁。"""
        assert self._conn is not None
        total = int(
            self._conn.execute(
                "SELECT COUNT(*) " + join + " WHERE " + where, params
            ).fetchone()[0]
        )
        rows = self._conn.execute(
            sql + "ORDER BY t.seq DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return total, rows

    def _resolve_owner_principal(
        self, principal_id: str, known_principals: Sequence[str]
    ) -> str:
        """从会话键解析账号归属：按**已知账号最长匹配**，不按前缀猜测。

        ``liuhao-prefix-child-s1`` 同时以 ``liuhao-prefix-`` 开头，但已知账号里
        有更长的 ``prefix-child``，因此归属是 ``prefix-child`` 而不是 ``prefix``。
        """
        pid = str(principal_id or "")
        if not pid.startswith("liuhao-"):
            return pid
        rest = pid[len("liuhao-") :]
        for candidate in sorted(
            {str(item) for item in known_principals if item}, key=len, reverse=True
        ):
            if rest == candidate or rest.startswith(f"{candidate}-"):
                return candidate
        # 无法解析的历史键保持空归属：宁可不可见，也不按前缀错认给别人。
        return ""

    def _backfill_owner_principals(self, known_principals: Sequence[str]) -> None:
        """一次性回填老库的 owner_principal；之后搜索只做精确等值比较。

        回填失败不影响搜索：未回填的行保持空归属（不会命中任何账号）。
        """
        if not known_principals:
            return
        assert self._conn is not None
        with self._lock:
            try:
                rows = self._conn.execute(
                    "SELECT DISTINCT principal_id FROM conversation_turns "
                    "WHERE owner_principal = ''"
                ).fetchall()
                updates = []
                for row in rows:
                    owner = self._resolve_owner_principal(
                        row[0], known_principals
                    )
                    if owner:
                        updates.append((owner, row[0]))
                if updates:
                    self._conn.executemany(
                        "UPDATE conversation_turns SET owner_principal = ? "
                        "WHERE principal_id = ? AND owner_principal = ''",
                        updates,
                    )
                    self._conn.commit()
            except sqlite3.Error as exc:
                try:
                    self._conn.rollback()
                except sqlite3.Error:
                    pass
                logger.warning("conversation owner backfill failed: %s", exc)

    def search(
        self,
        principal: Optional[str],
        query: str,
        limit: int = 20,
        offset: int = 0,
        known_principals: Sequence[str] = (),
    ) -> Dict[str, Any]:
        """在当前账号的会话里全文搜索消息（FTS5 优先，必要时降级并明说）。"""
        text = (query or "").strip()
        limit_i = max(1, min(200, int(limit or 20)))
        offset_i = max(0, int(offset or 0))
        status = self.search_status()
        match = _fts_query(text)
        if not text:
            return {
                "results": [],
                "count": 0,
                "total": 0,
                "has_more": False,
                "limit": limit_i,
                "offset": offset_i,
                "mode": "empty",
                "degraded": False,
                "ready": status["ready"],
                "indexed": status["indexed"],
                "index_total": status["index_total"],
                "notice": "",
            }

        # MEM-007：账号边界 = 精确 owner_principal 等值；绝不再用
        # ``instr(principal_id, prefix)=1``（prefix 会命中 prefix-child）。
        if principal:
            self._backfill_owner_principals(known_principals)
            owner_clause = "t.owner_principal = ?"
            owner_params: List[Any] = [principal]
        else:
            owner_clause = "1 = 1"
            owner_params = []

        use_fts = self._fts_available and match is not None and status["indexed"] > 0
        if use_fts:
            mode = "fts5-bigram"
            # FTS5 的 MATCH 左侧必须是表名本身（不能是别名），所以索引表不取别名。
            where = f"conversation_fts MATCH ? AND {owner_clause}"
            params: List[Any] = [match, *owner_params]
            notice = ""
            if not status["ready"]:
                percent = (
                    int(status["indexed"] * 100 / status["index_total"])
                    if status["index_total"]
                    else 0
                )
                notice = (
                    f"历史搜索索引正在建立（已完成 {percent}%），"
                    "结果可能不完整，完成后会自动变快。"
                )
        else:
            mode = "scan"
            where = (
                f"{owner_clause} AND instr(lower(t.content), lower(?)) > 0"
            )
            params = [*owner_params, text]
            if not self._fts_available:
                notice = SCAN_DEGRADED_NOTICE
            elif match is None:
                notice = SHORT_QUERY_NOTICE
            else:
                notice = "搜索索引尚未建立，本次改为逐行扫描，会变慢。"

        join = (
            "FROM conversation_fts JOIN conversation_turns t "
            "ON t.seq = conversation_fts.rowid"
            if use_fts
            else "FROM conversation_turns t"
        )
        sql = (
            "SELECT t.seq, t.principal_id, t.turn, t.role, t.content, t.created_at "
            f"{join} WHERE {where} "
        )
        with self._lock:
            try:
                total, rows = self._run_search(join, where, params, sql, limit_i, offset_i)
            except sqlite3.Error as exc:
                if not use_fts:
                    raise ConversationStoreError(
                        "搜索对话历史失败"
                        + describe_storage_failure(exc, self._db_path).removeprefix("无法写入")
                    ) from exc
                # 索引查询失败（索引表损坏/被删）：降级扫描，不能给用户 500。
                self._fts_error = str(exc)
                use_fts = False
                mode = "scan"
                notice = "搜索索引查询失败，本次降级为逐行扫描，会明显变慢。"
                join = "FROM conversation_turns t"
                where = (
                    f"{owner_clause} "
                    "AND instr(lower(t.content), lower(?)) > 0"
                )
                params = [*owner_params, text]
                sql = (
                    "SELECT t.seq, t.principal_id, t.turn, t.role, t.content, "
                    "t.created_at " + f"{join} WHERE {where} "
                )
                total, rows = self._run_search(
                    join, where, params, sql, limit_i, offset_i
                )

        results = [
            {
                "session_id": _session_id_from_key(principal_id, principal),
                "turn": int(turn),
                "role": str(role),
                "snippet": _snippet(content, text),
                "created_at": float(created_at or 0),
            }
            for _seq, principal_id, turn, role, content, created_at in rows
        ]
        return {
            "results": results,
            "count": len(results),
            "total": total,
            "has_more": offset_i + len(results) < total,
            "limit": limit_i,
            "offset": offset_i,
            "mode": mode,
            "degraded": mode != "fts5-bigram",
            "ready": status["ready"],
            "indexed": status["indexed"],
            "index_total": status["index_total"],
            "notice": notice,
        }

    # ------------------------------------------------------------------ #
    # 观测
    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        """返回存储统计（含各主体的轮数分布）。"""
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0]
            principals = self._conn.execute(
                "SELECT principal_id, COUNT(DISTINCT turn) FROM conversation_turns "
                "GROUP BY principal_id ORDER BY principal_id"
            ).fetchall()
        return {
            "total_turns": total,
            "principals": {p: t for p, t in principals},
        }


# ---------------------------------------------------------------------- #
# 全局单例（对齐 audit kernel 的 get_audit_store）
# ---------------------------------------------------------------------- #
_conversation_store: Optional[ConversationStore] = None
#: 冷会话构造已并发化，多个线程会同时首次调用这里。不加锁时每个线程都去
#: 打开同一个 sqlite 库（PRAGMA/journal_mode），实测直接撞出
#: ``database is locked``，并留下多个未关闭的连接。
_store_lock = threading.Lock()


@observe("conversation.get_conversation_store")
def get_conversation_store() -> ConversationStore:
    """获取或创建全局会话存储实例（线程安全，只打开一次）。

    打开失败时先写 ``evt=db.open.failed subsystem=conversation_store``（带路径与
    sqlite 原文）再抛出：否则日志里只有一句 ``file is not a database``，看不出
    是哪个库坏了。
    """
    global _conversation_store
    if _conversation_store is None:
        with _store_lock:
            if _conversation_store is None:
                try:
                    store = ConversationStore()
                except Exception as exc:
                    try:
                        from ..observability import boot_report

                        boot_report.record_db_open_failure(
                            "conversation_store",
                            _default_db_path(),
                            exc,
                            action=(
                                "degraded: 多轮对话历史不可用；修复数据目录写权限、"
                                "隔离/重建损坏库后重启"
                            ),
                        )
                    except Exception:  # noqa: BLE001 - 日志失败不能掩盖原始异常
                        pass
                    raise
                _conversation_store = store
    return _conversation_store
