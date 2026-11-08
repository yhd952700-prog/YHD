# 鎏灏 LiuHao AI OS 数据出站审计（2026-11-08）

> 审计性质：**只读**。未修改任何源码、配置或 `D:\LiuHaoData`。
> 验证方式：静态代码审计 + 隔离实例（端口 19012/19015，数据目录 `D:\_audit\egress`）+ 本地日志代理（127.0.0.1:19014）+ 按 PID 的连接采样 + 本地 mock 模型服务（127.0.0.1:19016）。

---

## 0. 一句话结论

1. **没有遥测、统计、崩溃上报、使用数据回传、许可证激活、自动更新心跳。首启不联网。** 隔离实例从进程启动到 `/v1/health` 就绪，外网连接数为 **0**。
2. **唯一"默认开启、界面上没有告知"的外发是「联网检索」工具**：它把用户问题生成的检索词（纯中文且未命中内置词表时是**原句**）发往 `hn.algolia.com`、`api.github.com`、`export.arxiv.org`，其中 arXiv 走**明文 HTTP**。这是本审计最重要的发现。
3. 一旦配置云端模型 Key：系统提示（含个人画像摘要）、最近 **20 轮**会话、当前问题、命中的本地知识库片段全部发往**该厂商**；Key 只出现在该厂商请求头里，不进检索、不进更新、不进日志/诊断包。
4. **"本地 Ollama 模式"对模型推理成立**（实测只连 `127.0.0.1:11434`），但**不等于整个应用零出网**（见第 2、3 条）。

---

## 1. 目标 → 用途 → 携带什么 → 是否可关（总表）

| # | 目标（协议/端口） | 触发与用途 | 实际携带什么 | 默认状态 | 是否可关 / 开关 |
|---|---|---|---|---|---|
| 1 | `127.0.0.1:11434`（HTTP）`/api/chat`、`/api/embed`、`/api/tags` | 本机 Ollama 推理、嵌入、模型探测 | 系统提示、最近 20 轮对话、当前问题、知识库命中片段、工具结果；**无账号/机器标识/Key** | **开**（默认提供者） | 换成本机即可；本身不出本机 |
| 2 | 云端模型预设：`api.openai.com:443`、`api.anthropic.com:443`、`generativelanguage.googleapis.com:443`、`api.deepseek.com:443`、`api.moonshot.cn:443`（或 `.ai`）；以及用户自定义任意 `base_url` | 对话生成（`/chat/completions`、`/messages` 等） | 同上全部内容 + `Authorization: Bearer <Key>` / `x-api-key` / `x-goog-api-key`；**无账号 ID、无邮箱、无主机名/MAC** | **关**（未配 Key） | 不配置 Key；或改用 Ollama |
| 3 | `{base_url}/models` | 用户点"测试连接 / 拉取模型列表" | `<Key>` + `base_url`；不发对话内容 | 手动触发 | 不用该按钮即可 |
| 4 | `hn.algolia.com:443` GET `/api/v1/search_by_date` | 助手调用「联网检索（network_bus）」 | `query=<检索词>`、`tags=story`、`hitsPerPage=5`；UA `LiuHaoAIOS/1.0 (read-only research)`；**不含 Key/历史/画像** | **开（默认启用的工具）** | `LIUHAO_WEB_RESEARCH=0`（无 UI 开关） |
| 5 | `api.github.com:443` GET `/search/repositories` | 同上 | `q=<检索词> in:name,description,readme`、`sort=stars`、`per_page=5`；UA 同上 | **开** | 同上，或 `LIUHAO_WEB_RESEARCH_PROVIDERS` 只留部分 |
| 6 | `export.arxiv.org` **:80 明文 HTTP** GET `/api/query` | 同上 | `search_query=all:<检索词>`、`start=0`、`max_results=5`；UA 同上 | **开** | 同上 |
| 7 | `api.openai.com/v1/embeddings`（仅在 `OPENAI_API_KEY` 存在于环境时） | 知识库入库/检索的向量化（`auto` 分支） | 文档分片原文 / 检索文本 + `Authorization: Bearer <Key>`，`{"model":..., "input":[...]}` | **取决于环境变量** | 保持 `LIUHAO_EMBEDDING_PROVIDER=ollama`，或不设 `OPENAI_API_KEY` |
| 8 | 用户配置的升级清单 URL（无内置默认） | 手动检查更新 | 只发 GET；**不含用户数据/账号/Key/机器码** | **关**（无更新源时） | 不配置 `LIUHAO_UPGRADE_*` 即可；无自动轮询 |
| 9 | `api.osv.dev:443` POST `/v1/query`；`api.github.com`（REST/GraphQL 安全公告） | 依赖漏洞情报扫描 | 包名、生态、版本号、仓库名；可选 `GITHUB_TOKEN` | **关**（`allow_network=False`） | 不设 `LIUHAO_INTEL_ALLOW_NETWORK=1` |
| 10 | 27 个第三方集成域名（Slack/Notion/Jira/GitHub/GitLab/Asana/Linear/Stripe/Telegram/…） | 用户主动"连接"某个应用后才会调用 | OAuth/Token + 该动作本身的内容（如发送的消息文本） | **关**（未连接） | 不连接该集成即可 |
| 11 | 用户添加的 MCP Server URL | MCP 工具调用 | 工具参数（可能含用户内容）+ 该 Server 的鉴权 | **关**（无默认 Server） | 不添加即可 |
| 12 | `localhost:4317`（OTLP） | OpenTelemetry 追踪/指标导出 | span 元数据与错误信息；**未发现 prompt 原文埋点** | **关**（导出器未在启动时初始化） | 不启用 OTEL 即可 |
| 13 | 用户环境变量 `LIUHAO_ALERT_WEBHOOK` | 告警外发 | 告警 JSON（指标名、阈值、消息） | **关** | 不设该变量即可（默认只写本地日志） |
| 14 | 用户配置的 RFC3161 TSA URL | 审计链可信时间戳 | **仅 SHA-256 摘要**，不发原文 | **关**（默认 `LIUHAO_TSA_PROVIDER=local`） | 不选 `rfc3161` 即可 |
| 15 | `localhost:2379`（etcd） | 分布式实例协调 | 实例心跳/租约 | **关**（单机默认） | 不启用分布式模式即可 |
| 16 | 字体 / CDN / 前端统计 / 崩溃上报 / 应用层时间同步 | — | — | **没有** | — |

