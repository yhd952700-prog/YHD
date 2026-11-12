# 记忆与知识系统域 · 只读深度体检（MEM）

- 审计对象：`d:\LiuHao-AI-OS`（当前工作树）
- 审计日期：2026-11-08
- 审计范围：短期会话记忆、长期记忆（内核/记忆页/画像）、读写一致性、纠错与删除、知识库 RAG 全链路、检索准确度、更新/重建索引、重复入库与数据污染
- 明确排除：`src/knowledge/**`、`src/api/routes/knowledge.py`、`src/gateway/knowledge_context.py` 只读不写（其他线可能仍在改动）；未改任何代码、未 commit、未 `git stash`
- 计数口径：一次实验只算一次；检索指标按“20 组独立问题—文档对”定义，未做交叉标注扩充

## 0. 执行边界与污染声明（先读）

1. **真实进程、真实请求**：在独立数据目录 `D:\cache\temp\liuhao-memkb-audit-20261108000636`、独立端口 `8899` 上，由同一 Python 托管进程拉起真实 `uvicorn`，用 `httpx` 发真实 HTTP 请求；随后按会话轮次重启进程复验持久化。
2. **真实依赖**：检索主实验使用本机 Ollama `nomic-embed-text`（768 维）；另起一个 OpenAI 兼容的 8 维 stub（端口 8898）专门做“向量维度漂移”实验。所有向量均落盘在隔离的 `knowledge_vectors.sqlite3`，实验结束核验库内维度分布：27 条 768 维 + 1 条 8 维。
3. **隔离变量**：`LIUHAO_DATA_DIR`、`LIUHAO_WORKSPACE_ROOT`、`CONVERSATION_DB_PATH`、`AUDIT_DB_PATH`、`MEMORY_DB_PATH`、`LIUHAO_KNOWLEDGE_DB`、`LIUHAO_HUMAN_IDENTITIES_FILE`、`LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY`、`LIUHAO_EMBEDDING_PROVIDER/MODEL` 全部指向隔离目录；真实安装 `D:\LiuHao` 未启停、未读写。
4. **并发改码披露**：审计期间工作树存在其他线未提交的 `M src/gateway/main.py`，00:36 后该文件一度启动报 `NameError: name 'asyncio' is not defined`（main.py:1642 调用处），使部分 HTTP 用例转静态判断/BLOCKED；该文件当前已重新出现 `import asyncio`（main.py:11、36），不属于本域缺陷，也未由本域修改。
5. **密钥**：本域源码未发现硬编码明文密钥；实验使用的 JWT 签名密钥与身份完整性密钥仅存在于隔离进程的环境变量（类型：HMAC 共享密钥 / JWT HS256 密钥），报告不落值。仓库内若存在同类密钥文件，本域未主动扫描，不在本次结论内。
6. **行号基准**：本报告所有 `文件:行号` 均指 2026-11-08 审计时的**工作树快照**。当时 `src/knowledge/**`、`src/api/routes/knowledge.py`、`src/gateway/knowledge_context.py`、`src/ai/conversation_store.py`、`src/ai/liuhao.py`、`src/ai/personal_context.py`、`src/gateway/account_memory.py` 等均为 `M`（其他线未提交改动）。若其他线继续改动，行号可能漂移，复现请以函数名/代码片段定位。

## 1. 方法与结论口径

- 静态：逐文件读实现，定位持久化路径、写入触发、事务边界、过滤条件，给出 `文件:行号`。
- 动态：真实上传/检索/删除/并发/越权请求 + 直接查 SQLite 行数核验（`knowledge_vectors` / `knowledge_document_hashes` / `knowledge_fts` / `account_memory_entries` / `memory_entries`）。
- 指标：**Recall@5** = 期望文档出现在返回前 5；**Top-3 命中率** = 期望文档排名 ≤3。题目与文档均为人工设计，含同义词、错别字、中文分词边界、专业术语、代码片段五类，各 3–5 题。
- 只写到证据能支撑为止；未实测的步骤明确标 `BLOCKED`/`静态判断`，不写“大概准确”。

## 2. 结论摘要

| 等级 | 数量 | 说明 |
|---|---|---|
| P0 | 1 | 中文账号/文件名坍缩成同一命名空间，导致跨账号覆盖与不可见 |
| P1 | 8 | 记忆删除不彻底、跨账号记忆泄漏、无纠错接口、短期意图丢失、维度漂移静默、会话检索越权、LLM 无确认写事实 |
| P2 | 7 | 去重/冲突、写穿静默失败、同内容吞文件名、无重建索引、闸门误判、同名覆盖、迁移归属 |
| P3 | 4 | TTL 残留、Unicode 归一、status 契约、知识库静默降级 |
| P4 | 0 | — |

**总体判断：知识库“写入—删除—并发”这条主干是干净的；真正危险的是“标识符空间”和“记忆的生命周期”。** 中文账号/文件名在进入 ID 时被正则压成 `account`/`-`，不同账号会写进同一个文档主键；记忆页没有删除/纠正接口，画像删除又清不掉内核里的 PERSISTENT 副本；内核对 L1 检索不按账号过滤。检索排序本身可用（17/20），但相关度闸门对 OOD 有 2/6 误放行。

## 3. 逐条发现

### MEM-001 · 知识库命名空间（账号/文件名） · P0

- 文件:行号
  - `src/knowledge/documents.py:25`（`_ID_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")`）
  - `src/knowledge/documents.py:43-46`（`_safe_document_id`：非 ASCII 连续串整体替换为 `-`）
  - `src/knowledge/documents.py:55-58`（`_owner_token`：非 ASCII owner → 空 → `"account"`）
  - `src/knowledge/documents.py:61-73`（`scoped_document_id`：`f"{_owner_token(owner)}--" + base`）
- 表现：中文账号名和中文文件名在进入 `doc_id` 前被正则剥空。账号 `测试甲`、`测试乙` 的 owner token 都变成 `account`；文件名 `文档.txt` 变成 `---.txt`。两个**互不相关**的账号上传同名文件时，落到同一个主键 `account---.txt`，后写覆盖先写；先写账号随后检索不到自己的内容。
- 是否复现：**已复现**。隔离实例真实建号、激活、登录、上传、检索：
  - `upload_测试甲` → `{"document_id":"account---.txt","chunks":1,"vectors":26,...}`
  - `upload_测试乙` → `{"document_id":"account---.txt","chunks":1,"vectors":26,...}`（同 id，未新增向量）
  - `search_测试甲` → `{"ids":[],"own_marker":false}`；`search_测试乙` → `{"ids":["account---.txt#0"],"texts":["乙方独占知识：部署窗口周三凌晨。"],"own_marker":true}`
