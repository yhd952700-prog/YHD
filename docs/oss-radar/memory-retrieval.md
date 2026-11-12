# 开源技术雷达 · 第 2 路：记忆与检索

> 调研对象：D:\LiuHao-AI-OS（鎏灏 LIUHAO AI OS）
> 调研日期：2026-11-06
> 调研范围：长期/短期记忆、记忆压缩与遗忘、知识图谱与时序图谱、RAG/语义检索/向量库、文档解析与切分、MCP/工具调用、本地与云端 Embedding、Reranker。
> 数据来源：GitHub REST API（仓库元数据，抓取于 2026-11-06）、raw.githubusercontent.com 上的 LICENSE 原文、PyPI、NVD/PyPA advisory 数据库、项目本地源码。
> 诚信约定：未能核验的字段一律写“未获取”，不做推测填充；社区 benchmark 数字均标注“非本项目实测”。

---

## 0. 本路核心结论（先读这里）

### 0.1 什么规模必须换向量库，换哪个最划算

**现状真实实现**：`src/knowledge/vector_store.py` 的 `PersistentVectorStore` 启动时把全部向量从 SQLite 读入 Python `dict`，embedding 以 JSON 文本存 `TEXT` 列，检索是纯 Python 循环逐条算余弦（无 numpy、无向量索引、无 SQL 侧过滤）。这决定了瓶颈不是“数据库慢”，而是**Python 对象内存膨胀 + O(n·d) 解释器级循环**。

量级估算（非本项目实测，用于定阈值；d=768，按 Python 逐元素乘加约 60µs/条、Python float 约 32B/维估算）：

| chunk 规模 | 单次检索估算 | 常驻内存估算 | 判定 |
|---|---|---|---|
| 5k | ~0.3 s | ~120 MB | 勉强可用，已影响交互体感 |
| 1万 | ~0.6 s | ~250 MB | 临界，必须改造 |
| 5万 | ~3 s | ~1.2 GB | 不可接受 |
| 20万 | ~12 s | ~5 GB | 大概率 OOM |
| 100万 | 不可用 | ~25 GB | 不可用 |

**结论（按“最小改动 + 最大收益”排序）**：

1. **0–1万 chunk：不换库，先改数据层**。embedding 改 `float32` BLOB（`array('f')`/`struct`），入库时 L2 归一化，检索用 numpy 批量矩阵乘。收益是内存降约 8–10 倍、单查询降到毫秒级，零新增服务、零 Docker、零接口变更。这是**当前最划算的一步**，应优先于换库。
2. **1万–10万 chunk：引入 `sqlite-vec`（MIT/Apache-2.0 双许可）**。同一个 SQLite 文件、同一个 `PersistentVectorStore` 接口，另加一个 `vec0` 虚拟表即可；无进程、无 Docker、无云依赖。社区 benchmark（非本项目实测）显示 1M×512 维精确扫描约 395 ms/查询，线性折算 10 万条约 40 ms，满足单机交互。
3. **10万–100万 chunk：换嵌入式 `LanceDB`（Apache-2.0，Rust，无服务端）**。支持 IVF-PQ/HNSW、标量过滤、版本化；以 wheel 形式随 Python 进程运行，不引入 Docker。
4. **>100万 chunk 或多租户高并发**：才考虑 Qdrant/Milvus 等服务化方案；这违背“单机、无 Docker 依赖”的当前约束，属于条件触发项，不在本期引入。

**不建议**：在当前阶段为了“先进”而直接上 Chroma/Qdrant Server/Milvus/pgvector。它们要么引入独立服务与容器，要么需要 Postgres，和本项目“单机一键启动”的产品形态冲突；收益（HNSW 召回率）在 10 万条以内并不明显。

### 0.2 MCP 适配层接主链路的安全前置条件

现状：`src/adapters/mcp/mcp_adapter.py` 已切换到 FastMCP（`fastmcp-slim 4.0.3`），支持 stdio / streamable HTTP / server 三种模式，外部 server 配置为空（`configs/mcp/servers.json` 为 `{"servers": []}`），默认关闭；server 模式只投影 `memory.search`、`system.status` 两个 capability。**结构是对的，缺的是“接入主链路前的硬门槛”**。

必须同时满足才允许打开主链路（建议作为 Go/No-Go 门禁，写入配置校验与 CI）：