---

## 2. 分类详情

### 2.1 模型调用（唯一会带走"全部上下文"的通道）

- 代码位置：`src/ai/liuhao.py`（组装消息）、`src/ai/providers.py`（各厂商实现）、`src/gateway/chat.py`（RAG 注入）。
- 发送内容（实测用本地 mock 捕获，`POST /v1/chat/completions`）：

  ```json
  {
    "messages": [
      {"role": "system",    "content": "鎏灏系统提示 + KAREN 个人情报摘要"},
      {"role": "assistant", "content": "历史回答"},
      {"role": "user",      "content": "当前问题"}
    ],
    "model": "gpt-4o-mini",
    "max_tokens": 1000,
    "temperature": 0.7
  }
  ```

  请求头：`Authorization: Bearer <Key>`、`User-Agent: OpenAI/Python 3.13.0`、`Content-Type: application/json`。
- 上下文范围：`MAX_HISTORY_MESSAGES = 20`（`src/ai/liuhao.py:67`）；若存在用户画像，会在系统提示中附带画像摘要；若知识库检索命中，`src/gateway/knowledge_context.py` 的 `knowledge_prompt()` 会把命中片段拼进系统提示（`use_knowledge` 默认 `auto`：有文档就开）。
- **不带**：账号 ID、邮箱、JWT、主机名、MAC、安装 ID、Key 以外的凭证。
- **失败降级**：默认 `fallback_chain: [ollama, mock]`（`config/default.yaml:199`），云端不可用时不会"偷偷"改发别家；`mock` 是本地测试提供者。
- Ollama 路径显式关闭代理（`proxies={"http": None, "https": None}`），实测只连 `[::1]:11434`。

### 2.2 联网检索（默认开启，最容易被忽视的外发）