- 根因：文档 ID 只允许 `[A-Za-z0-9_.:-]`，CJK 被 `+` 整体吞掉；`_ID_SAFE` 又用于 owner 前缀，导致所有中文账号共享 token。`scoped_document_id` 的 docstring 声称“避免两个账号同名文件互相覆盖”，实现与该承诺相反。
- 影响面：所有中文账号、所有中文文件名；知识库写入、检索、删除、去重均受影响；根账号“子树聚合”也会看到被覆盖后的单一版本。
- 安全或数据风险：**高**。跨租户数据覆盖（数据丢失）+ 越权可读（B 能看到 A 曾写入后被覆盖的条目只属于 B，但共享主键使所有权判定失真）；删除一个账号的 `account---.txt` 会连带删掉另一个账号的同名文档。
- 修复方案：不要用正则“替换”生成命名空间。对 owner 与文件名分别使用 `hashlib.sha256(normalized_owner)[:16]` 生成稳定 token，文件名保留原文作为 `display_name` 元数据；`doc_id` 采用 `<owner_hash>--<doc_uuid>`，杜绝同名冲突。若必须保留可读 ID，至少对 CJK 做 URL-safe base64 或 hex 转义，并在 `scoped_document_id` 里断言 token 非空、非 `account` 兜底。
- 需要改的文件：`src/knowledge/documents.py`（`_ID_SAFE`/`_safe_document_id`/`_owner_token`/`scoped_document_id`/`ingest` 的元数据）；迁移脚本需重写既有 `account---*.txt`、`auditroot---*.txt` 的 doc_id 并同步 vectors/hashes/fts。
- 验证方法：建两个中文账号 A/B，各自上传同名 `文档.txt`，断言两个 `document_id` 不同、`GET /documents` 各见 1 条、互相检索不到对方、删除 A 不影响 B；再上传 `报告.txt` 与 `并发.txt`，断言 doc_id 不相等。
- 等级：P0
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`_owner_token` 改为「可读 ASCII 前缀 + NFKC 后 sha256[:16]」，`_safe_document_id` 对含 CJK 的名字追加稳定摘要。证据：`测试甲`/`测试乙` 同名 `同名.txt` 得到 `acct-5c06c3aa64b338e1--txt-30debf3ee25f2035` 与 `acct-36ac31af3821880a--txt-30debf3ee25f2035`，各自检索仅命中自己；`migrate_legacy_document_ids` 对旧 `account---*` 迁移 `documents_before=2/documents_after=2/count_conserved=true/migrated=2/conflicts=0/failed=0`，二次运行 `migrated=0`（可重入），每文档独立事务可回滚。迁移接口 `POST /v1/knowledge/maintenance/migrate-document-ids`（默认 dry_run）并写审计。

### MEM-002 · 长期记忆删除 · 画像清了、内核没清 · P1

- 文件:行号
  - `src/ai/personal_context.py:476-489`（`clear()` 只 `DELETE FROM profile_facts/profile_preferences/profile_lists`）
  - `src/ai/personal_context.py:186-200`（`_sync_to_memory` 写穿 `MemoryTier.PERSISTENT, scope=L1`，key=`profile:{principal}:{key}`）
  - `src/ai/tools/kernel_tools.py:52-75`（`search_memory` 走全局 L1 过滤）
- 表现：`DELETE /v1/profile` 返回成功后，画像 SQLite 行已删，但 memory kernel 里的 PERSISTENT 事实仍在；聊天工具 `search_memory` 仍能把它检索出来，且永不过期。
- 是否复现：**已复现**。删除后直接 `recall` 内核：
  - `DELETE /v1/profile` → 200
  - `kernel recall("profile:user-auditroot:fact:hometown")` → 仍返回 `杭州`，`tier=persistent`、`expires_at=None`
- 根因：画像层与内核层是两套存储，`clear()` 只清前者；写穿没有对应的“删穿”。内核设计上又“刻意不提供单条 update/delete”（见 `src/kernels/memory/__init__.py` 模块 docstring）。
- 影响面：所有使用 `/v1/profile` 或 `personal_*` 工具写入的用户；删除/更正后旧事实继续参与对话与检索。
- 安全或数据风险：**中高**。用户行使“删除我的数据”却未真正删除，构成隐私合规风险；两个值会同时存在（见 MEM-010）。
- 修复方案：`clear()` 在清 SQLite 后，按 `profile:{principal}:` 前缀枚举内核条目并删除（或给 MemoryKernel 增加 `delete_by_key_prefix`）；`/v1/profile` DELETE 返回体应报告 `kernel_deleted` 数量，失败则整体 500 而不是假成功。
- 需要改的文件：`src/ai/personal_context.py`、`src/kernels/memory/__init__.py`、`src/gateway/profile.py`
- 验证方法：写入 fact → DELETE profile → 断言 SQLite 0 行、内核 `recall` 0 条、`search_memory("老家")` 返回“无匹配记忆”。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`MemoryKernel.delete_by_key_prefix` + `PersonalContextManager.clear()` 事务内先清 SQLite、再删穿内核 `profile:{principal}:`，成功才 commit；`DELETE /v1/profile` 返回 `kernel_deleted`。证据：写 fact→DELETE→`kernel_deleted=2`、内核 `recall` 为 `None`、SQLite `sqlite_rows=0`；单条 `DELETE /profile/facts/{key}` 同样删穿。

### MEM-003 · 长期记忆检索 · 全局 L1 不按账号过滤 · P1

- 文件:行号
  - `src/ai/tools/kernel_tools.py:18`（`from ...kernels.memory import MemoryScope, filter_memory`）
  - `src/ai/tools/kernel_tools.py:52-75`（`search_memory` docstring 写“本主体”，实现调用 `filter_memory(MemoryScope.L1)`，**未传 principal**）
  - `src/kernels/memory/__init__.py:381-405`（`scope_filter` 只按 scope 层级与过期时间过滤，无 owner/principal 维度）
  - `src/kernels/memory/__init__.py:276`（`key_hash = f"{tier.value}:{key}"`，key 本身不含账号维度）
- 表现：任一账号在对话里触发 `search_memory`，得到的是**全机所有账号**的 L1 记忆，包括 `profile:<其他账号>:fact:*`。
- 是否复现：**已复现**。隔离库内直接调用同款过滤：
  - `scope_filter(L1)` 同时返回
    `profile:user-auditmember:fact:secret_project` 与 `profile:user-auditroot:fact:hometown`
  - 而 `user-auditmember` 与 `auditroot` 是两个不同主体。
- 根因：内核把“作用域 L0–L7”当成隔离维度，但 L1 是全局层级而非租户；账号隔离只靠调用方在 key 里手工拼 principal，检索端没有对称校验。
- 影响面：所有启用记忆工具的多账号部署；员工/成员私密事实可被其他账号的助手读出。
- 安全或数据风险：**高**（跨租户隐私泄漏）。记忆页 `/v1/memory/entries` 的隔离是过的（见 §5），但聊天工具绕过了它。
- 修复方案：`search_memory` 读取时按 `key` 前缀 `profile:{principal}:` / `turn:{principal}:` 或 tags 里的 `agent:liuhao-{principal}-` 做二次过滤；更好的做法是 MemoryKernel 增加 `owner` 字段并在 `scope_filter` 内强制过滤，principal 由工具闭包注入。
- 需要改的文件：`src/ai/tools/kernel_tools.py`、`src/kernels/memory/__init__.py`、`src/kernels/memory/store.py`（如需加列）
- 验证方法：A、B 两账号各写一条 PERSISTENT fact，A 调 `search_memory("机密")`，断言只返回 A 的条目；再断言 B 的 fact 在 A 的 `recall` 中不可见。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`search_memory` 默认 `accounts={当前账号}`，按 `profile:{principal}:` / `turn:liuhao-{account}-` / `agent:liuhao-*` 正向归属白名单过滤，无归属标记一律不可见；仅主账号显式 `scope="subtree"` 才聚合。证据：`visible_alice=['A 的对话事实','A 的机密']`、`visible_bob=['B 的对话事实','B 的机密']`，互不返回。

