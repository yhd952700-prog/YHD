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

## 上传文档（当前实现）

> `/v1/knowledge/bases/...` 为规划中的多知识库形态；**当前可用的上传端点是下面这个**
> （落在进程级 SQLite 向量库，单库）。不带前缀的 `/knowledge/...` 是兼容别名。

```
POST /v1/knowledge/documents/upload
Content-Type: multipart/form-data
Authorization: Bearer <human token, scope L0 或 admin>
```

表单字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | file | 是 | 文件本体 |
| `doc_id` | string | 否 | 文档 id；缺省用文件名 |

支持的文件格式（**真实解析，不支持的类型会明确拒绝**）：

| 后缀 | 解析方式 | 说明 |
|------|----------|------|
| `.txt` / `.md` | UTF-8 解码 | Markdown 标题层级进入 `heading_path` |
| `.pdf` | pypdf 文本层 | 逐页提取；每个 chunk 带 `page`（1 起） |
| `.docx` | python-docx | 正文、表格；`Heading 1..6` 层级进入 `heading_path` |
| `.html` / `.htm` | 标准库 `html.parser` | `h1..h6` 层级、脚本/样式内容丢弃 |

### 上限（超限在解析前拒绝，不会等到 OOM）

| 限制 | 值 | 超限返回 |
|------|----|----------|
| 上传文件大小 | 20 MB | `413 文件过大：…` |
| PDF 页数 | 300 页 | `413 PDF 页数超过上限：N 页，最多 300 页…` |
| DOCX 解压后总大小 | 150 MB | `413 DOCX 解压后体积过大…` |
| 提取后正文字符数 | 2,000,000 | `413 提取出的正文过长：…` |

### 响应（200 OK）

```json
{
  "document_id": "product_manual.pdf",
  "chunks": 24,
  "vectors": 24,
  "deduplicated": false,
  "matched_document_id": null,
  "source": "product_manual.pdf",
  "extraction": {
    "file_type": "pdf",
    "page_count": 12,
    "text_chars": 18342,
    "title": null,
    "paged": true
  }
}
```

### 去重语义

内容哈希在 **提取后的文本** 上计算（空白归一化后 SHA-256），因此同一份 PDF
改名为 `product_manual-copy.pdf` 再传不会重复入库：第二次返回
`deduplicated: true` 与 `matched_document_id`（首次入库的 id），不会新增分块。

### 失败文案（原文，前端可直接展示）

| 情况 | 状态码 | `detail` |
|------|--------|----------|
| 扫描版 / 纯图片 PDF（无文本层） | 422 | `该 PDF 无文本层，需要 OCR，暂不支持` |
| 加密 PDF | 422 | `PDF 已加密，无法读取文本层；请上传未加密的 PDF 或先解除加密` |
| 损坏 PDF | 422 | `PDF 文件损坏或格式不合法，无法解析：…` |
| 损坏 DOCX | 422 | `DOCX 文件损坏（不是有效的 zip 包）：…` |
| 空文件 / 空正文 | 400 | `文件为空` / `文件未包含任何正文文本` |
| 非 UTF-8 文本 | 400 | `文件不是 UTF-8 编码：…` |
| 不支持的后缀 | 415 | `不支持的文件类型：.xlsx；支持 .txt / .md / .pdf / .docx / .html / .htm` |

**扫描版 PDF 绝不会以空字符串“成功入库”**：没有文本层就是 422，避免后续检索
永远搜不到却看不出原因。

### 依赖与许可证

`pypdf`（BSD-3-Clause）、`python-docx`（MIT，传递依赖 `lxml` BSD-3-Clause）。
HTML 走标准库，不新增依赖。选型理由见
[`src/knowledge/extractors.py`](../../../src/knowledge/extractors.py) 模块文档与
`oss-registry.yaml` 的 `document-parsing` 段。

---

## 规划中的多知识库端点（未实现）