- 开关：`LIUHAO_WEB_RESEARCH`，**未设置时默认启用**（`src/ai/web_research.py:51`、`enabled()`）；提供者默认 `hackernews/github/arxiv`。
- 触发：模型决定调用 `network_bus` 工具时（不是启动时，也不是每轮必然）。注册时为"已批准、已激活"。
- 检索词生成：`build_search_phrase()`（`src/ai/web_research.py:173`）先把中文映射成内置英文关键词、保留 ASCII 词；**若最终为空，回退为原句**。
  实测：
  - `"帮我查一下 React 19 的新特性"` → `"React 19"`
  - `"请帮我查一下本周量子计算的最新进展"` → **原句** `"请帮我查一下本周量子计算的最新进展"`
- 实测目标（日志代理捕获，全部返回 502，证明只尝试了这三个）：

  ```
  CONNECT api.github.com:443
  CONNECT hn.algolia.com:443
  GET http://export.arxiv.org/api/query?search_query=all%3AABCDEF-19012&start=0&max_results=5 HTTP/1.1
  ```

- **隐私弱点**：arXiv 走明文 HTTP（`src/ai/web_research.py:332`），检索词在链路上可被中间人看到；三个目标均为无鉴权公开接口，不需要 Key。
- 关闭方法：`LIUHAO_WEB_RESEARCH=0`；或用 `LIUHAO_WEB_RESEARCH_PROVIDERS` 限定提供者。**控制台没有对应开关**。

### 2.3 向量化（Embedding）

- 默认：本机 Ollama `nomic-embed-text`，实测解析结果为 `provider=ollama, model=nomic-embed-text`，只连本机。
- `auto` 分支的陷阱：**只要进程环境里存在 `OPENAI_API_KEY`（或 `LIUHAO_EMBEDDING_API_KEY`），即使聊天选择的是 Ollama，知识库文本也会默认发往 `api.openai.com/v1/embeddings`**（`src/knowledge/embedding.py:469` 起）。这不是 UI 配置项，而是环境变量触发。
- 关闭方法：显式 `LIUHAO_EMBEDDING_PROVIDER=ollama`，并确保环境里没有 `OPENAI_API_KEY`。

### 2.4 升级检查

- **没有内置更新服务器，没有启动自动检查，没有版本心跳。** `source_configured()`（`src/upgrade/update_check.py:90`）为假时，`seed_update_check_job()`（同文件 `:261`）直接返回，不排期。
- 只有运维显式配置 `LIUHAO_UPGRADE_MANIFEST_URL`（或本地发布目录）后才存在更新任务；请求仅 GET 清单，未发现携带账号/机器标识/Key。
- 实测（隔离实例、无配置）：`source_configured=false`，检查结果为 `not_configured`，代理日志无任何请求。
- 控制台"检查更新"按钮在无更新源时**禁用**，与行为一致。

### 2.5 漏洞情报（Intel）

- `OsvClient` / `GitHubAdvisoryClient` 的 `allow_network` **默认 `False`**（`src/intel/osv_client.py:148`、`github_client.py:92`）。
- 周任务会被注册，但只有 `LIUHAO_INTEL_ALLOW_NETWORK=1` 才真正请求；否则如实记为"未获取"。
- 请求内容：包名/生态/版本（OSV）；仓库名（GitHub）；可能带 `GITHUB_TOKEN`。**不含对话内容**。
- 实测：`OsvClient().network_enabled == False`。

### 2.6 第三方集成 / MCP

- 27 个集成适配器全部需要用户主动连接并提供凭证；未连接时不存在后台轮询。
- 连接后外发的是**该动作本身**（例如往 Slack 发一条消息），域名是厂商域名。
- MCP 同理：只有用户添加 Server 后才会连接，发的是工具参数。

### 2.7 可观测、告警、时间戳、协调

- OTel：默认 endpoint 是 `http://localhost:4317`，但网关启动时**不初始化导出器**（`src/gateway/main.py:45` 明确把 OTel SDK 排除在启动路径外，只做轻量 `get_tracer`），因此默认无 OTLP 出站。
- 告警：默认只写本地日志；设置 `LIUHAO_ALERT_WEBHOOK` 才会 POST 告警 JSON（`src/observability/alerts/sinks.py:33`）。
- 时间戳：默认 `LIUHAO_TSA_PROVIDER=local`（`src/security/evidence/__init__.py:20`），不出网；选 `rfc3161` 时只发哈希摘要。
- 分布式协调：etcd `localhost:2379`，仅分布式模式使用。

### 2.8 明确"没有"的类别

