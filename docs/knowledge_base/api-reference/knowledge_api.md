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
| `POST` | `/v1/knowledge/documents/upload` | 上传文件（同步，小文件） |
| `POST` | `/v1/knowledge/documents/upload/jobs` | 上传文件（异步，大文件，按页进度/可取消） |
| `GET` | `/v1/knowledge/ingest/jobs/{job_id}` | 查询异步入库任务进度 |
| `POST` | `/v1/knowledge/ingest/jobs/{job_id}/cancel` | 取消异步入库（全回滚） |

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
| `allow_low_quality` | bool | 否 | 默认 `false`；仅当用户确认“乱码也要入库”时传 `true`，见下文质量闸 |

> 本接口是**同步**的：从提取到嵌入全部完成后才返回，适合小文件。大文件（例如
> 300 页 PDF）请走下面的异步任务接口，否则用户只能干等、看不到进度也无法取消。

支持的文件格式（**真实解析，不支持的类型会明确拒绝**）：

| 后缀 | 解析方式 | 说明 |
|------|----------|------|
| `.txt` / `.md` | UTF-8 解码 | Markdown 标题层级进入 `heading_path` |
| `.pdf` | pypdf 文本层 | 逐页提取；每个 chunk 带 `page`（1 起），跨页时带 `pages` 范围 |
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
    "paged": true,
    "quality": {
      "garbled_chars": 0,
      "total_chars": 18342,
      "garbled_ratio": 0.0,
      "low_quality": false
    }
  }
}
```

> **多账号命名空间。** 在账号树部署下，`document_id` 会收进调用者账号的命名空间：
> `<account>--<doc_id>`（例如 `alice--product_manual.pdf`）。这样两个账号上传同名
> 文件不会互相覆盖，去重与删除也只在各自范围内成立；单机/老部署令牌没有账号树时
> 保持原始 id。账号内可见性以令牌主体为准：成员只读写自己，主账号可聚合读子树，
> 跨账号一律中性 404。

### 跨页段落规则（PDF）

PDF 会按页提取，但**一段话不会被页边界切成两个 chunk**：

1. 每页先独立分块，页码保持精确；
2. 如果某一页的**最后一个 chunk 结尾不是句末标点**（`。！？.!?…`，可容忍后置引号/
   括号），就把下一页开头的第一个 chunk 追加进来，补全整句；
3. 合并后 `page` 记为**起始页**，`pages` 记录跨页范围，例如 `[2, 3]`，
   `page_label` / `location` 显示为 `第 2-3 页`；
4. 表格与代码块不参与合并（宁可保持整块）；合并上限为 `2 * chunk_size`
   字符，防止无标点的超长 PDF 产生一个巨大的 embedding 输入。

效果：检索命中“前半句”时，返回的 `text` 已经包含后半句，模型不会拿到半句话。

### 提取质量闸（乱码检测）

提取正文后会统计替换符 `\ufffd`、方块字符 `□■` 与私用区字符的比例。若同时满足
“乱码字符 ≥ 20”且“乱码占比 ≥ 15%”，默认**拒绝入库**并返回：

| 情况 | 状态码 | `detail` |
|------|--------|----------|
| 乱码比例过高（字体缺字 / 扫描件） | 422 | `该文件可能是扫描件或字体嵌入异常，提取质量差（乱码 N 字符，占比 x%）；如确认仍需入库，请重试并设置 allow_low_quality=true` |

这是**让用户自己决定**，而不是悄悄把劣质语料存进知识库。用户确认后可用同名文件
加 `allow_low_quality=true` 重新上传；响应里 `extraction.quality.low_quality`
会保留为 `true`，便于前端/审计看到这份文档的质量标记。少量乱码（低于阈值）正常入库。

### 去重语义

内容哈希在 **提取后的文本** 上计算（空白归一化后 SHA-256），因此同一份 PDF
改名为 `product_manual-copy.pdf` 再传不会重复入库：第二次返回
`deduplicated: true` 与 `matched_document_id`（首次入库的 id），不会新增分块。
哈希带账号命名空间，上述去重只在**同一账号内**成立，不会跨账号命中或泄露对方的
`matched_document_id`。

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

## 异步上传任务（大文件）

大文件不要在同步接口里干等。异步接口立即返回 `202` 和 `job_id`，提取与嵌入在
后台 worker 里执行，进度是**真实按页计数**，不是定时器假进度。

```
POST /v1/knowledge/documents/upload/jobs
Content-Type: multipart/form-data
Authorization: Bearer <human token, scope L0 或 admin>
```

表单字段与同步上传一致：`file`（必填）、`doc_id`（可选）、
`allow_low_quality`（可选，默认 `false`）。

### 响应（202 Accepted）

```json
{
  "job_id": "3f2a9c...",
  "document_id": "product_manual.pdf",
  "source": "file",
  "file_name": "product_manual.pdf",
  "status": "queued",
  "phase": "queued",
  "total_pages": 0,
  "extracted_pages": 0,
  "embedded_pages": 0,
  "processed_pages": 0,
  "progress_percent": 0.0,
  "partial_commit_policy": "all-or-nothing（取消后不写入任何分块，不会留下半成品）",
  "note": null
}
```

### 查询进度

```
GET /v1/knowledge/ingest/jobs/{job_id}
```

关键字段：

| 字段 | 说明 |
|------|------|
| `phase` | `queued` → `extracting` → `embedding` → `committing` → `done` / `cancelled` / `failed` |
| `total_pages` | 该文件的总页数（PDF 在解析页目录后即确定；空页也计入） |
| `extracted_pages` | 已真正解析完的页数 |
| `embedded_pages` | 已完成嵌入的页数 |
| `processed_pages` | 上面两者之和 |
| `progress_percent` | 由“已处理页数 / (2 × 总页数)”计算；未真正成功前最高 99.0，`succeeded` 才给 100 |
| `quality` | 提取质量统计（乱码字符数 / 占比 / 是否低质量） |
| `error_code` | 失败时的稳定错误码，如 `pdf_no_text_layer` / `low_quality_extraction` |

DOCX / HTML / TXT 没有页的概念：`total_pages` 为 `0`，进度回退为分块计数
（`total_chunks` / `processed_chunks`），仍然不是假百分比。

### 取消

```
POST /v1/knowledge/ingest/jobs/{job_id}/cancel
```

* 提取阶段：在**下一页边界**生效（290/300 页取消，只读到第 290 页就停）；
* 嵌入阶段：在当前页/分块边界生效；
* **取消 = 全回滚**：向量在写入前全部算完，写入是一次事务替换。取消后
  `result.committed_chunks = 0`，知识库中不会留下该文档的任何分块、向量、
  FTS 行或内容哈希行；`note` 会写明“未写入任何数据/分块（全回滚）”。
* 任务已结束才取消：返回 `cancel_accepted: false`，不会误报成功。

### 任务失败不占 HTTP 状态码

扫描版 PDF、加密/损坏文件、乱码比例过高、嵌入服务不可用等，都体现在任务上：
`status = "failed"`、`error_code`、可读的 `error` 文案。乱码场景还会带上
`quality`，用户查看后可以用 `allow_low_quality=true` 重新提交。

> 任务记录保存在进程内存中：进程重启会丢失未完成任务的状态（已入库的数据本身
> 是持久的）。已完成任务保留供查询，但不会被恢复。

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
      "pages": [3],
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
    "abstention_calibration": {
      "embedder": "hashing:sha256-bigram-512",
      "calibrated": true,
      "thresholds_from": "hashing:sha256-bigram-512",
      "reason": null
    },
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
    "candidate_k": 40,
    "vector_candidates": 40,
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
    "abstention_calibration": {
      "embedder": "hashing:sha256-bigram-512",
      "calibrated": true,
      "thresholds_from": "hashing:sha256-bigram-512",
      "reason": null
    },
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
空结果。

### 混合检索：候选与重排是两个独立阶段

- **候选阶段**：向量取 top `candidate_k = max(40, top_k * 4)` 个，FTS5/BM25 取同样
  数量；两者决定“哪些片段有机会被看见”。下限 40 而非 16/20，是因为网关默认
  `top_k=4`，旧窗口会把向量名次 30+ 的正确章节挡在向量列表外（只剩词法一票）。
- **重排阶段**：按 BM25 强度加权的 RRF。向量列表贡献 `1/(60 + rank)`；词法列表贡献
  `bm25_weight/(60 + rank)`，`bm25_weight` 以本次最强命中为 1.0 归一化。这样
  “一个精确命中”不会因为名次票数少而被“两三个近似命中”压过。
  `retrieval.lexical_weighting` 为 `"bm25"`；无 FTS5 时降级为纯向量
  （`mode = "vector"`，该字段为 `null`），行为与之前一致。

修正依据（2026-11-06，同一套 34 篇 / 30 条评测集）：修复前真实模型
`section_hit@5 = 0.0` —— 正确章节词法排第 1（BM25 `-22.6`）、向量排第 30，
平铺 RRF 后被两个相邻章节（词法第 3/4，BM25 `-5.9`）反超，用户会看到
“文档对了、段落全错”。修复后正确章节排第 1、`section_hit@5 = 1.0`。
排查过程中“提高 candidate_k”“缩小 chunk”“去掉 `context_header` 或给
nomic 加 `search_query:` / `search_document:` 前缀”都实测无效并已回滚
（36 个 chunk 本来就全在 40 候选窗口内，正确章节从未缺席候选）。

### 阈值必须随 embedding 模型重测（硬性规则）

`min_top_score` / `min_score_gap` / `strong_match_similarity` 是针对**某一个
embedding 模型的分数分布**调出来的，不是检索代码的固定属性。更换
`LIUHAO_EMBEDDING_PROVIDER` 或 `LIUHAO_EMBEDDING_MODEL`（包括更换 OpenAI 兼容
服务背后的模型）后，**必须先重跑评测并更新阈值**，否则检索可能静默变差：

```bash
python -m src.knowledge.evaluation --provider ollama
python -m src.knowledge.evaluation --provider all    # hashing + 所有已检测到的真实源
```

报告里的 `providers[].recommended.env` 给出可直接使用的四个环境变量值（三个阈值
加上 `LIUHAO_KB_ABSTAIN_CALIBRATED_FOR`）；对比表
输出到 stderr，JSON 报告输出到 stdout。若本机没有任何真实 embedding 源，报告会
明确写出“本机无可用 embedding 源，真实模型下阈值未验证”，并把
`real_provider_available` / `real_model_verified` 置为 `false`，不会用伪造数字
填空。

#### 没有评测证据的 provider：宽松弃答，绝不沿用别人的阈值

用户新填一个 OpenAI 兼容 embedding 时，它的余弦分布与 nomic / hashing 无关，
`0.65` 或 `0.08` 都没有依据。代码里按 **实际 embedder 身份**
（`provider:model`）选择闸门（`resolve_abstention_config`）：

| embedder 身份 | 阈值来源 | 行为 |
|---|---|---|
| `hashing:sha256-bigram-512` / `mock` | 本仓库评测 | `0.08 / 0.05 / 0.85` |
| `ollama:nomic-embed-text` | 本仓库评测 | `0.65 / 0.05 / 0.85` |
| 其它任何 provider/model | **uncalibrated** | `-1.0 / 0.0 / 0.85`：非空库不弃答 |

未校准时宁可不弃答，也不把有答案的问题判成“没找到”。
`retrieval.abstention_calibration` 如实说明来源：

```json
{
  "embedder": "openai:text-embedding-3-small",
  "calibrated": false,
  "thresholds_from": "uncalibrated",
  "reason": "该 embedding provider/model 没有评测证据：使用宽松弃答……"
}
```

启用严格弃答必须完成一次校准：运行评测后，同时设置
`LIUHAO_KB_ABSTAIN_CALIBRATED_FOR=<provider>:<model>` 和三个阈值环境变量。
声明身份与当前 embedder 不匹配、或三个阈值缺任何一个，都会**保持**
uncalibrated（半个校准比没有更危险）。`thresholds_from` 只会是
`<provider>:<model>`（有证据）、`uncalibrated`（无证据）或 `explicit`
（调用方显式固定，仅评测/测试）。

当前默认 `0.08 / 0.05 / 0.85` **只适用于词面嵌入（hashing embedder）**：在 34
篇文档、30 条查询（29 条有答案 + 1 条无答案）的评测集上，有答案召回 0.8621、
无答案弃答 1.0、误弃答 0、`section_hit@5` 1.0；BM25 加权融合使 MRR 从 0.8621
降到 0.8448（两条查询里 BM25 更偏好干扰文档，而词面嵌入本来排对了），这是记录
在案的取舍，不改变任何阈值。

真实模型（本机 Ollama `nomic-embed-text`，768 维，同一套评测集，2026-11-06，
BM25 加权融合之后）：有答案召回@5 `0.8966`、@10 `0.8966`、MRR `0.8534`；无答案
弃答 `1.0`；误弃答 `0/29`；`section_hit@5` `1.0`。扫描建议阈值仍为
**0.65 / 0.05 / 0.85**（`holds_abstention=true`）；起决定作用的是 0.65 的绝对
下限，gap 保持词面默认值（评测数据无法区分备选值）。注意推荐余量很窄：有答案
最低 top 分数 `0.6759`，无答案最高 top 分数 `0.6280`；`0.08` 在真实模型下几乎
不筛任何候选，只能靠 gap 规则兜底。

详细依据见
[`src/knowledge/abstention.py`](../../../src/knowledge/abstention.py) 与
[`src/knowledge/evaluation.py`](../../../src/knowledge/evaluation.py) 模块文档。

### 端到端引用正确性（真实 embedding + 真实对话模型，2026-11-06）

验证“文档对了、段落错了”不会再发生：用真实模型通过网关 HTTP 提问，检查
**引用片段本身**是否来自正确章节，而不只看命中文档。

| 项目 | 值 |
|---|---|
| 传输 | `POST /v1/chat`，`use_knowledge=true`，本机 uvicorn（真实链路，无桩） |
| Embedding | Ollama `nomic-embed-text`（768 维） |
| 对话模型 | Ollama `qwen2.5:3b`（网关模型路由选择） |
| 问题 | `数据库连接池耗尽导致超时怎么处理` |
| 期望出处 | `kb-032#0`，章节 `连接故障手册 / 数据库连接超时`，标志词 `pool_size` |
| 实际第一引用 | `kb-032#0`，`score=0.027505`，片段含 `pool_size` |
| 是否命中 | **是**（第一来源即正确章节） |

实际引用顺序：`kb-032#0`（正确章节，含 `pool_size`）→ `kb-033#0`（干扰文档）→
`kb-032#1`（缓存连接超时）→ `kb-032#2`（消息队列连接超时）。回答原文包含
“调大 pool_size 并重启”，与期望章节一致。`knowledge.diagnostics`：
`retrieval_status=ok`、`top_score=0.7831`、`runner_up=0.7824`、
`thresholds={min_top_score:0.65,min_score_gap:0.05}`。

同时如实记录两点：

1. **修复前同一条链路第一来源是干扰文档 `kb-033#0`**（正确章节只排第 4），
   这正是候选窗口下限 20 导致正确块的向量贡献被挡掉；改为 40 后第一来源回到
   正确章节。
2. 本次未能使用 DeepSeek：本机环境变量与 `.env` 都没有 `DEEPSEEK_API_KEY`，
   `config/default.yaml` 仅声明了 `api_key_env: DEEPSEEK_API_KEY`。因此对话模型
   用本机 Ollama `qwen2.5:3b`，embedding 用真实 `nomic-embed-text`；
   **没有用桩数据替代缺失的 DeepSeek**。用户提供 Key 后重跑同一脚本即可得到
   DeepSeek 版本的记录。

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