1. **工具投影白名单（硬门槛）**：外部 MCP server 的 `tools/list` 结果不得直接透传给 LLM。必须逐工具登记 `server_id / tool_name / schema_hash / capability / risk_level / tenant_scope`，未登记默认拒绝；工具名与描述做长度截断与字符清洗。
2. **描述不可信原则（硬门槛）**：MCP 的 tool description、resource、prompt 一律按不可信数据处理，禁止拼接进 system prompt；这是 tool poisoning / 间接提示注入的主要入口。
3. **主体与租户绑定（硬门槛）**：每次调用从 JWT subject 派生 `user_id/tenant_id/role`，禁止信任请求体；写类 capability 必须过 `require_human_principal`；MCP server 侧再校验一次租户。
4. **子进程沙箱**：stdio 模式已有命令 allowlist，但还需最小 env（禁止把 API Key 注入子进程）、无 shell、禁网/只读挂载、CPU/内存/时长上限、工作目录固定。
5. **网络面**：HTTP transport 强制 HTTPS；URL 做 SSRF 校验（拒私网/回环/链路本地，仅管理员显式配置的地址放行）；OAuth 令牌必须绑定 `resource`（FastMCP 曾出过 OAuth Proxy 令牌跨 server 复用问题，见 §2 安全列）。
6. **输出治理**：结果大小上限、JSON Schema 校验、超时、失败显式失败（禁止静默降级）、结果哈希入审计。
7. **人工审批**：`risk_level >= MEDIUM` 或破坏性操作（write/delete/execute）默认进入 approval，不允许自动执行。
8. **会话与熔断**：为 streamable HTTP 会话加空闲过期与总量上限（MCP Python SDK 有过会话不回收的 CVE）；按 server 限流、连续失败自动熔断并禁用。
9. **供应链**：锁定 `fastmcp-slim` 版本并校验 wheel 来源与哈希；升级前跑 CVE 比对（当前 4.0.3，PyPI 最新 4.1.6）。

### 0.3 记忆召回是否有真实实现，缺什么

**结论：有三套记忆实现，但“默认可用且语义化”的召回没有真正闭环。**

| 实现 | 持久化 | 召回方式 | 是否接主链路 |
|---|---|---|---|
| `src/kernels/memory/__init__.py` + `store.py`（MemoryKernel） | SQLite 写穿，真实持久化 | **精确 key** + tier/scope/tag 过滤，TTL 过期、compress 晋升、auto_cleanup | 部分（AgentMemory / kernel_tools） |
| `src/knowledge/memory.py`（MemoryManager，10 层） | 默认走 Mem0：需要 `OPENAI_API_KEY` + **Qdrant localhost:6333**；失败回退 `InMemoryBackend`（JSON 文件，**子串匹配**） | Mem0 语义召回（条件满足时）；回退时子串召回 | 是（AI Employee / Goal / Workflow 经 `create_memory_manager`） |
| `src/knowledge/memory_system.py`（MemoryService，SQLAlchemy） | 数据库持久化，带 RBAC/审计 | 结构化查询 | **未接 API 主链路**（仅被 re-export 与测试引用） |

**缺口清单**：

- 主链路默认不是语义召回：`MemoryManager` 默认后端是 Mem0，而 `Mem0Backend._default_config()` 写死了 OpenAI LLM/Embedder 与 Qdrant（`localhost:6333`）；在“单机、无 Docker、仅本地模型”的部署里该配置不可用。`MemoryManager` 直接构造时会捕获异常并 `print` 后回退 `InMemoryBackend`（JSON 文件 + 子串匹配）；而 `create_memory_manager()` 是先构造后端再交给 `MemoryManager`，Mem0 初始化失败可能直接抛出而非回退——两条路径行为不一致，且都违反“禁止静默降级/吞错”的约束。
- `src/ai/tools/builtin.py` 的 `MemoryTool` 用的是独立 `WorkingMemory(capacity=50)`，与 MemoryKernel 不共享，形成“工具记忆孤岛”。
- `src/ai/tools/builtin.py` 的 `KnowledgeSearchTool` 是硬编码模拟知识库，未接 `src/knowledge` 的真实检索。
- 没有混合检索（BM25 + 向量）与 rerank，召回质量完全依赖单路余弦 + 阈值。
- 没有实体/关系/时序有效期（bi-temporal），即没有真正的知识图谱层。
- 没有跨会话 consolidation 的语义摘要：`compress` 的默认 summarizer 是**无损拼接**，不是 LLM 摘要。
- 没有召回可观测：无命中率、延迟、空召回率指标。

---

## 1. 该域能力现状表（附源码位置）