| 类别 | 结论 |
|---|---|
| 遥测 / 使用统计 / 行为打点 | **没有**（全仓无 analytics/telemetry 上报代码） |
| 崩溃上报（Sentry/BugSang/自建） | **没有** |
| 自动更新检查（启动/定时） | **没有**（必须先配置更新源） |
| 字体 CDN（Google Fonts 等） | **没有**，前端字体/资源全部本地打包 |
| 第三方 CDN / 外链脚本 | **没有** |
| 应用层 NTP 时间同步 | **没有**（系统时间服务与产品无关） |
| 许可证激活 / 心跳 | **没有** |

---

## 3. 运行时实测记录（证据）

| 实验 | 配置 | 结果 |
|---|---|---|
| 冷启动出网 | 隔离实例 19015，全新数据目录，PID 级 100ms 采样 | `startup_ready=True`，**外部目标数 0**；启动日志只出现 `http://127.0.0.1:19015` |
| 只读接口请求 | 19012，循环请求 `/v1/health`、`/v1/ready`、`/v1/models` | 该 PID **外部目标数 0**（仅回环） |
| 本机推理 | Ollama `qwen2.5:3b`，PID 级采样 | 目标仅 `[::1]:11434`，回复"收到" |
| 联网检索 | 日志代理 19014 | 命中 `api.github.com:443`、`hn.algolia.com:443`、`export.arxiv.org:80`（明文） |
| 检索词回退 | `build_search_phrase` | 纯中文未命中词表 → **原句直发** |
| 更新检查 | 无 `LIUHAO_UPGRADE_*` | `source_configured=false` → `not_configured`，无请求 |
| 依赖情报 | 默认构造 | `network_enabled=false` |
| Embedding | 默认环境 | `provider=ollama, model=nomic-embed-text`，无外部连接 |
| 模型请求体 | 本地 mock OpenAI 19016 | 捕获 `messages/model/max_tokens/temperature` + `Authorization: Bearer` |

---

## 4. 用户填 Key 后，什么会离开本机

**会离开（发往该厂商）**：
- 系统提示（含"鎏灏"人格设定与 KAREN 个人情报摘要，若有）；
- 最近 20 轮对话（用户与助手原文）；
- 当前问题；
- 命中的本地知识库片段（若开启检索增强）；
- 工具调用参数与工具返回结果（多步工具循环时会再次上行）。

**不会离开**：
- Key 本身以外的任何凭证；账号 ID/邮箱/JWT；主机名/MAC/安装 ID；
- 本地数据库、审计日志、未命中的知识库文档；
- 其他厂商的 Key（每个请求只带当前提供者的 Key）。

**注意**：AI 员工/自动化任务与手动对话走同一提供者，自动化运行的内容同样上行。

---

## 5. 隐私告知：界面在哪、说了什么、是否一致

| 位置 | 现状原文/表现 | 与实际是否一致 |
|---|---|---|
| 首启登录页（`apps/console/.../Login.tsx:799`、`:836`） | "同时配置模型 Key（可选，可跳过，之后也能补）"；输入框写"API Key（可留空跳过）" | **不完整**：没有说明"填了云端 Key 后对话会发到该厂商" |
| 设置 → 模型（`ModelManager.tsx:128-134`） | OpenAI/Anthropic/DeepSeek/Azure 标"（云端）"；Ollama 标"本地" | **部分不一致**：Gemini、Kimi（Moonshot）等云端项没有"（云端）"标记，用户难以区分 |
| 设置 → 更新 | 无更新源时按钮禁用并说明 | **一致**：确实不发请求 |
| 诊断导出 | "这个包不含你的密钥" | **一致**：Key 脱敏，配置只导出厂商/模式 |
| 全局 | **没有"隐私/数据流向"页面，没有首启同意，没有遥测开关，没有联网检索开关** | 见第 6 节 |

**结论：产品没有对"对话内容发往云端模型"和"联网检索会外发检索词（必要时原句）"作出任何界面告知。** 这不是"说错"，而是"该说没说"。

---

## 6. 不一致清单（重点）

### 6.1 "说了不发、实际发了"