### MEM-004 · 长期记忆（记忆页） · 无删除/纠正接口 · P1

- 文件:行号
  - `src/gateway/account_memory.py:258-283`（`GET /v1/memory/entries`）
  - `src/gateway/account_memory.py:286-301`（`GET /v1/memory/entries/{id}`）
  - `src/gateway/account_memory.py:304-335`（`POST /v1/memory/entries`，直接 INSERT）
  - 同文件无 `DELETE`/`PUT`/`PATCH` 路由（全文件仅 GET/POST + custody/adopt/export）
- 表现：记忆页只能“看”和“追加”，用户无法删除一条错误记忆，也无法改值；模型记错一次就永久留在列表里（该表无 TTL/过期字段）。
- 是否复现：**已复现**。真实 HTTP：
  - `DELETE /v1/memory/entries/{id}` → `405 {"detail":"Method Not Allowed"}`
  - `PUT` → 405；`PATCH` → 405
  - 同一文本 POST 两次 → 列表 2 条（见 MEM-010）
- 根因：产品口径只实现了“记录/阅读/导出”，未实现“更正/遗忘”生命周期。
- 影响面：所有使用记忆页的用户；错误记忆无法自助清理，只有整库迁移/手改 SQLite 才能消。
- 安全或数据风险：中。错误事实长期驻留；用户删除权缺失。
- 修复方案：增加 `DELETE /v1/memory/entries/{id}`（owner 校验，非本人 404，与读一致）与 `PATCH /v1/memory/entries/{id}`（只允许本人、写审计）；删除需同时清理任何以该 entry 为来源的派生记忆（若有）。
- 需要改的文件：`src/gateway/account_memory.py`、前端记忆页（如有调用）、审计事件类型注册
- 验证方法：写入 → DELETE → 列表不再出现、`GET /entries/{id}` 404；他人 DELETE 返回 404；重启后仍不出现。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。新增 `PATCH /v1/memory/entries/{id}`（只改正文/来源/标签、不改归属、写审计 `memory.update`）与 `DELETE /v1/memory/entries/{id}`（非 owner 统一 404、连带删除 `origin:{id}` 派生副本、审计 `memory.delete`）；控制台记忆页增加「纠正/删除」入口（二次确认）。证据：PATCH 改文成功、非本人 PATCH 404、DELETE 后库内行数 0。

### MEM-005 · 短期记忆 · 上下文裁剪丢早期意图 · P1

- 文件:行号
  - `src/ai/liuhao.py:81`（`CONTEXT_SOURCE_MESSAGES = 240`）
  - `src/ai/liuhao.py:599-601`（`load_recent(self.principal, CONTEXT_SOURCE_MESSAGES)`）
  - `src/ai/conversation_store.py:539-548`（`load_recent ... ORDER BY seq DESC LIMIT ?`，只取最近 N 条）
  - `src/ai/context_budget.py:44`（`SUMMARY_MAX_CHARS = 800`）
  - `src/ai/context_budget.py:245-269`（`deterministic_summary`：每消息只取**首句**，超长截尾）
  - `src/ai/context_budget.py:292`（`trim_history`）
  - `src/ai/liuhao.py:1289`（实际裁剪调用点）
- 表现：会话超过 240 条后，**最早的消息不会被加载，也没有摘要**；即使进入摘要的消息，也只保留首句。用户的早期约束（预算、偏好、否定条件）因此消失。
- 是否复现：**已复现**（真实 `ConversationStore` + 真实 `trim_history`）：
  - 灌入 244 条消息，`loaded=240`、`dropped_messages=232`、`dropped_turns=116`
  - 早期事实“我要做一个个人记账工具。**预算只有三百元**。”中，摘要只留下首句：`budget_in_summary=false`、`budget_in_kept=false`
  - `summary_prefix="第2轮：请继续介绍通用编"`，`summary_len=470`，`second_sentence_retained=false`
  - `cap_oldest_fact=false`（最早事实完全不在 240 条窗口内）
- 根因：窗口是硬截断而非滚动摘要；`deterministic_summary` 以“每消息首句 + 800 字截尾”为压缩策略，信息损失不可控，且没有“已丢弃历史摘要”随会话持久化。
- 影响面：长会话用户；早期目标/预算/禁用词丢失后会给出自相矛盾的答复，用户往往感知为“助手失忆”。
- 安全或数据风险：中。若早期消息含拒绝指令/安全约束（“不要动生产库”），裁剪后会失效。
- 修复方案：把摘要持久化到会话行（每轮增量更新），并在 `load_recent` 时同时加载“历史摘要”作为系统消息；摘要改为“抽取约束/决策/未完成项”而非每消息首句；对用户显式约束打 tag 永不被裁（关键事实单独存储）。
- 需要改的文件：`src/ai/liuhao.py`、`src/ai/context_budget.py`、`src/ai/conversation_store.py`（schema 增加 summary 列）
- 验证方法：构造“第1轮设定预算三百元 + 后续 300 轮闲聊”，在第 301 轮问“我的预算是多少”，断言回答含 300；再断言 `load_recent` 返回带 `history_summary`。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。新增 `conversation_summaries`（按 principal 持久化、`last_seq` 增量滚动）+ `summarize_overflow`；`deterministic_summary` 改为「关键事实层（预算/金额/禁止/必须/截止等提示词逐句扫描，带 `[#seq]`）+ 对话要点层（每条首句）」，不再 800 字截尾丢事实。证据：摘要含 `【关键事实】[#1] 预算只有三百元`；灌入 301 轮后 `trim_history` 仍保留「预算」。

### MEM-006 · 知识库文件名 · 中文名坍缩，同账号互相覆盖 · P1

- 文件:行号
  - `src/knowledge/documents.py:25`（`_ID_SAFE`）
  - `src/knowledge/documents.py:43-46`（`_safe_document_id`)
  - `src/knowledge/documents.py:61-73`（`scoped_document_id`）
- 表现：同一账号上传 `报告.txt` 与 `并发.txt`，两个 `document_id` 都是 `auditroot---.txt`；第二次上传替换第一次，旧内容从检索中消失。
- 是否复现：**已复现**。两次真实上传返回：
  - `filename_conflict_ids = ["auditroot---.txt","auditroot---.txt"]`
  - `chunks` 只剩 `["报销规则版本B：上限两万元，旧规则作废。"]`
  - 检索“旧规则”不再返回版本 A 的正文
- 根因：与 MEM-001 同源——文件名不是唯一键，中文被整体替换；`replace_prefix` 语义是“同前缀全量替换”，两次上传天然命中同一前缀。
- 影响面：中文文件名用户；报告、合同、制度等多版本文件极易静默互相覆盖。
- 安全或数据风险：**高**（静默数据丢失，且无版本/无提示）。
- 修复方案：`doc_id` = `<owner_hash>--<sha256(原始文件名+内容 or 随机uuid)>`；把原始文件名放进 metadata `source/title`；同前缀替换仅允许“显式指定 doc_id 的更新”，按文件名上传时禁止覆盖或返回 409 + `existing_document_id`。
- 需要改的文件：`src/knowledge/documents.py`、`src/api/routes/knowledge.py`（上传响应/冲突码）
- 验证方法：同账号上传 `报告.txt`/`并发.txt`，断言两个 doc_id、两条记录、两份 chunks 均存在；同文件名不同内容上传，断言默认不覆盖（或显式覆盖时返回版本信息）。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线，与 MEM-001 同源）。文件名进入 `_safe_document_id` 时按「可读前缀 + 稳定 hash」区分，`报告.txt` 与 `并发.txt` 不再得到同一 id；同账号同名上传属于显式覆盖路径，现在返回 `overwritten/previous_chunks` 提示（见 MEM-015）。