| # | 能力 | 现状 | 实现位置 | 关键缺口 |
|---|---|---|---|---|
| 1 | 短期工作记忆 | 有，LRU/容量 + 激活度衰减 | `src/ai/memory/working_memory.py` | 与持久层不共享；工具侧另起一份 |
| 2 | 长期记忆内核 | 有，SQLite 写穿 + 分层 TTL + provenance + scope | `src/kernels/memory/__init__.py`、`src/kernels/memory/store.py`、`src/kernels/memory/backends.py` | 召回是精确 key，不是语义 |
| 3 | 记忆压缩 | 有，tier 晋升 + 容量压缩 | `src/kernels/memory/__init__.py` | 默认 summarizer 为拼接，非语义摘要 |
| 4 | 记忆遗忘 | 有，TTL 到期 + `auto_cleanup` | `src/kernels/memory/__init__.py` | 无重要性衰减驱动的淘汰 |
| 5 | 10 层记忆管理 | 有代码，默认 Mem0 后端 | `src/knowledge/memory.py`（`MemoryManager`、`Mem0Backend`、`InMemoryBackend`） | 依赖 OpenAI+Qdrant；失败静默回退子串 |
| 6 | 结构化记忆服务 | 有代码（SQLAlchemy + RBAC） | `src/knowledge/memory_system.py`、`src/knowledge/db.py` | 未接 API 主链路 |
| 7 | 语义网络/图谱 | 有轻量内存图（节点/边/激活扩散） | `src/ai/memory/semantic_network.py` | 进程内 `dict`，不持久化，子串检索 |
| 8 | 元记忆 | 有（信念/缺口记录） | `src/ai/memory/meta_memory.py` | 未参与召回排序 |
| 9 | 知识库文档入库 | 有，chunk→embedding→upsert | `src/knowledge/documents.py` | 仅纯文本；无 PDF/DOCX/HTML 解析 |
| 10 | 切分 | 有，字符窗口 + overlap | `src/knowledge/chunker.py` | 无结构/语义/代码切分 |
| 11 | Embedding | 有，OpenAI 兼容 + Ollama + 显式 fail-closed | `src/knowledge/embedding.py` | 无本地 ONNX/fastembed 轻量路线；无维度/模型变更迁移 |
| 12 | 向量存储 | SQLite 单表 + JSON TEXT + 启动全量载入 | `src/knowledge/vector_store.py` | 纯 Python 余弦；无索引；无元数据过滤 |
| 13 | 检索器 | 有，top_k + 相似度阈值 | `src/knowledge/retriever.py` | 单路向量；无 BM25/混合/rerank |
| 14 | RAG 管线 | 有（检索→拼 context→LLM） | `src/knowledge/rag_pipeline.py` | **未接入 `/v1/chat` 与 Agent 主链路**（`src/gateway/chat.py` 无 knowledge/memory 引用） |
| 15 | 知识 API | 有 status/ingest/search/query | `src/api/routes/knowledge.py` | 无文档列表/删除/重建索引；无租户级过滤 |
| 16 | Agent 工具-记忆 | 有 `memory` 工具 | `src/ai/tools/builtin.py`（`MemoryTool`） | 用独立 `WorkingMemory`，非持久内核 |
| 17 | Agent 工具-知识 | 有 `knowledge.search` | `src/ai/tools/builtin.py`（`KnowledgeSearchTool`） | **硬编码模拟数据**，未接真实向量库 |
| 18 | MCP 适配层 | 有，FastMCP 三种 transport + allowlist + 默认关闭 | `src/adapters/mcp/mcp_adapter.py`、`configs/mcp/servers.json` | 未接主链路；外部 server 未配置；工具投影/审批/审计未闭环 |
| 19 | 时序知识图谱 | 无 | — | 完全缺失 |
| 20 | Reranker | 无 | — | 完全缺失（全仓无 `rerank/` 命中） |

已有测试基线（扩展回归时直接挂在这些文件旁）：`tests/kernels/memory/test_memory.py`、`tests/kernels/memory/test_memory_backends.py`、`tests/knowledge/test_memory_system.py`、`tests/knowledge/test_rag_pipeline.py`、`tests/knowledge/test_embedding_fail_closed.py`、`tests/knowledge/test_embedding_pipeline_phase22.py`、`tests/test_knowledge_live.py`、`tests/test_knowledge_memory.py`、`tests/test_retriever_metadata_shadowing.py`、`tests/test_mcp_adapter.py`、`tests/ai/test_enhanced_memory.py`。

---

## 2. 候选清单（按“值得引入程度”排序）

> star / 最近提交为 GitHub API 快照（2026-11-06）；许可证以仓库 LICENSE 原文核验为准；测试覆盖除特别说明外均“未获取（未统计覆盖率）”；安全公告仅列出本次实际检索到的条目。