1. **拟发布的首启文案**（`docs/audit-first-run-disclosure-20261108.md`）写的是"默认不联网；只有你配置云端模型后……"以及"选本机 Ollama 时只连 127.0.0.1，不出本机"。
   **若原样上线，将与实际不符**：联网检索默认开启，即使聊天选 Ollama，模型一旦调用 `network_bus`，检索词（纯中文时为**原句**）就会发往三个公网接口。
   → 上线前必须改写为："本机模型不外发；但「联网检索」默认开启，会把检索关键词（必要时原句）发送到 Hacker News / GitHub / arXiv，可在设置中关闭。"

2. `docs/knowledge_base/architecture/overview.md` 的"离线可用"、`docs/faq.md` 的"Ollama 断网照常"。
   **边界不清**：核心对话确实可用；但联网检索会尝试出网（失败后以工具错误返回，不影响本地回答）。文案应限定为"模型推理离线可用"。

3. `docs/oss-radar/compliance-supplychain.md:521` 把"断网可完整使用；抓包无外联"列为待兑现的合规承诺。
   **当前不成立**（至少"抓包无外联"不成立）。本次审计即是对该条的核验。

### 6.2 "说了发、其实没发"

4. `docs/oss-radar/memory-retrieval.md:59` 称"MemoryManager 默认走 Mem0（需要 `OPENAI_API_KEY` + Qdrant localhost:6333），是主链路"。
   **代码事实相反**：所有生产调用点都显式 `use_mem0=False`（`src/ai/employee.py`、`src/knowledge/memory.py` 等），记忆走本地 SQLite，**不会**把记忆发往 OpenAI。
   → 文档需更正；这也是一个**正向**差异（实际比文档更保守）。

5. 升级/时间戳/漏洞情报等功能在文档中存在，但默认都不发起外部请求；如果对外宣传"自动检查更新 / 可信时间戳已启用"，同样属于"说了其实没发生"。目前控制台按钮状态与行为一致，属于良性差异。

### 6.3 其它告知缺口

6. "云端"标签覆盖不全（Gemini/Kimi 未标），用户无法从界面判断某提供者是否出网。
7. `posthog` 作为 `mem0ai` 的传递依赖被固定在 `requirements.txt`，产品代码**没有**导入；当前无遥测，但依赖层面保留了遥测能力，属于回归风险而非现行行为。
8. 联网检索的开关只存在于环境变量，普通用户无法在界面关闭。

---

## 7. 残余风险（不改代码，仅记录）

| 风险 | 说明 | 触发条件 |
|---|---|---|
| Google / Moonshot / DeepSeek 未接入 `net_guard` | `assert_safe_runtime_target` 只覆盖 OpenAI/Anthropic/Ollama/模型发现；其余提供者可被配置指向内网或云元数据地址 | 需要本机改配置，远程不可利用 |
| `OPENAI_API_KEY` 触发 embedding 自动走 OpenAI | 聊天可选 Ollama，但知识库文本仍可能上行 | 环境变量存在 |
| `langgraph_workflow.py` 默认构造 OpenAI 客户端（`gpt-4o-mini`） | 网关未使用；CLI/工作流路径若被启用会直连 `api.openai.com`（无 Key 时也尝试） | 手动运行该工作流 |
| `Mem0Backend` 工厂默认 `use_mem0=True` | 当前所有调用点传 `False`，未来若有人按默认值调用，会把记忆发往 OpenAI + Qdrant | 代码回归 |
| arXiv 明文 HTTP | 检索词可被链路观察 | 联网检索触发 |
| 检索词回退为原句 | 纯中文问题可能整句外发 | 联网检索触发 |

---

## 8. 建议（供后续修复，不在本次只读范围内实施）

1. 首启/设置增加「数据流向」页：三条一句话说明（本地推理 / 云端模型 / 联网检索），并提供"联网检索"开关（建议默认关或首启询问）。
2. 修正 6.1、6.2 中的文档与拟发布文案；把"离线可用"限定为"模型推理离线可用"。
3. 给所有云端提供者补 `net_guard`；统一"（云端）/（本地）"标签。
4. embedding `auto` 改为跟随当前聊天提供者，或在切换到 OpenAI 前显式提示。
5. `web_research` 的 arXiv 改 HTTPS；纯中文回退策略改为"不检索 + 提示用户"，避免整句外发。
6. 在依赖层面移除未使用的 `posthog`，或显式关闭 mem0 遥测，防止未来回归。