### MEM-007 · 短期记忆检索 · 会话搜索跨账号前缀泄漏 · P1

- 文件:行号
  - `src/ai/conversation_store.py:731`（`scope_prefix = f"liuhao-{principal}-"`）
  - `src/ai/conversation_store.py:754-755`（`instr(t.principal_id, ?) = 1`，参数是前缀）
  - `src/ai/conversation_store.py:769-770`、`804-807`（降级扫描同样用 `instr(..., 前缀)=1`）
  - `src/gateway/chat.py:1486-1517`（`_search_in_scope`：单 owner 直接 `store.search(owners[0], ...)`，见 1493）
- 表现：`principal="prefix"` 的会话搜索会命中 `principal="prefix-child"` 的会话，因为 `principal_id` 拼接为 `liuhao-prefix-child-s1`，包含 `liuhao-prefix-`。返回片段直接带出他人对话正文。
- 是否复现：**已复现**（直接调用生产 `ConversationStore.search`，绕过被并发改坏的 main.py）：
  - `store.search("prefix", "紫罗兰")` → `total=2`
  - 命中 `session_id="child-s1"`，`snippet="受害者私密对话：暗号紫罗兰，预算三十万。"`
- 根因：用“字符串包含”做租户边界，而不是精确 `principal_id = ?`；账号名互为前缀时必然越界（`a`/`ab`、`root`/`root2`、`test`/`test-child` 都中招）。
- 影响面：所有存在前缀关系的账号；根账号与 `root2` 类命名尤其常见。
- 安全或数据风险：**高**（跨账号对话内容泄漏）。搜索是产品功能，不需要特殊权限即可触发。
- 修复方案：`principal_id` 按账号**精确等值**过滤；若历史行以 `liuhao-{principal}-{session}` 编码，应反解出 principal 后比较，或新增 `owner` 列并在写入时落库。子树聚合必须显式传 owner 列表（逐个 `=`），不得用前缀。
- 需要改的文件：`src/ai/conversation_store.py`（`search` 三处 where）、`src/gateway/chat.py`（`_search_in_scope`）
- 验证方法：建 `prefix` 与 `prefix-child` 两会话，`search("prefix","紫罗兰")` 断言只返回 `prefix` 自己的会话；再测 `root`/`root2`。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`conversation_turns` 新增 `owner_principal` 精确归属列，老库按已知账号最长匹配一次性回填；`search` 的 FTS 与降级扫描两条路径都改为 `owner_principal = ?`。证据：`prefix` 令牌只返回 `AAA`，`prefix-child` 只返回 `BBB`，无交叉命中。

### MEM-008 · RAG 向量化 · 维度漂移静默降级 · P1

- 文件:行号
  - `src/knowledge/vector_store.py:107-119`（`cosine_similarity` 用 `zip(left,right)`，长度不等时静默截断）
  - `src/knowledge/documents.py:281-317`（`status()` 只返回 `provider/model/vectors`，不校验维度与模型一致性；`message="知识库已启用"`）
  - `src/knowledge/documents.py:319-442`（`ingest` 不校验新向量维度是否与库内一致）
- 表现：切换 embedding 提供方/模型后，新向量维度与旧库不一致；入库成功、`/status` 仍 `enabled:true` 且称“知识库已启用”，检索用截断余弦算出无意义分数，聊天闸门静默弃答。
- 是否复现：**已复现**（8 维 stub 混入 768 维库）：
  - `/v1/knowledge/status` → `{"enabled":true,"provider":"openai","model":"stub-8","vectors":24,"reason":null,"message":"知识库已启用"}`
  - 入库 → `{"document_id":"auditroot--stub-8","chunks":1,"vectors":25,"deduplicated":false}`
  - 检索 → 得分 `[0.028158, 0.016393, 0.016129, 0.015873, 0.015658]`，8 维与 768 维混排且无告警
  - 直接计算：同维 `1.0000000000000002`；8 vs 768 = `0.10206207261596574`；4 vs 768 = `0.07216878364870323`，**均无异常**
- 根因：向量库没有“embedder 身份/维度”元数据与启动校验；`cosine_similarity` 对长度不匹配既不抛错也不告警；`status` 只探活 provider，不探活“库与模型是否匹配”。
- 影响面：任何换模型/换 provider/降级到 mock 的部署；旧向量仍被当作有效，召回质量断崖式下降但界面全绿。
- 安全或数据风险：中高。属于“静默错误”而非崩溃，用户会把“答非所问/拒答”归因于模型，难以排障。
- 修复方案：库级元数据记录 `embedding_model`、`dim`、`normalized`；启动/入库时维度不一致直接 `enabled:false` 并给出 `reason="embedding dimension mismatch: stored=768 live=8"`；`cosine_similarity` 长度不等时抛 `ValueError`（或显式返回 0 并计数告警）；`status` 增加 `embedding_consistent` 字段。
- 需要改的文件：`src/knowledge/vector_store.py`、`src/knowledge/documents.py`、`src/knowledge/embedding.py`、`src/api/routes/knowledge.py`（status 透传）
- 验证方法：768 维库中切到 8 维 provider，断言 `/status` 立即 `enabled:false` 且 `reason` 指明维度；检索返回空并由聊天层显示“知识库不可用（维度不一致）”。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。新增库级台账 `knowledge_index_meta`（provider/model/dim），`ingest` 前校验维度并拒绝混写；`cosine_similarity` 维度不等返回 `0.0` 并告警（不再 `zip` 静默截断）；`/status` 汇报 `index_model/index_dimension/dimensions`，不一致 `enabled:false` + 中文原因；补 `POST /maintenance/reindex`。证据：8 维库 `enabled=True/index_dimension=8` → 混入 4 维后 `enabled=False` 且原因含「维度不一致」→ reindex 后 `dimensions={'8':2}`、`enabled=True`。

### MEM-009 · 长期记忆写入 · LLM 无确认写事实（记忆投毒路径） · P1

- 文件:行号
  - `src/ai/tools/kernel_tools.py:185-196`（`personal_set_preference` 直接写画像+内核）
  - `src/ai/tools/kernel_tools.py:198-205`（`personal_add_fact`：`pcm.record_fact(principal, k, v)`，无用户确认、无置信度）
  - `src/ai/personal_context.py:265+`（`record_fact`，`source="explicit"`）