| 排名 | 仓库 | ★ | 最近提交 | 语言/栈 | 许可证（LICENSE 核验） | 维护状态 | 安全公告 | 核心源码目录 |
|---|---|---|---|---|---|---|---|---|
| 1 | https://github.com/asg017/sqlite-vec | 8,235 | 2026-05-18 | C（SQLite 扩展）+ Python/Node 绑定 | **Apache-2.0 与 MIT 双许可**（LICENSE-APACHE / LICENSE-MIT） | 活跃但节奏慢；最新 tag `v0.1.10-alpha.4`，仍是 alpha；open issues 249 | 未获取（未检索到公开 CVE/GHSA） | 仓库根 C 扩展源码（`sqlite-vec*`）、`bindings/`、`benchmarks/`、`examples/` |
| 2 | https://github.com/xhluca/bm25s | 1,822 | 2026-11-02 | Python（NumPy/Numba） | **MIT** | 活跃 | 未获取 | `bm25s/`（检索/分词/索引实现），`tests/` |
| 3 | https://github.com/PrithivirajDamodaran/FlashRank | 1,014 | 2026-07-11 | Python（ONNX Runtime） | **Apache-2.0** | 低频维护（约 4 个月未推送），功能型小库 | 未获取 | `flashrank/`（Ranker/ModelCache），`tests/`（未逐目录核验） |
| 4 | https://github.com/feyninc/chonkie（原 `chonkie-inc/chonkie`） | 4,818 | 2026-10-31 | Python | **MIT** | 活跃；v1.7.0 | 未获取 | `src/chonkie/{chunker,refinery,embeddings,handshakes,pipeline,types,utils}`、`tests/`（来自 pyproject 原文） |
| 5 | https://github.com/lancedb/lancedb | 11,843 | 2026-11-04 | Rust + Python/Node/Java 绑定 | **Apache-2.0** | 活跃 | 未获取 | 多语言 monorepo（`python/`、`rust/` 等，未逐目录核验） |
| 6 | https://github.com/unum-cloud/USearch | 4,370 | 2026-10-31 | C++（多语言绑定） | **Apache-2.0** | 活跃 | 未获取 | C++ 核心 + `python/`、`javascript/` 等绑定（未逐目录核验） |
| 7 | https://github.com/getzep/graphiti | 20,375 | 2025-11-21 | Python | **Apache-2.0** | **API 显示约 12 个月未推送，需复核**（疑似维护节奏变化或迁移） | 未获取 | `graphiti_core/`（nodes/edges/search/driver/prompts，未逐目录核验）、`tests/` |
| 8 | https://github.com/docling-project/docling | 70,096 | 2026-11-04 | Python | **MIT** | 非常活跃（每周多次） | 未获取 | `docling/`（解析/版面/OCR 后端）、`tests/` |
| 9 | https://github.com/HKUDS/LightRAG | 24,119 | 2025-11-22 | Python | **MIT** | API 显示约 12 个月未推送，需复核 | 未获取 | `lightrag/`（图构建/检索）、`tests/`（未逐目录核验） |
| 10 | https://github.com/topoteretes/cognee | 33,092 | 2026-11-05 | Python | **Apache-2.0** | 非常活跃；但 open issues 1,110 | 未获取 | `cognee/`（pipeline/记忆层）、`tests/`（未逐目录核验） |
| 11 | https://github.com/PrefectHQ/fastmcp（`jlowin/fastmcp` 已重定向） | 28,319 | 2026-11-05 | Python | **Apache-2.0** | 非常活跃；PyPI `fastmcp-slim` 最新 4.1.6（2026-11-03） | **有**：CVE-2026-32871（OpenAPIProvider 路径穿越，<3.2.0 修复）；PYSEC-2026-2474（OAuth Proxy 令牌跨 server 复用）；PYSEC-2026-1365（Windows 下 `fastmcp install cursor` 命令注入） | `src/fastmcp/`（server/client/auth/providers），`tests/` |
| 12 | https://github.com/modelcontextprotocol/python-sdk | 24,762 | 2026-11-03 | Python | **MIT** | 非常活跃 | **有**：CVE-2026-59951（stateful Streamable HTTP 会话无空闲过期/总量上限，影响 1.8.0–1.30.0 与 2.2.0） | `src/mcp/`（server/client/shared），`tests/` |
| 13 | https://github.com/mem0ai/mem0 | 36,607 | 2025-07-11 | Python | **Apache-2.0** | **API 显示约 16 个月未推送；维护状态存疑** | **有**：CVE-2026-59705（未授权记忆读写，CVSS 9.8）、CVE-2026-59706（未授权 config API 泄露 LLM Key + SSRF）、CVE-2026-31241（未授权 DELETE /memories）、CVE-2026-7597（向量库 `pickle.load`，≤1.0.11） | `mem0/`（memory/vector_stores/llms/embeddings），`tests/`（未逐目录核验） |
| — | https://github.com/kuzudb/kuzu | 3,521 | 2025-10-10 | C++ | **MIT** | **已归档（`archived: true`）** | 未获取 | — |

### 2.1 九问速答矩阵（Q1–Q4）

| 候选 | Q1 鎏灏是否已有同类（路径） | Q2 当前实现不足 | Q3 候选解决什么 | Q4 值得借鉴的具体模块 |
|---|---|---|---|---|
| sqlite-vec | 有：`src/knowledge/vector_store.py` | 纯 Python 余弦、JSON TEXT、启动全量载入、无索引/无过滤 | 10 万级以内的低延迟向量检索，且不引入服务 | `vec0` 虚拟表设计、距离函数、分块写入与事务边界 |
| bm25s | 无 | 只有单路向量，无词法召回 | 中文/专名/编号类查询的字面命中；混合检索基线 | 稀疏打分实现、分词器适配、索引序列化 |
| FlashRank | 无 | 无 rerank，top_k 直接进 context | 用小型 cross-encoder 对 20–50 条候选重排，成本和延迟可控 | ONNX 推理封装、模型缓存、批量打分接口 |
| chonkie | 部分：`src/knowledge/chunker.py` | 仅字符窗口，破坏语义边界 | 语义/递归/代码切分，减少上下文割裂 | `chunker/` 的递归与语义切分策略、`refinery/` 的 overlap/去重 |
| LanceDB | 有：同 `vector_store.py` | 规模上限低 | 10 万–100 万级嵌入式 ANN、标量过滤、版本化 | 列式存储 + IVF-PQ/HNSW 索引、过滤下推 |
| usearch | 有：同上 | 无 ANN | 进程内极小依赖 ANN，适合单机几万到几十万向量 | HNSW 参数（M/efConstruction）、保存/加载格式 |
| graphiti | 无 | 无图谱、无时序有效性 | 实体/关系抽取 + bi-temporal 时序图召回 | 边有效期（valid_at/invalid_at）建模、去重与冲突消解 |
| docling | 无（仅纯文本入库） | 无法解析 PDF/DOCX/PPTX/表格 | 结构化文档解析到 Markdown/JSON，带版面与表格 | `docling/` 的 DoclingDocument 中间表示、版面/表格后处理 |
| LightRAG | 无 | 无图 RAG | 实体图 + 局部/全局检索范式 | 图构建 prompt、双层检索（local/global） |
| cognee | 部分：记忆模块 | 记忆无 consolidation、无图 | “记忆→图→检索”的数据管道编排思路 | pipeline/task 编排与幂等重跑 |
| FastMCP | 有：`src/adapters/mcp/mcp_adapter.py` | 未接主链路、无审批/审计/投影登记 | 标准化 server/client 生命周期与 transport | 生命周期管理、auth provider、客户端 session 处理 |
| python-sdk | 有（作为 FastMCP 底座） | 版本未纳入 CVE 门禁 | 低层协议正确性、会话治理 | session manager 过期策略 |
| mem0 | 有：`src/knowledge/memory.py` 的 `Mem0Backend` | 依赖 OpenAI+Qdrant；失败静默回退；安全记录差 | （参考）ADD/UPDATE/DELETE 记忆决策逻辑 | 记忆冲突消解与去重策略（仅借鉴思路，不建议启用） |
| kuzu | 无 | — | 嵌入式图库 | 不引入（已归档） |

