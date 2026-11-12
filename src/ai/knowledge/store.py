"""企业知识库与长期记忆的本地存储层。

设计边界
========

* **只用 Python 内置 sqlite3**，不引入 Postgres / pgvector / 向量服务。单机自用
  场景下 WAL 足够支撑「一个写入 + 多个读取」，且备份就是拷一个文件。
* **知识库与长期记忆各用一个库文件**，再与正式业务库（目标、任务、项目、审计）
  物理隔离。记忆模块的 SQL 里没有 ``kb_*`` 表，写入路径也不可能碰企业资料 ——
  这比「靠代码自觉」更可靠。
* **FTS5 只做词法检索**。中文按重叠 bigram 预切词写入索引，英文/数字按词保留；
  排序用 BM25（标题列加权）。没有 embedding provider 也能离线全文检索，且不会
  把「词法命中」伪装成语义相似度。

本模块只负责连接、建表与通用行转换；业务规则在 ``service.py`` / ``memory.py``。
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src._time import utc_now
from src.common.data_paths import resolve_store_path

KNOWLEDGE_DB_ENV = "LIUHAO_KNOWLEDGE_ENTERPRISE_DB"
MEMORY_DB_ENV = "LIUHAO_KNOWLEDGE_MEMORY_DB"

#: 企业知识库主库（元数据、版本正文、分块、FTS 索引）。
KNOWLEDGE_LEGACY_DB = os.path.join("data", "knowledge_enterprise.sqlite3")

#: 长期记忆库。**必须与知识库分开**：记忆允许 Agent 频繁写入，企业资料不允许。
MEMORY_LEGACY_DB = os.path.join("data", "knowledge_memory.sqlite3")

_CATEGORY_SEED = (
    # (stable id, name, visibility, description)
    ("cat_enterprise", "企业知识", "enterprise", "公司层面的通用资料"),
    ("cat_product", "产品资料", "team", "产品说明、规格与对外材料"),
    ("cat_customer", "客户资料", "team", "客户相关文档，按团队可见"),
    ("cat_project", "项目资料", "team", "项目过程与交付资料"),
    ("cat_policy", "制度", "enterprise", "公司制度与规范"),
    ("cat_process", "流程", "enterprise", "标准作业流程"),
    ("cat_decision", "历史决策", "team", "关键决策及其背景"),
    ("cat_agent", "Agent 经验", "enterprise", "智能体沉淀的可复用经验"),
    ("cat_skills", "Skills 资料", "enterprise", "技能与工具使用资料"),
)

_ENTERPRISE_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_categories (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    parent_id   TEXT REFERENCES kb_categories(id) ON DELETE RESTRICT,
    path        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    visibility  TEXT NOT NULL DEFAULT 'team'
                CHECK (visibility IN ('private', 'team', 'enterprise')),
    created_by  TEXT NOT NULL,
    system      INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_kb_categories_sibling_name
    ON kb_categories(COALESCE(parent_id, ''), name);
CREATE INDEX IF NOT EXISTS idx_kb_categories_parent ON kb_categories(parent_id);

CREATE TABLE IF NOT EXISTS kb_documents (
    id                 TEXT PRIMARY KEY,
    owner              TEXT NOT NULL,
    project_id         TEXT NOT NULL DEFAULT '',
    category_id        TEXT REFERENCES kb_categories(id) ON DELETE SET NULL,
    doc_key            TEXT NOT NULL,
    title              TEXT NOT NULL,
    current_version    INTEGER NOT NULL DEFAULT 0,
    source_type        TEXT NOT NULL DEFAULT 'upload',
    source_ref         TEXT NOT NULL DEFAULT '',
    origin_filename    TEXT NOT NULL DEFAULT '',
    mime               TEXT NOT NULL DEFAULT '',
    visibility         TEXT NOT NULL DEFAULT 'team'
                       CHECK (visibility IN ('private', 'team', 'enterprise')),
    tags               TEXT NOT NULL DEFAULT '[]',
    description        TEXT NOT NULL DEFAULT '',
    expires_at         TEXT,
    expiry_notice_days INTEGER NOT NULL DEFAULT 30,
    status             TEXT NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'deleted')),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    deleted_at         TEXT,
    UNIQUE(owner, doc_key)
);
CREATE INDEX IF NOT EXISTS idx_kb_documents_owner ON kb_documents(owner);
CREATE INDEX IF NOT EXISTS idx_kb_documents_category ON kb_documents(category_id);

CREATE TABLE IF NOT EXISTS kb_document_versions (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    content_hash    TEXT NOT NULL,
    extracted_text  TEXT NOT NULL,
    chunk_count     INTEGER NOT NULL DEFAULT 0,
    file_path       TEXT NOT NULL DEFAULT '',
    origin_filename TEXT NOT NULL DEFAULT '',
    mime            TEXT NOT NULL DEFAULT '',
    size_bytes      INTEGER NOT NULL DEFAULT 0,
    text_source     TEXT NOT NULL DEFAULT 'extracted',
    note            TEXT NOT NULL DEFAULT '',
    created_by      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(document_id, version)
);
CREATE INDEX IF NOT EXISTS idx_kb_versions_document
    ON kb_document_versions(document_id);

CREATE TABLE IF NOT EXISTS kb_chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    version      INTEGER NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    page         INTEGER,
    UNIQUE(document_id, version, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);
"""