- 表现：模型在对话中听到“我老家在苏州”（含幻觉扩写）即可调用工具把它写为长期事实；用户未确认、无审计回执、也没有撤销入口（MEM-004）。一次误导后，后续对话持续以该“事实”为前提。
- 是否复现：**静态确证 + 部分动态**。工具 schema 允许 LLM 直接调用（`capability="personal.write"`）；隔离实例中通过同款 `record_fact` 写入后，`search_memory`/`recall` 均能取回；记忆页无删除路由（实测 405）。
- 根因：写入触发条件是“模型决定”，而非“用户显式确认”；`record_fact` 的 `source="explicit"` 名不副实（工具路径没有确认环节）。
- 影响面：所有对话用户；长期错误事实污染画像与检索。
- 安全或数据风险：中高。可被间接提示注入利用（让助手“记住”攻击者指定的事实）。
- 修复方案：事实写入改为“提议 + 用户确认”两阶段（工具只返回 `pending_fact`，UI 确认后才落库）；至少记录 `source="model_inferred"` 与 `confidence`，并在记忆页可删除（依赖 MEM-004）。
- 需要改的文件：`src/ai/tools/kernel_tools.py`、`src/ai/personal_context.py`、`src/gateway/account_memory.py`、前端确认流
- 验证方法：让模型从“用户说：我可能老家在苏州”触发工具，断言只产生待确认项；未确认前 `search_memory` 不返回该事实。
- 等级：P1
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`personal_add_fact` 改为 `propose_fact` 写入待审区（`profile_pending_facts`），返回 `status="pending_confirmation"`；`GET /v1/profile/pending` + `POST /pending/{id}/approve|reject` 由用户确认后才落长期事实。证据：模型写入后直接 `get_fact` 为 `None`，确认后为 `三百元`。

### MEM-010 · 长期记忆（记忆页） · 无去重、无冲突仲裁 · P2

- 文件:行号
  - `src/gateway/account_memory.py:61-64`（`MemoryWriteRequest` 仅约束长度）
  - `src/gateway/account_memory.py:304-335`（直接 `INSERT`，无内容哈希、无 key 概念）
- 表现：同一句话重复写入产生两条；同一事实“老家杭州/苏州”两个值同时存在，读取时无仲裁顺序（只按 `created_at DESC`）。
- 是否复现：**已复现**：
  - `memory_duplicate_count = 2`
  - `memory_conflict_values = ["用户老家是苏州","用户老家是杭州","我的老家是苏州","我的老家是杭州"]`
- 根因：账号记忆表是“append-only 日志”，没有 `subject/predicate` 结构，也没有 `supersedes` 链；冲突只能靠用户手工解读。
- 影响面：所有记忆页用户；记忆越长噪声越大。
- 安全或数据风险：低中。错误值不会被新值压住，模型可能取到旧值。
- 修复方案：写入时按 `(owner, normalized_text)` 去重（幂等键）；引入可选 `fact_key` + `valid_from/superseded_at`，同 key 新值自动 supersede 旧值并保留历史；列表默认只返回有效版本。
- 需要改的文件：`src/gateway/account_memory.py`、schema 迁移
- 验证方法：同文写两次断言 1 条；同 key 写两个值断言列表只显示最新且 `GET` 历史可追溯。
- 等级：P2
- 状态：PARTIAL FIXED（2026-11-08 修复线）。`POST /v1/memory/entries` 已有 60 秒短窗口幂等（同 owner/正文/来源/标签返回原条目并标 `deduplicated:true`，非本线新增），重复提交不再翻倍；跨窗口历史重复与同 key 冲突仲裁仍缺，留给下一轮。

### MEM-011 · 画像写穿 · 内核失败被静默吞掉 · P2

- 文件:行号
  - `src/ai/personal_context.py:186-200`（`_sync_to_memory` 的 `except Exception: pass`）
- 表现：内核不可用（只读盘、库损坏、权限不足）时，画像写入仍返回成功；SQLite 有值、内核没有，`search_memory` 检索不到，用户以为“助手记住了”。
- 是否复现：**静态判断**（异常分支无法从外部 HTTP 观测，但代码明确 `pass`，且无日志）。
- 根因：把“尽力而为”的写穿伪装成成功；没有 `sync_status` 回执。
- 影响面：所有写画像/事实的功能；排障时“数据库有、记忆没有”难以解释。
- 安全或数据风险：低。数据不一致，但不泄漏。
- 修复方案：至少 `logger.warning(..., exc_info=True)` 并在返回值/审计里带 `kernel_synced=false`；若产品承诺双写一致，则失败应回滚 SQLite 或返回 5xx。
- 需要改的文件：`src/ai/personal_context.py`
- 验证方法：把 `MEMORY_DB_PATH` 指向只读路径，写 fact 断言响应含 `kernel_synced=false` 且日志有告警。
- 等级：P2
- 状态：PARTIAL FIXED（2026-11-08 修复线）。`_sync_to_memory` 不再 `except: pass`：失败返回 `False` 并 `logger.warning(..., exc_info=True)`。尚未把 `kernel_synced` 透传到 HTTP/工具回执——留给下一轮。

### MEM-012 · 知识库去重 · 同内容不同文件名被“吞文件名” · P2

- 文件:行号
  - `src/knowledge/documents.py:417-442`（`deduplicated=True, chunks=0, matched_document_id=...`）
  - `src/knowledge/vector_store.py` 内容哈希去重表 `knowledge_document_hashes`
- 表现：第二份**内容完全相同、文件名不同**的文档上传返回成功，但 `chunks=0`、`vectors` 不变、ledger 只保留第一个文件名；用户看不到第二份文件存在。
- 是否复现：**已复现**：
  - 第 1 份 `去重A.txt` → `deduplicated:false, chunks:1`
  - 第 2 份 `去重B.txt`（同内容）→ `deduplicated:true, chunks:0, matched_document_id:"auditroot---A.txt"`
  - `ledger_sources = [["auditroot---A.txt","去重A.txt"]]`（B 消失）
- 根因：去重键是纯内容哈希，且以“文档”为单位返回 `chunks=0`；没有把“同一内容的多个来源/别名”建模。
- 影响面：同一制度/合同被多人分别上传；后上传者的文件名与归属丢失。
- 安全或数据风险：低中。审计上无法回答“这份文件是谁上传的”。
- 修复方案：去重返回 `matched_document_id` 的同时追加 `sources`（文件名、上传者、时间）；或建立 `document_aliases`，列表按“逻辑文档 + 来源”展示。
- 需要改的文件：`src/knowledge/documents.py`、`src/knowledge/vector_store.py`、`src/api/routes/knowledge.py`
- 验证方法：同内容不同名上传两次，断言 `GET /documents` 能列出两个来源、`matched_document_id` 指向逻辑文档。
- 等级：P2
- 状态：OPEN

### MEM-013 · 知识库重建索引 · 无接口/无迁移路径 · P2

- 文件:行号
  - `src/api/routes/knowledge.py`（仅 status/search/documents/upload/delete/chunks，无 reindex）
  - `src/knowledge/documents.py`、`src/knowledge/vector_store.py`（无 `rebuild`/`reindex` 方法）
  - 全 `src` 搜索 `reindex|rebuild_index` → **0 命中**
- 表现：换 embedding 模型或修复维度漂移后，没有任何受支持的“重建全库向量”操作；只能逐文档重传。`_rebuild_fts` 只重建 FTS 行，不重算 embedding。
- 是否复现：**静态判断**（接口清单与全仓搜索均为 0；运行时也没有该路由，404/405）。
- 根因：索引被视为“写入时一次性产物”，没有运维生命周期。
- 影响面：升级/迁移/换模型后旧向量永久不可用，且无告警（叠加 MEM-008）。
- 安全或数据风险：中。知识库可能长期“绿但不可用”。
- 修复方案：增加 `POST /v1/knowledge/reindex`（后台任务 + 进度 + 幂等 + 审计），逐文档读原文重算向量，原子替换前缀；期间查询用旧索引并在 status 标 `reindexing=true`。
- 需要改的文件：`src/api/routes/knowledge.py`、`src/knowledge/documents.py`、`src/knowledge/vector_store.py`、后台任务注册
- 验证方法：切模型后调用 reindex，断言进度从 0→N、完成后 `vectors` 全为新维度、检索恢复；reindex 期间 `search` 仍 200 且 `reindexing=true`。
- 等级：P2
- 状态：OPEN