### 2.2 九问速答矩阵（Q5–Q9）

| 候选 | Q5 能否独立封装成模块 | Q6 是否破坏现有接口/数据结构 | Q7 许可证允许使用与修改（GPL/AGPL 判定） | Q8 安全风险与维护成本 | Q9 集成/回归测试怎么写 |
|---|---|---|---|---|---|
| sqlite-vec | 能，作为 `VectorStore` 的一个后端实现 | 否（保持 `add/search/delete` 契约）；需新增 embedding BLOB 迁移 | 允许。Apache-2.0/MIT 双许可，**非 GPL/AGPL**，可商用闭源分发 | 低依赖风险；alpha 版本 + 单维护者 + 249 open issues 是主要风险；需 Windows wheel 验证 | 同一数据集下与旧实现的 recall@k 对比；边界（空库/维度不符/删除后查询）；重启后持久化 |
| bm25s | 能，`HybridRetriever` 的词法分支 | 否；新增并列分支，不改原 `search` 返回结构 | 允许。MIT，非 GPL | 纯 Python + NumPy/Numba，供应链低风险；中文需自定义分词（默认英文分词） | 中英文用例、空查询、停用词、索引持久化与增量更新 |
| FlashRank | 能，`Reranker` 适配器，失败时退回原序 | 否；作为后置可选阶段 | 允许。Apache-2.0，非 GPL | 模型文件分发与 ONNX Runtime 体积；首次下载需离线可用的本地包 | 固定候选集下 NDCG/命中率对比；超时/模型缺失时优雅降级断言 |
| chonkie | 能，替换 `chunker.py` 内部策略 | 否；需保证 chunk 元数据结构兼容 | 允许。MIT，非 GPL | 依赖增多（可选 extras）；语义切分需 embedding，离线环境要预置模型 | 切分边界快照测试；chunk 数/长度分布断言；与入库链路的端到端 |
| LanceDB | 能，作为第二后端（规模阈值切换） | 否（接口契约不变）；需新增数据目录与迁移脚本 | 允许。Apache-2.0，非 GPL | 依赖体积较大（Rust wheel）；版本升级需回归读写兼容 | 同数据集 recall/延迟；过滤器与租户隔离；重启持久化；并发读写 |
| usearch | 能，纯索引库 | 否；需自行维护 id↔向量映射 | 允许。Apache-2.0，非 GPL | 索引文件损坏恢复、参数调优成本 | 增删改后召回一致性；索引损坏回退重建 |
| graphiti | 能（作为独立“图谱记忆”子系统） | 否，但需新增图数据库依赖与 LLM 抽取成本 | 允许。Apache-2.0，非 GPL | 实体抽取成本高（每文档多次 LLM 调用）；近 12 个月未推送需复核 | 抽取稳定性（固定文档对比三元组）；时序查询（历史时点）；幂等重复写入 |
| docling | 能，作为 `DocumentParser` 层 | 否；入库前新增解析阶段 | 允许。MIT，非 GPL | 依赖重（版面/OCR 模型），冷启动慢；需离线模型包 | PDF/DOCX/表格样例解析快照；损坏文件；大文件超时 |
| LightRAG | 能，但更像“参考实现”而非库 | 若整体替换会破坏现有 RAG 管线 | 允许。MIT，非 GPL | 代码体量大、耦合其存储层；引入等于换一套 RAG 框架 | 不建议整体集成，只做范式对照 |
| cognee | 难（框架级，带自身存储/编排） | 会 | 允许。Apache-2.0，非 GPL | issues 1,110，框架级替换成本高 | 不建议集成 |
| FastMCP | 已封装 | 否（已适配） | 允许。Apache-2.0，非 GPL | **有已知 CVE**；需版本门禁与升级流程 | 现有 `tests/test_mcp_adapter.py` 扩展：投影白名单、租户、超时、审计 |
| python-sdk | 间接依赖 | 否 | 允许。MIT，非 GPL | **有已知 CVE**（会话不回收）；需 pin 安全版本 | 依赖扫描 + 会话过期行为测试 |
| mem0 | 已封装（`Mem0Backend`） | 否，但默认路径改变行为 | 允许。Apache-2.0，非 GPL | **高风险**：多条未授权/SSRF/反序列化 CVE；近 16 个月未推送 | 若保留，仅允许显式开启 + 本地无服务模式；加“禁止默认启用”回归 |
| kuzu | — | — | 允许。MIT，非 GPL | 已归档，无维护 | 不引入 |

