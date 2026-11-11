"""企业知识库与长期记忆（0.3.0）。

对外只暴露 :mod:`src.gateway.knowledge_enterprise` 的 HTTP 接口；本包内部分为：

* ``store``   —— 两个独立 SQLite 库、表结构与连接；
* ``text``    —— 多格式提取、中文 bigram、分块；
* ``service`` —— 分类/文档/版本/检索/过期；
* ``memory``  —— 长期记忆（独立库、项目/员工隔离）。
"""

from .service import (
    KnowledgeError,
    Viewer,
    VISIBILITIES,
    create_category,
    delete_category,
    delete_document,
    diff_versions,
    document_file_info,
    document_file_path,
    expiring_documents,
    expiry_state,
    get_document,
    get_version,
    ingest_file,
    ingest_text,
    list_categories,
    list_documents,
    search,
    status_counts,
    update_category,
    update_document,
)
from .memory import (
    MEMORY_SCOPES,
    create_memory,
    delete_memory,
    get_memory,
    list_memories,
    memory_views,
    update_memory,
)

__all__ = [
    "KnowledgeError",
    "MEMORY_SCOPES",
    "VISIBILITIES",
    "Viewer",
    "create_category",
    "create_memory",
    "delete_category",
    "delete_document",
    "delete_memory",
    "diff_versions",
    "document_file_info",
    "document_file_path",
    "expiring_documents",
    "expiry_state",
    "get_document",
    "get_memory",
    "get_version",
    "ingest_file",
    "ingest_text",
    "list_categories",
    "list_documents",
    "list_memories",
    "memory_views",
    "search",
    "status_counts",
    "update_category",
    "update_document",
    "update_memory",
]