### MEM-014 · 知识库相关度闸门 · OOD 误放行 / 误弃答 · P2

- 文件:行号
  - `src/knowledge/abstention.py:216-218`（`ollama:nomic-embed-text` 阈值 `0.65/0.05/0.85`）
  - `src/knowledge/abstention.py:317-435`（`assess_confidence`）
- 表现：与语料无关的问题仍被判定 `status="ok"` 并注入 5 条片段；部分本可回答的问题被 `below_threshold` 弃答。
- 是否复现：**已复现**（真实 20 文档 + nomic）：
  - OOD 6 题：**2 题误放行**（“量子色动力学中的渐近自由” top=0.7163/gap=0.0934；“2026 年世界杯冠军” top=0.7007/gap=0.098）
  - 4 题正确弃答（火星天气 0.648/0.0271、猫心肺复苏 0.6883/0.0372、科幻电影 0.7318/0.0044、Python GIL 0.6248/0.0637）
  - 20 题可答集中 **3 题**直接 0 命中（q07/q19/q20，search 返回 `hits=0`），即闸门/召回导致的假弃答
  - 误放行率 **33%（2/6）**；可答题假弃答 **15%（3/20）**
- 根因：阈值是**模型级全局常量**，只看 top1 与 gap，不看“答案是否由片段支持”；不同主题分数分布不同，单一 0.65 必然两头失准。
- 影响面：所有知识库问答；误放行会编出“看似有据”的答案，误弃答让用户以为没资料。
- 安全或数据风险：中。误放行会提高幻觉可信度。
- 修复方案：二次判别（如“片段是否包含问题实体/答案句”的轻量分类器或 LLM 校验），并按 embedding 模型 + 语料统计做 per-model 校准；对 `ok` 但 top1 未显著高于 runner-up 的情况降级为“低置信引用”而非直接注入。
- 需要改的文件：`src/knowledge/abstention.py`、`src/gateway/knowledge_context.py`（只读，需协调）
- 验证方法：固定 34 题校准集 + 本次 6 题 OOD，跑出混淆矩阵，要求 OOD 误放行 ≤1/6 且可答假弃答 ≤2/20。
- 等级：P2
- 状态：OPEN

### MEM-015 · 知识库同名上传 · 静默覆盖无提示 · P2

- 文件:行号
  - `src/knowledge/documents.py:61-73`（同 owner+同名 → 同 doc_id）
  - `src/knowledge/vector_store.py:644-665`（`replace_prefix` 语义上的全量替换）
- 表现：同账号再次上传同名但内容不同的文件，旧版本直接消失；API 返回 200 且 `deduplicated:false`，没有 `overwritten` 字段、没有版本号。
- 是否复现：**已复现**：两次同名上传后 `filename_conflict_ids` 相同、`chunks` 只剩版本 B、旧内容检索不到（同 MEM-006 的测试向量）。
- 根因：把文件名当主键，且默认 upsert 覆盖。
- 影响面：制度/合同/报告类文档的版本管理。
- 安全或数据风险：中高（数据丢失 + 无审计提示）。
- 修复方案：上传默认“新建版本”，同 doc_id 需显式 `overwrite=true`；响应返回 `document_id/version/previous_chunks`；删除按版本而非整前缀。
- 需要改的文件：`src/knowledge/documents.py`、`src/api/routes/knowledge.py`、`src/knowledge/vector_store.py`
- 验证方法：同名不同内容上传两次，断言两次不同 doc_id/版本、旧版本仍可检索；显式覆盖时响应含 `overwritten:true`。
- 等级：P2
- 状态：PARTIAL FIXED（2026-11-08 修复线）。同名覆盖不再静默：`ingest` 返回 `overwritten` 与 `previous_chunks`（本次替换掉的旧分块数）。版本链/显式 `overwrite=true` 语义留给下一轮。

### MEM-016 · 记忆迁移 · 无法归属的旧记忆归 root · P2

- 文件:行号
  - `src/gateway/account_memory.py:676-700`（`_owner_for_memory`，最后 `return root`）
  - `src/gateway/account_memory.py:779-789`（`_owner_for_profile`，对不上账号树也 `return root`）
  - `src/gateway/account_memory.py:849`（`ensure_legacy_memory_migrated`）
- 表现：旧内核/画像数据若 key/tags 里认不出账号，迁移时一律记到根账号；成员私密记忆会出现在根的“子树聚合”里。
- 是否复现：**静态判断**（迁移函数有明确 fallback `return root`；本次隔离库无 legacy 数据，未构造旧库）。
- 根因：旧数据没有 owner 列，只能靠字符串前缀猜；猜不出就“向上归并”，与“记忆属于记录者本人”的产品口径冲突。
- 影响面：升级用户；一旦迁移完成，归属即固化（迁移表记录 completed_at，不会重试）。
- 安全或数据风险：中高（隐私归属错误，且难以回滚）。
- 修复方案：认不出的条目迁入 `owner="__unassigned__"`（仅根可见的“未归属”区）而不是 root 本人；迁移前导出清单供人工确认；迁移记录保留 `source_key` 以便重放。
- 需要改的文件：`src/gateway/account_memory.py`
- 验证方法：构造一条无账号前缀的 legacy 记忆，迁移后断言 owner=`__unassigned__`，root 的 self 列表不出现、subtree 也不出现。
- 等级：P2
- 状态：OPEN

### MEM-017 · 记忆 TTL · 过期行永不物理清理 · P3

- 文件:行号
  - `src/kernels/memory/__init__.py:237-262`（tier 默认 TTL：SHORT 5m / MID 1h / LONG 1d / PERSISTENT 无过期）
  - `src/kernels/memory/__init__.py:551-577`（`auto_cleanup` 实际可删内存+持久化）
  - 全 `src` 搜索 `auto_cleanup` → 仅定义、风险分类、策略表，**无运行时调用者**
  - `src/ai/liuhao.py:1167-1171`（每轮 `memory.store("turn:...")` 未指定 tier → 默认 MID_TERM，1 小时 TTL）
- 表现：对话轮次镜像记忆 1 小时后“读不到”，但行仍在 SQLite；库随时间无限膨胀，`auto_cleanup` 从不执行。
- 是否复现：**静态判断 + 部分动态**（重启后过期条目不再返回，但库行数未减少；`auto_cleanup` 无调用点可用 grep 证明）。
- 根因：读路径有 TTL 过滤，写路径没有周期性清理任务；`auto_cleanup` 只被风险/策略表引用。
- 影响面：长期运行实例的磁盘与启动加载时间；隐私上“已过期”数据仍留在盘上。
- 安全或数据风险：低中（残留数据 + 体积）。
- 修复方案：在调度器注册每日 `memory.auto_cleanup`（已有风险分类，可直接用），或在 `MemoryStore` 写入时顺带按概率清理；给 PERSISTENT 明确的保留策略。
- 需要改的文件：`src/kernels/memory/__init__.py`、调度器注册处（`src/gateway/main.py` 或 scheduler 模块，需与其他线协调）
- 验证方法：写入 SHORT tier 条目并等待 5 分钟，调用 cleanup 前后查 `memory_entries` 行数，断言减少且返回值>0；再断言调度器日志出现每日清理。
- 等级：P3
- 状态：OPEN