---

## 9. 审计边界说明

- 未修改任何源码/配置；未读写 `D:\LiuHaoData`。
- 隔离实例写入 `D:\_audit\egress\data`、`data2`、`logs`，代理/探针脚本放在 `D:\cache\temp\liuhao-audit-egress`。
- 访问密钥类请求均为本地 mock（`127.0.0.1:19016`），未向任何真实厂商发送测试数据。
- 结论以本机代码 + 本机实测为准；升级源、集成、MCP、OTel、TSA 等需要用户配置的能力，仅评估"配置后会发生什么"，未在本次实际启用。

---

## 10. 修复后复测（2026-11-08 晚，默认状态实测）

### 10.1 这一轮改了什么

| # | 改动 | 文件 |
|---|---|---|
| 1 | 联网检索**默认关闭**；新增 `GET/PUT /v1/network/research` 与「设置 → 隐私与联网」开关，开启时才显示"检索词会发到哪三个站点" | `src/ai/web_research.py`、`src/gateway/networks.py`、`apps/console/.../pages/Settings.tsx` |
| 2 | arXiv 由 `http://export.arxiv.org:80` 改为 `https://export.arxiv.org:443` | `src/ai/web_research.py` |
| 3 | 检索词出网前统一清洗：去掉邮箱/电话/密钥/URL/控制字符；词表未命中时退到"已清洗 + 截断（≤48 字符）"，不再整句原样外发 | `src/ai/web_research.py`（`sanitize_phrase` / `build_search_phrase`） |
| 4 | embedding `auto` 不再读取 `OPENAI_API_KEY`：`auto` 只走本机 Ollama；云端 embedding 必须显式选 `openai` / `openai_compatible` | `src/knowledge/embedding.py` |
| 5 | Gemini / Kimi 补齐「云端」标记；每个云端选项直接写明"你的对话会发给 X"，选中后另有说明；自定义 `base_url` 时显示真实主机名 | `apps/console/.../components/ModelManager.tsx` |
| 6 | `/v1/about` 增加后端实时 `network` 事实（默认值、当前开关、目标域名），关于页不再只靠写死文案 | `src/gateway/diagnostic_bundle.py`、`apps/console/.../pages/About.tsx` |
| 7 | `BEFORE-INSTALL.txt`、首启向导（CLI + GUI）、`docs/faq.md` 同步改写，删除"完全不出网"的绝对说法 | 文案同上 |

### 10.2 实测方法（默认状态）

- 隔离实例：端口 **19022**，数据目录 `D:\_audit\egress2`，`APP_ENV=production`，不设任何厂商 Key。
- 全量出站拦截：进程 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY=http://127.0.0.1:19023`（日志代理，一律回 502，**不放行**）。
- 直连旁路检测：按网关 PID 每 100ms 采样 `Get-NetTCPConnection`，过滤非回环地址（**按 PID 过滤**，避免误判系统其它进程）。
- 真实上游替代：`127.0.0.1:19024` 本地假 OpenAI 兼容服务，记录请求体与请求头；未使用任何真实 Key。
- 全流程：启动 → 首屏 HTML → 登录态 → 发两条消息 → 知识库入库/检索 → 生成诊断包 → 检查更新 → 读取关于页 → 联网检索开关开/关。

### 10.3 结果

- **冷启动到全流程结束，日志代理记录 0 条出站请求；PID 级采样记录 0 条非回环连接。**
- 首屏 HTML 中 `https?://` 外部引用数为 **0**（资源全部来自网关自身）。
- 消息只到 `127.0.0.1:19024`（本地假上游），`false` 状态下的 `/v1/network/research` 为 `enabled=false`。
- 知识库检索使用本机 Ollama embedding，未产生外网连接。
- 诊断包无 `Bearer`、无 API Key、无用户名。
- 检查更新：`source_configured=false` → `not_configured`，无请求（HTTP 409 为"未配置"语义，非错误）。
- 显式开启后调用检索路径（受控探针）：只尝试了三个目标，**arXiv 已是 `:443`**：
  ```
  CONNECT hn.algolia.com:443
  CONNECT api.github.com:443
  CONNECT export.arxiv.org:443
  ```
