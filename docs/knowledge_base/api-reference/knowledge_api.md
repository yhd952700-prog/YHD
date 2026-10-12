# 知识 API 参考

本 API 提供知识库管理和 RAG（检索增强生成）功能，包括文档导入、知识库管理、检索等。

---

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/v1/knowledge/bases` | 列出知识库 |
| `POST` | `/v1/knowledge/bases` | 创建知识库 |
| `GET` | `/v1/knowledge/bases/{id}` | 获取知识库详情 |
| `PUT` | `/v1/knowledge/bases/{id}` | 更新知识库 |
| `DELETE` | `/v1/knowledge/bases/{id}` | 删除知识库 |
| `POST` | `/v1/knowledge/bases/{id}/documents` | 上传文档 |
| `GET` | `/v1/knowledge/bases/{id}/documents` | 列出文档 |
| `DELETE` | `/v1/knowledge/bases/{id}/documents/{doc_id}` | 删除文档 |
| `POST` | `/v1/knowledge/search` | 知识检索 |
| `POST` | `/v1/knowledge/rag` | RAG 查询 |
| `POST` | `/v1/knowledge/bases/{id}/import-url` | 从 URL 导入 |
| `POST` | `/v1/knowledge/bases/{id}/reindex` | 重建索引 |

---

## 创建知识库

### 请求

```
POST /v1/knowledge/bases
Content-Type: application/json
```

```json
{
  "name": "产品文档库",
  "description": "公司产品相关的技术文档和用户手册",
  "config": {
    "embedding_model": "nomic-embed-text",
    "chunk_size": 1000,
    "chunk_overlap": 100,
    "language": "zh-CN"
  },
  "scope_level": "L2"
}
```

### 响应（201 Created）

```json
{
  "id": "kb-abc123",
  "name": "产品文档库",
  "description": "公司产品相关的技术文档和用户手册",
  "document_count": 0,
  "chunk_count": 0,
  "size_bytes": 0,
  "config": {
    "embedding_model": "nomic-embed-text",
    "chunk_size": 1000,
    "chunk_overlap": 100
  },
  "created_at": "2026-10-07T12:00:00Z",
  "updated_at": "2026-10-07T12:00:00Z"
}
```

---

## 上传文档

向知识库中上传文档，支持多种格式。

### 请求

```
POST /v1/knowledge/bases/{kb_id}/documents
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | 文档文件 |
| `metadata` | string | 否 | JSON 格式的元数据 |

支持的文件格式：
- 文本：`.txt`, `.md`, `.markdown`
- 文档：`.pdf`, `.docx`, `.doc`
- 数据：`.csv`, `.json`, `.jsonl`
- 网页：`.html`, `.htm`

### 响应（201 Created）

```json
{
  "id": "doc-def456",
  "filename": "product_manual.pdf",
  "size_bytes": 1048576,
  "status": "processing",
  "chunk_count": 0,
  "metadata": {
    "author": "技术团队",
    "version": "2.0"
  },
  "created_at": "2026-10-07T12:00:00Z"
}
```

---

## 知识检索

在知识库中搜索相关内容。

### 请求

```
POST /v1/knowledge/search
Content-Type: application/json
```

```json
{
  "query": "如何配置 API 认证？",
  "base_ids": ["kb-abc123"],
  "limit": 5,
  "threshold": 0.7,
  "options": {
    "include_metadata": true,
    "highlight": true
  }
}
```

### 响应（200 OK）

```json
{
  "query": "如何配置 API 认证？",
  "total": 12,
  "results": [
    {
      "id": "chunk-xxx",
      "document_id": "doc-def456",
      "document_name": "product_manual.pdf",
      "content": "API 认证配置步骤如下：1. 进入管理后台...",
      "score": 0.91,
      "page": 15,
      "metadata": {
        "section": "3.2 API 认证"
      },
      "highlight": "API <em>认证</em>配置步骤如下..."
    }
  ]
}
```

---

## RAG 查询

使用检索增强生成回答问题。

### 请求

```
POST /v1/knowledge/rag
Content-Type: application/json
```

```json
{
  "query": "LiuHao AI OS 支持哪些部署方式？",
  "base_ids": ["kb-abc123"],
  "model": "qwen2.5:7b",
  "options": {
    "top_k": 5,
    "include_sources": true,
    "stream": false,
    "prompt_template": "请根据以下参考资料回答问题..."
  }
}
```

### 响应（200 OK）

```json
{
  "query": "LiuHao AI OS 支持哪些部署方式？",
  "answer": "LiuHao AI OS 支持以下几种部署方式：1. 源码部署...",
  "sources": [
    {
      "document": "部署指南.md",
      "section": "2.1 部署方式",
      "relevance": 0.95
    }
  ],
  "tokens_used": {
    "prompt": 1200,
    "completion": 150,
    "total": 1350
  }
}
```

---

## 从 URL 导入

从网页 URL 导入内容到知识库。

### 请求

```
POST /v1/knowledge/bases/{kb_id}/import-url
Content-Type: application/json
```

```json
{
  "url": "https://docs.example.com/guide",
  "options": {
    "crawl_depth": 1,
    "include_links": true,
    "exclude_patterns": ["*/login/*"],
    "max_pages": 50
  }
}
```

### 响应（202 Accepted）

```json
{
  "import_id": "imp-xyz789",
  "status": "processing",
  "url": "https://docs.example.com/guide",
  "started_at": "2026-10-07T12:00:00Z"
}
```

---

## 错误码

| HTTP 状态码 | 错误码 | 说明 |
|-------------|--------|------|
| 400 | `INVALID_REQUEST` | 请求参数无效 |
| 401 | `UNAUTHORIZED` | 未认证 |
| 403 | `FORBIDDEN` | 无权限 |
| 404 | `KNOWLEDGE_BASE_NOT_FOUND` | 知识库不存在 |
| 409 | `DUPLICATE_NAME` | 知识库名称重复 |
| 413 | `FILE_TOO_LARGE` | 文件过大 |
| 415 | `UNSUPPORTED_FORMAT` | 不支持的文件格式 |
| 429 | `RATE_LIMITED` | 触发速率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |

---

## 下一步

- [记忆 API](memory_api.md) - 记忆系统 API
- [任务 API](tasks_api.md) - 任务管理 API