### MEM-018 · 知识库去重 · Unicode 等价未归一 · P3

- 文件:行号
  - `src/knowledge/documents.py:49-52`（`content_hash` 仅 `re.sub(r"\s+"," ",text)`，无 `unicodedata.normalize("NFKC")`）
- 表现：全角 `ＡＢＣ１２３` 与半角 `ABC123` 内容语义相同，但哈希不同，入库为两条独立文档。
- 是否复现：**已复现**：
  - `norm-a` → `chunks:1,deduplicated:false`
  - `norm-b`（NFKC 等价文本）→ `chunks:1,deduplicated:false`；`vectors 27→28`
- 根因：去重只做空白归一，未做 Unicode 兼容归一。
- 影响面：从不同来源（Word/PDF/网页）导入的同一文本。
- 安全或数据风险：低（存储冗余 + 检索重复片段）。
- 修复方案：`normalized = unicodedata.normalize("NFKC", text)` 后再折叠空白并哈希；对既有哈希做一次性重算迁移。
- 需要改的文件：`src/knowledge/documents.py`
- 验证方法：上传全角/半角等价文本，断言第二份 `deduplicated:true` 且 `matched_document_id` 指向第一份。
- 等级：P3
- 状态：CONFIRMED FIXED（2026-11-08 修复线）。`content_hash` 与 `scoped_content_hash` 先 `unicodedata.normalize("NFKC")` 再折叠空白；新写入的全角/半角等价文本会命中同一去重哈希。既有旧哈希未回算（旧数据不会自动重新归一），需要时可借 reindex/重传，列入下一轮运维项。

### MEM-019 · 知识库状态接口 · 契约与实现不符 · P3

- 文件:行号
  - `src/api/routes/knowledge.py:188-191`（`@core.get("/status")`，docstring 写“Always 200”，无显式依赖）
- 表现：未带 token 请求 `/v1/knowledge/status` 得到 401，而不是“始终 200 的未启用态”，与控制台探活预期不符。
- 是否复现：**已复现**：`status_no_token` → `401 {"code":"AUTH_UNAUTHORIZED","message":"未提供访问令牌，请先登录"}`
- 根因：鉴权由全局中间件统一施加，路由自身没有放行；docstring 的“Always 200”只描述了 handler 不抛异常，不覆盖鉴权层。
- 影响面：控制台/探针在登录前无法判断知识库是否可用；自动化健康检查会误报。
- 安全或数据风险：低（偏可用性/契约）。
- 修复方案：明确取舍——要么把 `/status` 加入公开白名单（只暴露 `enabled/provider/model/vectors`），要么改 docstring 与前端约定为“需登录”；两者择一，不能继续矛盾。
- 需要改的文件：`src/api/routes/knowledge.py`、鉴权白名单（`src/gateway/auth*.py`，需与其他线协调）
- 验证方法：无 token GET `/v1/knowledge/status`，断言 200 且 `enabled` 字段存在；或断言 401 且文档同步更新。
- 等级：P3
- 状态：OPEN

### MEM-020 · 知识库上下文注入 · 异常静默降级 · P3

- 文件:行号
  - `src/gateway/knowledge_context.py:325-361`（`_scope_for_principal`，root 含子树）
  - `src/gateway/knowledge_context.py:387-390`（`status()` 异常 → `logger.warning` + 降级）
  - `src/gateway/knowledge_context.py:408-410`（检索异常 → 静默降级）
  - `src/gateway/knowledge_context.py:425-448`（`no_relevant`/空命中/分数不可用 → skip）
- 表现：知识库模块不可用、检索抛错、status 失败时，聊天照常回答，但回答里没有任何“知识库没参与”的提示；用户无法区分“查过但没有”与“根本没查”。
- 是否复现：**动态确证可答路径 + 静态确证降级路径**：
  - 可答问题 `休假额度…` → `used=true, decision=inject, hits=4`
  - OOD `今天火星天气如何` → `used=false, decision=below_threshold, hits=0`
  - 异常分支（388-390/408-410）没有对用户可见字段，只写日志（静态判断）。
- 根因：降级只面向“继续出答案”，没有面向“告知检索状态”；响应契约缺 `knowledge_status`。
- 影响面：所有聊天用户与排障人员；静默降级会把知识库故障伪装成模型能力问题。
- 安全或数据风险：低中（可用性/可观测性）。
- 修复方案：在聊天响应/引用区加 `knowledge: {used, status, reason, hits}`；`status` 失败时显式 `degraded=true`，前端显示“知识库暂不可用，本次未检索”。
- 需要改的文件：`src/gateway/knowledge_context.py`（只读，需协调归属线）、`src/gateway/chat.py`、前端渲染
- 验证方法：停掉 embedding provider，发聊天请求，断言响应含 `knowledge.status="unavailable"` 且前端有提示；正常时可答问题仍 `used=true`。
- 等级：P3
- 状态：OPEN

## 4. 检索准确度实测（20 组，真实数字）

- 语料：20 篇独立文档（kb-01…kb-20），单 chunk；真实 Ollama `nomic-embed-text` 768 维；`POST /v1/knowledge/search top_k=5`。
- 判定：`expected` 在前 5 → Recall@5；排名 ≤3 → Top-3 命中。
- 结果：**Recall@5 = 17/20（85.0%）**；**Top-3 = 17/20（85.0%）**（返回最多 5 条，故两指标在本实验中等价）。

| 题号 | 类别 | 问题 | 期望 | 排名 | Top-3 | Top-1 RRF 分 |
|---|---|---|---|---|---|---|
| q01 | 同义词 | 出差发票怎么报账 | kb-01 | 2 | 是 | 0.0303 |
| q02 | 错别字 | 向量数剧库的相似度怎么算 | kb-02 | 1 | 是 | 0.0315 |
| q03 | 分词边界 | 研究生命起源 怎么切分 | kb-03 | 1 | 是 | 0.0328 |
| q04 | 专业术语 | 检索增强生成里召回率指标是什么意思 | kb-04 | 1 | 是 | 0.0309 |
| q05 | 代码片段 | 余弦相似度 python 代码怎么写 | kb-05 | 1 | 是 | 0.0328 |
| q06 | 同义词 | 休假额度怎么算，没休完能留到明年吗 | kb-06 | 1 | 是 | 0.8052 |
| q07 | 错别字 | VNP 连不上怎么办 端口多少 | kb-07 | — | 否 | 无命中 |
| q08 | 专业术语 | 接口重复提交怎么防重复扣款 | kb-08 | 1 | 是 | 0.0325 |
| q09 | 分词边界 | 鎏 灏 智能 体 平台 的账号隔离 | kb-09 | 1 | 是 | 0.0328 |
| q10 | 代码片段 | 删除知识库文档的 SQL 语句 | kb-10 | 1 | 是 | 0.0325 |
| q11 | 同义词 | 机器怎么安全重启 | kb-11 | 1 | 是 | 0.0313 |
| q12 | 错别字 | 发漂抬头写什么，税号多少 | kb-12 | 1 | 是 | 0.0301 |
| q13 | 分词边界 | 五万到五十万的合同谁签字 | kb-13 | 1 | 是 | 0.0328 |
| q14 | 专业术语 | ABAC 是什么访问控制模型 | kb-14 | 1 | 是 | 0.0328 |
| q15 | 代码片段 | FastAPI 文件上传接口代码 | kb-15 | 1 | 是 | 0.0328 |
| q16 | 同义词 | 数据多久备一次，备份放哪里 | kb-16 | 1 | 是 | 0.0320 |
| q17 | 错别字 | 知识库切篇默认长度 900 吗 | kb-17 | 1 | 是 | 0.0328 |
| q18 | 专业术语 | 缓存雪崩和击穿区别与处理 | kb-18 | 1 | 是 | 0.0328 |
| q19 | 同义词 | 向量化失败会不会入库成功 | kb-19 | — | 否 | 无命中 |
| q20 | 代码片段 | 怎么用 PowerShell 查日志里的错误 | kb-20 | — | 否 | 无命中 |