- 同一探针的检索词：`请帮我查一下本周量子计算的最新进展，我的邮箱 alice@example.com，电话 13800138000`
  → 实际出网短语 `"请 量子计算的 进展"`（邮箱、电话与标签被删除并截断到 48 字内，**不是原句**）。

### 10.4 出站清单（默认状态实测）

| 域名 / IP | 触发时机 | 携带字段 | 是否含个人信息 | 文案是否已披露 |
|---|---|---|---|---|
| `127.0.0.1:11434`（本机 Ollama） | 模型推理 / 向量化 | 系统提示、最近 20 轮对话、当前问题、知识库命中片段、工具结果 | 对话内容=个人内容，但**不出本机**；无账号/机器标识 | 是（首启/关于页：本机推理仅连 127.0.0.1） |
| 用户配置的模型 `base_url`（本实测为 `127.0.0.1:19024`） | 发消息 / AI 员工 / 自动化任务 | 同上 + `Authorization: Bearer <Key>`（Key 仅在请求头） | 是（对话原文与上下文）；无账号 ID/主机名 | 是（首启/关于页/模型页："对话会发给这家公司"） |
| `hn.algolia.com:443` | **仅当用户在设置里开启联网检索**后，助手调用检索工具 | `query=<清洗后关键词>`、`tags=story`、`hitsPerPage=5`、UA `LiuHaoAIOS/1.0` | 仅关键词；已去邮箱/电话/密钥/URL，不含历史/画像/Key | 是（设置页开启时逐站点列出；BEFORE-INSTALL/关于页） |
| `api.github.com:443` | 同上 | `q=<关键词> in:name,description,readme`、`sort=stars`、`per_page=5` | 同上 | 是 |
| `export.arxiv.org:443` | 同上（**已由 :80 改为 :443**） | `search_query=all:<关键词>`、`start=0`、`max_results=5` | 同上 | 是 |
| 用户配置的升级清单 URL | 仅在配置 `LIUHAO_UPGRADE_*` 后手动/排期检查 | 仅 GET；无用户数据/Key/机器码 | 否 | 是（升级页 + 关于页"未配置不请求"） |
| `api.osv.dev:443`、`api.github.com`（安全公告） | 运维显式 `LIUHAO_INTEL_ALLOW_NETWORK=1` | 包名/生态/版本/仓库名 | 否 | 是（关于页/BEFORE-INSTALL 新增披露） |
| 用户配置的 RFC3161 TSA | 显式选择 `rfc3161` | 仅 SHA-256 摘要 | 否 | 是（关于页新增披露） |
| 用户配置的告警 Webhook | 显式设置 `LIUHAO_ALERT_WEBHOOK` | 告警 JSON | 否 | 是（关于页新增披露） |
| 用户主动连接的集成 / MCP Server | 用户连接并发起动作 | 该动作内容 + 该服务凭证 | 取决于用户动作 | 是（关于页新增披露） |
| 遥测 / 崩溃上报 / 字体 CDN / 广告 / 版本心跳 / 许可证激活 | — | — | — | **没有**（实测 0 出站） |

### 10.5 文案对齐结论

- 原先两处"说了不发"已修正：首启向导、`docs/faq.md`、关于页不再声称 Ollama 模式"完全不出网"，
  而是明确写出"模型推理不出本机；若你显式开启联网检索，关键词会发到三个公开站点"。
- `BEFORE-INSTALL.txt` 已逐条列出本地推理、云端模型、联网检索、运维可选功能四类去向。
- `/v1/about` 的 `network` 字段现在由后端实时计算，前端"关于与隐私"直接展示当前真实开关状态，
  避免文案再次与行为漂移。
- 未再发现"默认开启但文案没写"的外发域名。

### 10.6 仍未完成（需后续单独处理）

1. `langgraph_workflow.py` 默认构造 OpenAI 客户端（网关未使用，CLI/工作流若被手动运行会直连 `api.openai.com`）。
2. Google/Moonshot/DeepSeek 未接入 `net_guard`（需本机改配置，远程不可利用）。
3. `posthog` 仍作为 `mem0ai` 传递依赖存在（产品代码零导入）。
4. 根账号 `is_root` 的 `/v1/chat/sessions` 会列出 legacy 会话（本次隔离实例因此看到了仓库本地旧会话；纯本地读取，不产生出站）。