---

## 3. 最值得的深评（5 个）

### 3.1 sqlite-vec —— 单机向量检索的最优中间态

**仓库**：https://github.com/asg017/sqlite-vec｜★8,235｜最近提交 2026-05-18｜C + Python/Node 绑定｜LICENSE：Apache-2.0 + MIT 双许可（`LICENSE-APACHE`、`LICENSE-MIT`）｜最新 tag `v0.1.10-alpha.4`。

1. **是否已有同类**：有。`src/knowledge/vector_store.py` 已实现 `PersistentVectorStore`（SQLite 单表 + in-memory dict + 纯 Python 余弦）。
2. **当前不足**：启动全量载入；embedding JSON TEXT 导致解析与内存放大；O(n·d) 解释器循环；无法按租户/文档过滤后再检索；无索引结构。
3. **能解决什么**：同文件内建 `vec0` 虚拟表，把距离计算下沉到 C/SIMD；检索可带 `WHERE` 过滤；查询复杂度与内存占用的量级都下降，且**不引入任何服务进程**。
4. **值得借鉴的源码**：`vec0` 虚拟表与分块（chunk）存储设计、距离函数实现、事务内批量写入方式、`bindings/` 里的 Python 绑定生命周期管理。
5. **能否独立封装**：能。建议新增 `SQLiteVecStore` 实现同一 `VectorStore` 协议，保留现有 `PersistentVectorStore` 作为回退；由配置项 `KNOWLEDGE_VECTOR_BACKEND=sqlite_vec|python` 切换。
6. **是否破坏接口**：不破坏。需一次性迁移：`embedding TEXT(JSON)` → `BLOB(float32)`，并保留 `dim/document_id/tenant_id` 列；迁移脚本必须幂等且可回滚。
7. **许可证**：Apache-2.0 与 MIT 双许可，允许使用、修改、闭源分发；**不是 GPL/AGPL**，不触发传染。
8. **安全与维护成本**：无网络面、无凭据；风险集中在“alpha 版本 + 单维护者 + 249 open issues”。Windows 下需验证官方 wheel 是否可用，否则要本地编译 C 扩展（这会让“一键启动”变复杂）。因此建议**特性开关 + 失败回退**，不能成为唯一路径。
9. **测试**：在 `tests/knowledge/` 增加：同一数据集下新旧后端 recall@k 对比；维度不符、空库、删除后查询；进程重启后持久化；租户过滤不串数据；迁移脚本双向（upgrade/downgrade）。

**与 LanceDB 的取舍**：10 万条以内 sqlite-vec 更划算（零新增文件格式、零进程）；超过 10 万或需要强过滤/版本化时，LanceDB（Apache-2.0，Rust，嵌入式）更合适，但依赖体积与升级成本更高。

### 3.2 bm25s —— 混合检索里最便宜的那一半

**仓库**：https://github.com/xhluca/bm25s｜★1,822｜最近提交 2026-11-02｜Python（NumPy/Numba）｜LICENSE 原文：MIT。

1. **是否已有同类**：无。全仓无 BM25/FTS 命中，检索只有向量单路。
2. **当前不足**：专有名词、产品编号、错误码、中文短查询容易召回失败；阈值以下的“字面完全匹配”会被漏掉。
3. **能解决什么**：与向量结果做 RRF/加权融合，显著提升“精确词”类查询的命中率；索引可落盘，重启无需重建。
4. **值得借鉴的源码**：稀疏打分主体、索引序列化格式、批量 query 的向量化实现。
5. **能否独立封装**：能。作为 `HybridRetriever` 的词法分支，与 `Retriever` 并列。
6. **是否破坏接口**：不破坏；融合后仍返回 `RetrievalResult` 列表，仅增加 `score_components`（可选字段）。
7. **许可证**：MIT，允许商用与修改；非 GPL/AGPL。
8. **安全与维护成本**：纯本地计算，无外发；主要成本是中文分词（自带分词偏英文，需接 jieba/tokie 之类的本地分词器）。
9. **测试**：中英文混合语料；空查询/全停用词；索引持久化后结果一致；融合权重边界（0/1）；与纯向量结果对比的固定快照。

> 零依赖替代：SQLite 自带 FTS5 也能做 BM25（`bm25()` 辅助函数），不改依赖、不增包，适合作为第一步；bm25s 适合作为第二步（更强的分词与性能）。

### 3.3 FlashRank —— 低成本 rerank

**仓库**：https://github.com/PrithivirajDamodaran/FlashRank｜★1,014｜最近提交 2026-07-11｜Python + ONNX Runtime｜LICENSE 原文：Apache-2.0。