分类命中（Top-3 / 题数）：

| 类别 | 命中 | 题数 | 失败题 |
|---|---|---|---|
| 同义词 | 4 | 5 | q19 |
| 错别字 | 3 | 4 | q07 |
| 分词边界 | 3 | 3 | — |
| 专业术语 | 4 | 4 | — |
| 代码片段 | 3 | 4 | q20 |

补充说明：
- 表中 `Top-1 RRF 分` 是 **RRF 融合分**（`1/(60+rank)` 加权、最佳命中归一为 1.0），不是余弦；q06 因词面高度重叠、词法榜权重高而得到 0.8052，其余题目由向量榜主导故在 0.030 量级。两套尺度不能互比，也不能拿 0.25 之类的余弦门槛二次否决（`knowledge_context.py:437` 已正确处理）。
- 失败的三题已单独复测：q07（`VNP` 错别字，`hits=0`）、q19（同义改述，`hits=0`）、q20（PowerShell 代码，`hits=0`）。失败不是“排错”，而是**闸门直接弃答**（`status=no_relevant`），因此同时计入 MEM-014 的假弃答。
- 真实 Ollama 余弦（离线复算，仅作对照）：q01 对 kb-01=0.6473、q06 对 kb-06=0.8052、q13 对 kb-13=0.8873；说明向量本身可用，问题在融合排序与阈值。

## 5. 已验证通过的对照项（负结果，避免误判）

| 项 | 实测结果 | 结论 |
|---|---|---|
| 记忆写后立读 | 写入后立即 `GET /v1/memory/entries` 可见；重启后仍在 | 通过（无异步延迟窗口） |
| 记忆页跨账号读隔离 | member 读 root 条目 → 404；root `self` 看不到 member；root `subtree` 可见 | 通过（符合《账号层级》设计） |
| 知识删除彻底 | 删 kb-07/kb-10 后 `vectors/hash/fts` 均为空、`search=[]`、`chunks=404` | 通过 |
| 向量化失败不留半条 | FAILME 入库 → 503；ledger 未增（24→25 是前一次成功 stub-8 所致）、`chunks=404`、`search_fail=[]` | 通过 |
| 并发同 doc 5 次 | 5 次全 200，仅 1 条向量 `auditroot--concurrent-test#0` | 通过 |
| 并发同 doc 不同内容 4 次 | 全 200，最终仅版本 B，chunks 与检索内容一致 | 通过（最后写入胜出） |
| UTF-8 BOM 去重 | 同内容带/不带 BOM → 去重命中 | 通过 |
| 查询与替换并发 | 未出现服务错误；6 线程压测触发 429 限流 | 通过（限流按设计） |
| 重启持久化 | 记忆 1 条存活、知识 26 文档、检索正常 | 通过 |

## 6. 未验证 / BLOCKED（诚实边界）

1. **RAG“回答生成”一步未跑**：隔离实例未配置聊天模型，且 `src/gateway/main.py` 在审计时段被其他线改坏（`NameError: asyncio`）。本域验证到“检索 → 拼上下文（`used=true/decision=inject/hits=4`）”为止；模型是否忠实使用上下文不做推断。
2. **重建索引不可测**：代码中不存在该接口（MEM-013），无法构造“重建期间查询”的运行时证据。
3. **迁移归属为静态判断**：隔离库无 legacy 数据，未构造旧格式库；MEM-016 依据代码 fallback 判定。
4. **`main.py` 并发故障**：非本域缺陷，未修改；涉及 `/v1/chat` 的少量用例转静态判断。

## 7. 最该先修的 5 条

1. **MEM-001（P0）**：中文账号/文件名命名空间坍缩。任何多租户中文部署都会跨账号覆盖/不可见，必须先修；同时需数据迁移。
2. **MEM-007（P1）**：会话搜索前缀越权。不需要特殊权限即可读出他人对话，安全优先。
3. **MEM-003（P1）**：内核 L1 全局检索。员工私密事实可被其他账号助手读出，与 MEM-007 同属跨租户泄漏。
4. **MEM-002 + MEM-004（P1）**：删除不彻底 + 无删除/纠正接口。用户“删了还在”，是合规与信任的硬伤，二者一起做成完整生命周期。
5. **MEM-008 + MEM-013（P1/P2）**：维度漂移静默 + 无重建索引。换模型是常规运维动作，现在是“全绿但不可用”；先加维度校验与状态降级，再补 reindex。

## 8. 等级计数与状态

| 等级 | 条目 | 数量 |
|---|---|---|
| P0 | MEM-001 | 1 |
| P1 | MEM-002/003/004/005/006/007/008/009 | 8 |
| P2 | MEM-010/011/012/013/014/015/016 | 7 |
| P3 | MEM-017/018/019/020 | 4 |
| P4 | — | 0 |

状态（2026-11-08 修复线更新）：

| 状态 | 条目 |
|---|---|
| CONFIRMED FIXED | MEM-001（P0）、MEM-002/003/004/005/006/007/008/009（P1）、MEM-018（P3） |
| PARTIAL FIXED | MEM-010/011/015（P2） |
| OPEN（留给下一轮） | MEM-012/013（P2，013 的 reindex 主体已随 MEM-008 落地，见条目）、MEM-014/016（P2）、MEM-017/019/020（P3） |

说明：
- MEM-013 的 `POST /v1/knowledge/maintenance/reindex` 已随 MEM-008 一并落地并实测（维度错配 → reindex 恢复），但“后台任务 + 进度 + 重建期间 `reindexing=true`”的完整体验未做，故条目仍保留 OPEN 的剩余部分。
- 未做项优先级（下一轮）：MEM-016（迁移归属 `__unassigned__`）> MEM-014（闸门校准）> MEM-012（去重来源别名）> MEM-017（TTL 定时清理，需调度器协调）> MEM-019（`/status` 鉴权契约，需鉴权线协调）> MEM-020（知识库降级可见性，需重读 `knowledge_context.py`）。
- 并发改码提示：本轮运行 `tests/test_knowledge_live.py` 时，`test_unconfigured_knowledge_answers_503_not_fake_vectors` 因**他线未提交的 `main.py` 5xx 掩码**把 503 的 `detail="知识库未启用…"` 换成通用文案而失败；本线未改 `main.py`，该失败归属并发线，需由网关线决定“掩码是否放行知识库未启用文案”。
- `src/ai/providers.py` 存在他线未完成的语法错误（函数体缺 `def`），导致 `tests/ai/test_context_budget.py` 无法被 pytest 收集；本线未改该文件，MEM-005 改以隔离脚本直接验证 `deterministic_summary` / `trim_history`。