_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS lt_memories (
    id          TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    project_id  TEXT NOT NULL DEFAULT '',
    employee_id TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'personal'
                CHECK (scope IN ('personal', 'project', 'employee', 'enterprise')),
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT '',
    source_ref  TEXT NOT NULL DEFAULT '',
    tags        TEXT NOT NULL DEFAULT '[]',
    confidence  REAL,
    expires_at  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_lt_memories_owner ON lt_memories(owner);
CREATE INDEX IF NOT EXISTS idx_lt_memories_project ON lt_memories(project_id);
CREATE INDEX IF NOT EXISTS idx_lt_memories_employee ON lt_memories(employee_id);

CREATE TABLE IF NOT EXISTS lt_memory_revisions (
    id         TEXT PRIMARY KEY,
    memory_id  TEXT NOT NULL REFERENCES lt_memories(id) ON DELETE CASCADE,
    revision   INTEGER NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    tags       TEXT NOT NULL DEFAULT '[]',
    edited_by  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(memory_id, revision)
);
"""

_schema_lock = threading.Lock()
_initialized: "set[str]" = set()
_fts_available: Optional[bool] = None


def knowledge_db_path() -> str:
    """知识库主库路径：显式变量 > 工作区根 > 数据根 > 历史默认值。"""
    return resolve_store_path(
        "knowledge_enterprise.sqlite3",
        KNOWLEDGE_LEGACY_DB,
        env_var=KNOWLEDGE_DB_ENV,
        workspace=True,
    )


def memory_db_path() -> str:
    """长期记忆库路径。与知识库库文件不同名、不同目录也能配。"""
    return resolve_store_path(
        "knowledge_memory.sqlite3",
        MEMORY_LEGACY_DB,
        env_var=MEMORY_DB_ENV,
        workspace=True,
    )


def knowledge_files_dir() -> Path:
    """原件落盘目录：默认与知识库主库同目录下的 ``knowledge_files``。"""
    explicit = (os.environ.get("LIUHAO_KNOWLEDGE_FILES_DIR") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(knowledge_db_path()).expanduser().resolve().parent / "knowledge_files"


def now_iso() -> str:
    return utc_now().isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def connect(path: str) -> sqlite3.Connection:
    """打开一个 WAL 连接。每次调用都是新连接，调用方负责 ``with`` 关闭。"""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def fts_available(connection: sqlite3.Connection) -> bool:
    """当前 SQLite 是否带 FTS5（Python 3.11+ 官方包默认带）。"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'kb_chunk_fts'"
    ).fetchone()
    return row is not None


def ensure_enterprise_schema(connection: sqlite3.Connection) -> bool:
    """建表 + 写入系统预置分类。返回 FTS5 是否可用。"""
    global _fts_available
    with _schema_lock:
        connection.executescript(_ENTERPRISE_SCHEMA)
        try:
            connection.executescript(
                "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunk_fts USING fts5("
                "  seg,"           # 正文 bigram 串
                "  title_seg,"     # 标题/文件名 bigram 串
                "  chunk_id UNINDEXED,"
                "  document_id UNINDEXED,"
                "  tokenize='unicode61 remove_diacritics 2'"
                ");"
            )
            _fts_available = True
        except sqlite3.OperationalError:
            # 老 SQLite 没有 FTS5：检索自动降级 LIKE，功能不消失。
            _fts_available = False
        _seed_categories(connection)
        connection.commit()
        return bool(_fts_available)


def ensure_memory_schema(connection: sqlite3.Connection) -> None:
    with _schema_lock:
        connection.executescript(_MEMORY_SCHEMA)
        connection.commit()


def _seed_categories(connection: sqlite3.Connection) -> None:
    stamp = now_iso()
    for order, (cat_id, name, visibility, description) in enumerate(_CATEGORY_SEED, 1):
        row = connection.execute(
            "SELECT id FROM kb_categories WHERE id = ?", (cat_id,)
        ).fetchone()
        if row is not None:
            continue
        connection.execute(
            "INSERT INTO kb_categories"
            " (id, name, parent_id, path, description, visibility, created_by,"
            "  system, sort_order, created_at, updated_at)"
            " VALUES (?, ?, NULL, ?, ?, ?, '__system__', 1, ?, ?, ?)",
            (cat_id, name, f"/{cat_id}", description, visibility, order, stamp, stamp),
        )


def open_knowledge() -> sqlite3.Connection:
    connection = connect(knowledge_db_path())
    ensure_enterprise_schema(connection)
    return connection


def open_memory() -> sqlite3.Connection:
    connection = connect(memory_db_path())
    ensure_memory_schema(connection)
    return connection


def loads_tags(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def dumps_tags(tags: Optional[Iterable[str]]) -> str:
    cleaned = [str(item).strip() for item in (tags or []) if str(item).strip()]
    return json.dumps(cleaned, ensure_ascii=False)


def row_to_dict(row: sqlite3.Row, json_fields: Iterable[str] = ()) -> Dict[str, Any]:
    data = dict(row)
    for field in json_fields:
        if field in data:
            data[field] = loads_tags(data[field])
    return data