1. **是否已有同类**：无。
2. **当前不足**：`Retriever` 只按余弦阈值截断，top_k 直接进 context；相似度高但语义不相关的 chunk 会挤占上下文。
3. **能解决什么**：对 20–50 条候选做 cross-encoder 重排，通常能把“答非所问”的条目压下去；模型小、CPU 可跑，适合单机。
4. **值得借鉴的源码**：ONNX 会话封装、模型缓存与懒加载、批量打分与截断策略。
5. **能否独立封装**：能。`Reranker` 适配器，作为 `RAGPipeline` 的可选后置阶段。
6. **是否破坏接口**：不破坏；失败/超时必须**原序返回**并记录指标，不能阻断检索。
7. **许可证**：Apache-2.0，允许使用与修改；非 GPL/AGPL。注意：**模型权重有独立许可证**，需逐个核对（本次未获取）。
8. **安全与维护成本**：无网络面；成本在模型体积与首次加载延迟；项目近 4 个月未推送，属“功能稳定型”小库而非高速迭代库。
9. **测试**：固定候选集下的 NDCG/命中率对比；模型缺失时降级路径；超时；空候选；多语言样例。

### 3.4 chonkie —— 把切分从“字符窗口”升级为“结构感知”

**仓库**：https://github.com/feyninc/chonkie（原 `chonkie-inc/chonkie`）｜★4,818｜最近提交 2026-10-31｜Python｜LICENSE 原文：MIT｜版本 1.7.0。
**源码结构（来自其 `pyproject.toml` 原文）**：`src/chonkie/{chunker,types,embeddings,utils,refinery,genie,handshakes,porters,cloud,experimental,fetcher,chef,pipeline,cli,api}`，`testpaths = ["tests"]`。

1. **是否已有同类**：部分。`src/knowledge/chunker.py` 已做字符窗口 + overlap。
2. **当前不足**：固定窗口切断句子/表格/代码块；overlap 造成重复上下文；无按 token 计量的稳定切分。
3. **能解决什么**：递归/语义/代码切分，按 token 控制长度，refinery 做去重与 overlap 治理；直接提升召回与上下文质量。
4. **值得借鉴的源码**：`chunker/`（递归与语义切分）、`refinery/`（overlap/去重/嵌入净化）、`types/`（chunk 数据模型）。
5. **能否独立封装**：能。作为 `Chunker` 策略实现，保留现有字符切分为默认回退。
6. **是否破坏接口**：需保证 chunk 结构与现有入库字段（`content/metadata/index`）兼容；否则会影响 `documents.py` 与已有测试快照。
7. **许可证**：MIT，允许商用与修改；非 GPL/AGPL。
8. **安全与维护成本**：核心依赖 `numpy/tqdm/httpx/tokie`；语义切分需 embedding（离线环境要预置模型）。可选 extras 很多，**只装需要的**，避免把 Chroma/Qdrant 等客户端带进来。
9. **测试**：切分边界快照；chunk 数/长度分布；中文与代码样例；与入库→检索的端到端；旧切分结果的兼容回归。

### 3.5 graphiti —— 时序知识图谱的正确姿势（但先别接）

**仓库**：https://github.com/getzep/graphiti｜★20,375｜最近提交 2025-11-21（API 显示约 12 个月未推送，需复核）｜Python｜LICENSE 原文：Apache-2.0。

1. **是否已有同类**：无。`src/ai/memory/semantic_network.py` 只是进程内轻量图，不是知识图谱。
2. **当前不足**：记忆是“文本条目 + tier”，没有实体、关系、时间有效性；同一事实的新旧版本无法区分。
3. **能解决什么**：实体/关系抽取 + **bi-temporal**（事件发生时间 vs 系统记录时间）建模，能回答“当时是什么状态”“这条何时失效”。
4. **值得借鉴的源码**：边的 `valid_at/invalid_at` 建模、实体去重与冲突消解、增量 episode 写入流程、混合检索（语义+BM25+图遍历）。
5. **能否独立封装**：能，但代价高：需要图数据库（其默认依赖 Neo4j 一类服务），违背“无 Docker 依赖”。若要做，必须换嵌入式图存储（如 SQLite 关系表自建，或已验证的嵌入式图库）。
6. **是否破坏接口**：不破坏现有记忆 API，但会新增一整套写入/抽取链路与成本模型。
7. **许可证**：Apache-2.0；非 GPL/AGPL。
8. **安全与维护成本**：每次写入触发多次 LLM 抽取，成本与延迟显著；近 12 个月未推送是**硬阻塞项**，需先确认项目是否迁移。
9. **测试**：固定文档的三元组抽取稳定性；同一事实两次写入的幂等；时序查询（指定历史时点）；删除/失效传播。

**本路建议**：本期只做“设计借鉴”，不引入依赖；先把记忆的 `valid_from/valid_to`、`entity`、`relation` 字段在现有 SQLite 记忆表里预留，等规模与需求明确后再决定是否上图库。

---

## 4. 明确不建议引入的（及原因）