以下端点是设计稿，尚未接线，调用会 404；保留在此仅作接口规划参考。

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/v1/knowledge/bases` | 列出知识库 |
| `POST` | `/v1/knowledge/bases` | 创建知识库 |
| `GET` | `/v1/knowledge/bases/{id}` | 获取知识库详情 |
| `PUT` | `/v1/knowledge/bases/{id}` | 更新知识库 |
| `DELETE` | `/v1/knowledge/bases/{id}` | 删除知识库 |
| `GET` | `/v1/knowledge/bases/{id}/documents` | 列出文档 |
| `POST` | `/v1/knowledge/bases/{id}/import-url` | 从 URL 导入 |
| `POST` | `/v1/knowledge/bases/{id}/reindex` | 重建索引 |

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
  "top_k": 5,
  "threshold": 0.7
}
```

### 响应（200 OK）

```json
{
  "query": "如何配置 API 认证？",
  "top_k": 5,
  "count": 12,
  "hits": [
    {
      "doc_id": "doc-def456#3",
      "score": 0.0107,
      "text": "[第 3 页 / 3.2 API 认证]\nAPI 认证配置步骤如下：1. 进入管理后台...",
      "page": 3,
      "location": "第 3 页",
      "metadata": {
        "document_id": "doc-def456",
        "chunk_index": 3,
        "page": 3,
        "heading_path": ["3.2 API 认证"]
      }
    }
  ],
  "retrieval": {
    "status": "ok",
    "reason": null,
    "top_score": 0.5012,
    "runner_up_score": 0.4137,
    "thresholds": {"min_top_score": 0.08, "min_score_gap": 0.05},
    "hits": [
      {
        "doc_id": "doc-def456#3",
        "score": 0.0107,
        "text": "[第 3 页 / 3.2 API 认证]\nAPI 认证配置步骤如下：1. 进入管理后台...",
        "page": 3,
        "location": "第 3 页",
        "metadata": {"document_id": "doc-def456", "page": 3}
      }
    ],
    "mode": "hybrid",
    "fts_available": true,
    "candidate_k": 20,
    "vector_candidates": 20,
    "lexical_candidates": 2,
    "rrf_k": 60
  }
}
```

### 弃答（无相关内容）响应

`retrieval.status` 只有两个取值：`"ok"` 或 `"no_relevant"`。判为
`"no_relevant"` 时 `hits`（以及 `retrieval.hits`）**必须是空数组**，不会为了
凑满 `top_k` 返回低分片段：

```json
{
  "query": "员工年假怎么申请",
  "top_k": 5,
  "count": 12,
  "hits": [],
  "retrieval": {
    "status": "no_relevant",
    "reason": "ambiguous",
    "top_score": 0.145,
    "runner_up_score": 0.124,
    "thresholds": {"min_top_score": 0.08, "min_score_gap": 0.05},
    "hits": [],
    "mode": "hybrid",
    "fts_available": true
  }
}
```

`retrieval.reason` 的取值与含义（上层要给用户说不同的话，**不能合并**）：

| reason | 含义 | 典型情况 |
|---|---|---|
| `below_threshold` | 最高分低于绝对阈值 | 库里有文档，但没有任何片段够格 |
| `ambiguous` | 最高分过线，但与次高分的差距不足 | 候选彼此太像，无法确定相关 |
| `empty_corpus` | 库里一个文档都没有 | 尚未入库，不是“没搜到” |

判定规则（所有比较均为 `>=`，恰好等于阈值算通过）：

1. 绝对阈值 `top_score >= min_top_score`；
2. 相对差距 `top_score - runner_up_score >= min_score_gap`，或
   `top_score >= strong_match_similarity`（近乎完全匹配时允许并列）；
3. 词法佐证：FTS5/BM25 路径至少命中一个候选。

`confident = absolute and (relative or strong_match or lexical)`；不置信时返回
空结果。阈值默认 `0.08 / 0.05`，是在 34 篇文档、30 条查询（29 条有答案 +
1 条无答案）的评测集上扫描得到的，详细依据见
[`src/knowledge/abstention.py`](../../../src/knowledge/abstention.py) 模块文档。

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