| 对象 | 结论 | 原因（基于本次核验） |
|---|---|---|
| `kuzudb/kuzu` | **不引入** | 仓库 `archived: true`，最后推送 2025-10-10；归档项目不能作为新依赖。 |
| `mem0ai/mem0` 作为默认记忆后端 | **不建议默认启用** | ① 安全记录差：CVE-2026-59705（未授权读写，9.8）、CVE-2026-59706（未授权 config + SSRF + 明文 Key）、CVE-2026-31241（未授权删除）、CVE-2026-7597（`pickle.load`）；② GitHub API 显示约 16 个月未推送，维护存疑；③ 现有 `Mem0Backend` 默认配置硬绑 OpenAI + Qdrant，单机离线必然失败并静默回退。 |
| Chroma / Qdrant Server / Milvus / Weaviate 作为核心存储 | **本期不引入** | 都引入独立服务或容器，与“单机一键启动、无 Docker 依赖”冲突；10 万条以内收益不抵复杂度。仅当 >100 万条或多租户高并发时作为条件触发项。 |
| LangChain / LlamaIndex / Haystack / RAGFlow / Cognee 作为 RAG 主框架 | **不引入** | 会替换现有 `knowledge/` 分层与 API 契约，违反“最小必要修改、优先复用现有模块”；且依赖树与维护面显著扩大。可借鉴范式，不引入框架。 |
| LightRAG 整体集成 | **不引入，仅作范式参考** | 自带存储与管线，整体接入等于换一套 RAG；MIT 许可没问题，但改造成本远高于收益。 |
| `sqlite-vss` | **不引入** | 已被 sqlite-vec 取代（上游明确推荐迁移）；本次未单独核验其归档状态，属“已知替代关系”判断。 |
| `fastmcp install cursor` 等 CLI 安装子命令 | **禁止在生产机使用** | PYSEC-2026-1365：Windows 下 `server_name` 可注入任意命令。 |

---

## 5. 未验证项清单（不得当作结论使用）

1. **Windows wheel 可用性**：`sqlite-vec`、`LanceDB`、`usearch` 在本机 Windows + 当前 Python 版本下是否有可用 wheel，未实测；若需本地编译，会破坏“一键启动”。
2. **性能数字**：本报告 §0.1 的延迟/内存为**量级估算**；未在本仓库数据集上做基准测试。sqlite-vec 的 395ms@1M×512 来自社区 benchmark（非官方、非本项目实测）。
3. **测试覆盖率**：除 chonkie（`testpaths=["tests"]`）外，各候选的覆盖率、CI 状态未获取。
4. **安全公告完整性**：仅检索到上文列出的 CVE/GHSA；未对每个仓库逐条遍历 GitHub Security Advisories 与 OSV 全量数据。FastMCP 的 PYSEC-2026-2474 修复版本未获取；mcp SDK 的 CVE-2026-59951 是否已修复于当前锁定的 `mcp` 版本未核验。
5. **`fastmcp-slim` 供应链**：4.0.3 与最新 4.1.6 的差异、wheel 签名/哈希、发布者身份未核验。
6. **graphiti / LightRAG / mem0 的近 12–16 个月“未推送”**：仅为 GitHub API `pushed_at` 事实，是否迁移到新仓库/私有仓库未获取。
7. **Graphiti 的默认图数据库依赖**：未逐目录核验其 driver 支持矩阵（是否可在无 Neo4j 的嵌入式模式下运行）。
8. **Docling 的模型与离线部署**：版面/OCR 模型体积、离线缓存路径、冷启动耗时未获取。
9. **各 Reranker 模型权重许可证**：仅核验了代码仓库 LICENSE（Apache-2.0），模型权重许可未逐一核对。
10. **真实召回质量基线**：本项目当前没有 recall@k / NDCG 基线，无法量化“引入后提升多少”；需先补评测集。
11. **MCP 外部 server 实测**：`configs/mcp/servers.json` 为空，未做任何真实第三方 MCP server 的端到端验证（stdio 与 HTTP 均未实测）。
12. **记忆召回真实链路**：未运行服务验证 AI Employee 在生产配置下到底走 Mem0 还是回退；本报告结论来自静态源码（`src/knowledge/memory.py` 的 `_default_config` 与 `create_memory_manager`）。

---

## 6. 建议的最小落地顺序（供总策划参考，不在本文件执行）

1. **记忆召回止血**（P0）：把 `create_memory_manager` 的默认后端改为“MemoryKernel/SQLite 优先”，Mem0 仅在显式配置开启；删除 `print` 静默回退，改为结构化日志 + 显式状态位；`KnowledgeSearchTool` 接真实检索；`MemoryTool` 复用持久内核。
2. **数据层改造**（P0）：embedding 改 float32 BLOB + 归一化 + numpy 批量余弦；补齐文档删除/重建索引 API。
3. **混合检索**（P1）：FTS5 或 bm25s + 向量 RRF 融合；加 recall@k 评测集。
4. **Rerank**（P2）：FlashRank 作为可选后置阶段，失败原序返回。
5. **切分与解析**（P2）：chonkie 替换字符切分；docling 作为可选解析器（离线模型包）。
6. **向量后端升级**（P2，按阈值触发）：>1 万条启用 sqlite-vec 特性开关；>10 万条评估 LanceDB。
7. **MCP 主链路**（P1，安全门禁先行）：按 §0.2 的 9 条硬门槛落地后再打开开关。
8. **时序图谱**（P3）：先预留 `valid_from/valid_to/entity` 字段，不引入图库。
