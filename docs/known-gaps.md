# Known gaps

> 需求逐条追溯（哪些做了、哪些只是文档、哪些延后）见
> [需求追溯表](requirements-traceability.md) 与 [总任务台账](MASTER-TASK-LEDGER.md)。
> 本文件负责登记「已明确延后、不得遗忘」的事项；三者互补，缺一不可。

Facts about this checkout that are true today and that we are **not** fixing
now. Each entry says what is broken, how we know, what it affects, and what
would close it. No "should be fine" entries — if it is unverified, it says so.

| # | Gap | Impact | Status |
|---|-----|--------|--------|
| 1 | Local `semgrep` CLI cannot start | Development tooling only | Open, accepted |
| 2 | Gemini real success path unverified (no API key) | Gemini option is wired to `google-genai`; only a fake-key 400 proves the request reaches Google | Open, accepted |
| 3 | `file_write` 的占位内容会被当成真实产物 | 模型可以把任务名/描述原样写进文件，`artifacts` 非空即被算作可交付 | **发布后处理**（本轮只登记，不改代码） |
| 4 | Windows 上 rename 的掉电持久性只有 best-effort | 队列迁移在整机掉电后可能回到 rename 前状态；不损坏数据，最坏多迁一次 | Open（平台限制，见 §5） |
| 5 | 取证沙箱 `rmtree` 删除不是安全擦除 | 删除后磁盘/日志/卷影副本仍可能恢复旧证据 | **保持结论**（数据残留；凭证 ACL 已在保留期内收紧，见「取证沙箱删除不是安全擦除」一节） |

---

## 1. Local semgrep CLI is unusable because of a `mcp` version conflict

**What happens.** Running `semgrep` from this checkout's virtualenv fails at
import/startup: semgrep pulls a `mcp` dependency range that conflicts with the
`mcp>=2.0.0,<3.0.0` pin the product now needs for its MCP adapter (installed:
`mcp==2.1.1`). Either the CLI resolves against the wrong `mcp`, or it refuses to
install the version it wants without downgrading the product dependency.

**Why we are not fixing it.** The conflict is inside the development tool's
dependency closure, not in the shipped application. Resolving it by changing
`pyproject.toml` would mean downgrading `mcp` below the range the MCP adapter
(FastMCP 4.0.3) requires — the product would lose working MCP support to
satisfy a local linter. That trade is wrong, so semgrep stays out of this
checkout's virtualenv.

**Impact.** None on the product at runtime. Static analysis with semgrep has to
run from a separate tool environment instead of this venv.

**How to close it if it matters later.** Install semgrep in an isolated
environment (`uvx semgrep` / a dedicated venv) and point it at the source tree;
or wait for a semgrep release whose `mcp` range includes 2.x.

**Evidence.** `pip install -r requirements.txt` succeeds and the test suite
passes with `mcp==2.1.1` installed; the failure only appears when semgrep is
added to the same environment.

---

## 2. Gemini's real success path is unverified because no API key is available

**What happens.** `GoogleProvider.generate()` now uses the `google-genai` SDK
(`google.genai`), and the lock ships `google-genai==2.22.0` with
`protobuf==6.33.6`. With a deliberately invalid key the call reaches Google
and fails with `400 API_KEY_INVALID`, which proves the client wiring is real.
A **successful** generation has not been observed: this machine has no valid
`GOOGLE_API_KEY` / `GEMINI_API_KEY`.

**Why we are not closing it now.** Verifying the success path needs a funded
Google AI Studio key; we do not have one and will not fabricate a passing
result. Response parsing (single part, multi-part, empty, upstream error,
missing SDK) is covered by a fake `google.genai` client in
`tests/test_provider_google_genai_parsing.py` — that tests our code, not
Google's live endpoint.

**Impact.** The Gemini option is wired and reports failures honestly, but
"fill in a real key and get an answer" is unproven in this build.

**How to close it if it matters later.** Run one real generation with a valid
key (e.g. `GOOGLE_API_KEY=... python -c "..."` against
`GoogleProvider.generate()`), record the response and evidence, then remove
this entry.

---

## 3. `file_write` 的占位内容会被当成真实产物（发布后处理）

**What happens.** `LLMTaskDecomposer` 目前只校验 `file_write` 的 `inputs` 里
`path` / `content` 非空，不校验 `content` 是否真的是一份可交付正文。模型可以把
任务名或描述原样塞进文件（例如 `content="整理好的会议纪要"`），执行器照样落盘，
`artifacts` 非空，目标就被算成"有产出"。

**Why it matters.** 这与本轮修复的「没有产物文件就不算完成」是同一类问题的
两端：一端是 **没有产物却报成功**（已修：`artifacts=[]` 时降级为 `partial`）；
另一端是 **有文件但内容不是交付物**（本项）。两端都不能用"绿"骗人。

**Status.** **发布后处理**。本轮只登记，不改代码：收紧内容校验需要先定义
"什么算正文"（最小字节数 / 与 name、description 不同 / 可解析格式），否则容易
把合法的短文件误杀。改之前必须先补这条验收标准与回归用例。

**How to close it if it matters later.** 在执行前或执行后校验 `file_write` 的
`inputs.content`：不能与任务名/描述相同、不能是明显的占位说明；不满足则不计入
`artifacts`，并按"无产出"处理。验收用例至少覆盖：占位内容 → 不算交付；真实
正文 → 算交付；短但合法的文件（如 `hello.txt`）不被误杀。

---

## 4. 本轮安全修复的端到端验证缺口（2026-11-08）

> 口径：**代码已改、单元/HTTP 层有用例；但没有在真实外部环境跑通整条链路。**
> 任何人引用这些能力时，不得写成「已验证」。每项都给出关闭它所需的最小实验。
>
> **2026-11-08 更新**：4.2（https+真实 Key）与 4.5（Ollama 真实生成）已在本机
> 真实外网 / 真实模型上跑通，状态改为「已端到端验证」，并保留各自仍未覆盖的
> 部分；4.4（PDF 单页超时）改为子进程可抢占并附实测耗时；4.1、4.3 状态不变。

### 4.1 DNS 重绑定 TOCTOU（`src/ai/net_guard.py`）

**已修**：校验通过后按线程固定解析结果（替换 `socket.getaddrinfo`），同线程后续
建连只返回已校验地址。**证据**：用伪解析器（第一次公网、第二次 127.0.0.1）
验证第二次解析仍返回已校验公网 IP。

**未端到端验证**：没有用真实恶意 DNS 服务端 + 真实 SDK 建连验证"TTL=0 切换后
仍连到已校验地址"。

**关闭方式**：起一个可控 DNS 服务，第一次返回公网 IP、第二次返回回环 IP，让
真实 provider 发起连接，抓包确认 peer address 仍是第一次的公网 IP。

### 4.2 带 Key 公网端点强制 https（`src/gateway/models.py` + `net_guard`）

**已端到端验证（2026-11-08，真实 https + 真实 Key，非桩）**。以下证据来自运行中的
网关（同一进程 PID 15332，启动时间 05:27:05，全程**未重启**）：

- **保存**：`POST /v1/models`（`provider=deepseek`、`base_url=https://api.deepseek.com/v1`、
  `model=deepseek-chat`、`timeout=60`）→ 200 / 153ms，`verified=false`，默认模型不变；
  响应里的 Key 为脱敏形式，明文不出网关。
- **不重启即用**：同一进程内 `POST /v1/models/{id}/test` → 200 / 1475ms，真实回复
  `Pong! 🏓 …`；`POST /v1/chat/stream` → 首字 1143.6ms / 总 1272.5ms，真实回复
  「2+2等于4。」，`done.status=completed`、`usage_recorded=true`。
- **http+Key 必失败（反例）**：同一基址改 `http://api.deepseek.com/v1` → 400，原文
  「带 Key 的服务地址必须使用 https；http 明文只允许本机 Ollama/本地推理端口
  （11434/1234/8000/8080）。」
- **TLS 握手与证书来源（2026-11-09 修正）**：`requests` 链路的 `verify` 为 True，CA 取
  `.venv/Lib/site-packages/certifi/cacert.pem`（`certifi 2026.10.23`），只认内置列表；
  但模型调用的主流链路走 `httpx`，默认上下文在 Windows 下加载**系统受信任根证书库**
  （实测 `cert_store_stats={'x509':38,'x509_ca':35}`）并尊重 `SSL_CERT_FILE`。
  因此「本产品不读系统证书商店」对 httpx 链路不成立，界面文案统一改为
  「不同链路信任来源不同，在系统里信任证书不一定生效」；自救入口见
  `docs/TROUBLESHOOTING-USER-20261108.md` 4.1（`SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`）。
  实测 `TLSv1.3` / `TLS_AES_256_GCM_SHA384` / 对端证书 `CN=api.deepseek.com`；
  无 Key 访问同一端点 156ms 返回 401，证明连接真实到达上游。
- **超时与重试发生在哪一步（2026-11-09 更新）**：401 不重试（日志 `attempt=1`、
  200ms 收口，`upstream_kind=invalid_key`）；超时/连接类在 `src/ai/providers.py`
  的 `chat_with_retry` / `stream_with_retry` 最多重试**一次**，网关层不重试。
  新增短超时收敛：`timeout < 5s`（默认阈值，可用
  `LIUHAO_RETRY_MIN_TIMEOUT_SECONDS` 调整）时**不重试**——`timeout=2` 的总耗时
  从 4992~5075ms 降到接近单次（不再为同一句错误多等 2.5 倍）。
  证书 / DNS / 地址拒绝始终 `attempt=1`。
- **失败时用户看到的中文**：错 Key 时 `test` 返回
  「API Key 无效：请检查是否填错或复制不完整」；`/v1/chat` 返回
  「API Key 无效或已失效，请到「系统设置 → 模型管理」更新密钥。（模型：deepseek-chat）」，
  技术详情里上游把 Key 打成 `****0000`（不含明文）。
- **Key 不泄露**：日志只有 `key_fp=sha256:…`；诊断包（zip 14045 字节，逐成员扫描）
  与数据目录/日志共 105 个文件，全文命中 0、前 12 位命中 0。

**仍未覆盖**：只用 DeepSeek 一家真实公网 Key；OpenAI/Anthropic/Gemini/Moonshot 的
**真实** Key 未测（其协议适配见 4.6）。带 Key 的 http 仅对回环
`11434/1234/8000/8080` 放行——自建 OpenAI 兼容服务若监听其它本机端口，仍会被 400
拒绝（本机 18080 实测如此），这是有意收紧，不是缺陷。

### 4.3 远端升级成功链路（`src/upgrade/sources.py` / `downloader.py`）

**已修**：强制 https、拒绝私网/回环、禁 30x、远端包必须带 Ed25519 签名 +
可信公钥；清单本身也签名（`manifest_signature`），并带单调 `sequence` 做防重放/
防降级。固定清单哈希 `manifest_sha256` 已退化为**可选首次引导**，不再需要每次
发布重新下发——客户端只固定公钥即可自动检查。**证据**：签名工具本地闭环
`tests/test_upgrade_signing_e2e.py` 9 条；"无固定哈希自动升级 + 重放/降级/篡改
拒绝" `tests/test_upgrade_manifest_trust.py` 7 条（真实密钥 + 本机 HTTP 服务）。

**未端到端验证**：没有真实公网 HTTPS + CDN + 证书链环境；30x 不跟随只在本机
HTTP 服务上验证；真实公网上的"检查 → 下载 → 应用"整链仍未跑过。

**关闭方式**：把一份签名清单与 ZIP 放到真实 https 静态站点，配置
`LIUHAO_UPGRADE_MANIFEST_URL` + `LIUHAO_UPGRADE_PUBLIC_KEY`（不再需要
`LIUHAO_UPGRADE_MANIFEST_SHA256`），跑一次 `check + download + apply`，记录真实
响应；再用 302 跳转、旧 sequence 清单、旧版本包各跑一次反例。

### 4.4 PDF 单页超时（`src/knowledge/extractors.py`）

**已修（2026-11-08，可抢占解析）**：单流/总解压字节上限；每页计时，超 10s 抛
`PdfPageTimeout`。PDF 解析已整体移到 `spawn` 子进程（`sink.get` 计时 → 超时
`Process.kill()`），父进程只收页与组装，进程、队列句柄在 `finally` 回收。

**实测证据**（`%TEMP%\liuhao-pdf`，`.venv` 复现脚本）：

- 病态页（单页 40 万条 `Tj`，200in × 200in MediaBox）+ 1s 预算：
  改前 **19.11s** 才返回 `PdfPageTimeout`（耗时在页返回后才判定）；
  改后 **1.95s** 返回，进程被强杀，`multiprocessing.active_children()==[]`。
- 正常 PDF 解析本身不变（5 页 0.06s / 50 页 0.50s），但每次上传多一次子进程
  启动开销：改前 0.109s / 0.500s → 改后 0.90s / 1.30s（固定 +0.75~0.85s）。
- 边界：320 页 → `TooManyPages`；加密 → `EncryptedPdfError`；扫描件 →
  `ScannedPdfError`；截断文件 → `CorruptedDocument`；均无残留子进程。
- 回归：`tests/knowledge/test_document_import.py` + `tests/test_security_hardening_batch.py`
  64 项、`tests/knowledge/` 225 项全绿；超时用例改为真实慢页 + 0.5s 预算，
  断言「5s 内返回且子进程已回收」。

**残留风险**：子进程启动依赖 `spawn`（宿主 `__main__` 需有 `__main__` 守卫；
打包运行时 `runtime/python.exe` 已满足）。若要求每次上传都消除这 0.8s，
需要常驻预热 worker，属于后续优化而非本缺口。

### 4.5 Ollama 真实生成（`src/ai/providers.py` / `src/gateway/models.py`）

**已端到端验证（2026-11-08，本机真实 Ollama，非 mock）**：

- **环境**：`127.0.0.1:11434`，模型 `qwen2.5:3b`（1.9GB，Q4_K_M，3.1B 参数）。
- **网关链路**（`provider=ollama`，默认端口，同一网关进程）：首字 13409.8ms /
  总 16680.4ms（含 1.9GB 模型冷加载），真实中文回复，`done.status=completed`、
  `usage_recorded=true`；结束帧只带 `usage_recorded`，不含 token 明细（明细在用量账本）。
- **直连对照（token 统计）**：`POST /api/chat` → `prompt_eval_count=34`、
  `eval_count=2`、`total_duration_ms=607`、`load_duration_ms=9`、
  `eval_duration_ms=101`（墙钟 612ms）。
- **自定义端口**：另建 `provider=ollama` + `base_url=http://127.0.0.1:11435`
  （本机转发到 11434）→ 首字 389.9ms / 总 6307.0ms，真实回复；证明自定义端口 +
  无 Key 不会被"必须 https"误伤。
- **反例保留**：带凭据的公网 http 仍被 400 拒绝（见 4.2）。

### 4.6 跨厂商 Key 可用性（2026-11-08 实测结论）

**结论：没有写死某一家**，但"换一家"能否直接用取决于两点：协议形态与地址。

- `provider` 表达的是**协议适配器**，不是厂商：`provider=openai` + 自定义
  `base_url` 可指向任何 OpenAI 兼容服务；`provider=anthropic` 走 Anthropic 的
  `/v1/messages`；`provider=ollama` 走本地 `/api/chat`。展示用的
  `vendor_for(provider, base_url)` 按主机名换算真实厂商，不参与路由
  （`src/ai/model_config.py:110-159`）。
- **实测证据**（本机桩，端口 8000，只验证协议与鉴权头，不是模型服务）：
  OpenAI 适配器请求 `/v1/chat/completions` 且带 `Authorization: Bearer …`；
  Anthropic 适配器请求 `/v1/messages` 且带 `x-api-key: …` +
  `anthropic-version: 2023-06-01`；两者都经网关返回 200 与桩回复。说明 base_url
  与鉴权格式由 provider 决定，未被写死为 DeepSeek。
- **限制**：`SUPPORTED_PROVIDERS` 只有 7 种（`mock/openai/anthropic/google/ollama/
  moonshot/deepseek`），未知协议没有通用适配器；带 Key 的非回环地址必须 https，
  回环 http 仅限 4 个端口（见 4.2）。
- **未覆盖**：只有本机桩的协议证据，没有第二家真实厂商 Key 的真实成功记录。

### 4.7 MiniMax 预设待定（未实测端点形态，暂不提供）

**现状**：`GET /v1/models/presets` 已提供通义（DashScope compatible-mode）、
智谱（`/api/paas/v4`）、火山方舟（`/api/v3`，模型名需填接入点 ID）三家的
OpenAI 兼容预设；**MiniMax 不在列表里**。

**为什么先不给**：其 OpenAI 兼容入口的路径形态（是否带版本段、`/v1` 还是
`/v1/text/chatcompletion_v2` 一类）没有实测过。预设会被用户当成"填 Key 就能用"
的默认值，给错地址只是把 404 提前一步送到用户面前，比没有预设更糟。

**仍可用的路径**：用户选「OpenAI 兼容（自定义端点）」，自行填写 MiniMax 控制台
给出的完整端点与模型名即可，协议适配器无需改动。

**做完的验收标准**：
1. 用真实 MiniMax Key 跑通一次 `provider=openai` + 自填 `base_url` 的对话；
2. 记录最终请求路径与模型名（脱敏，不含 Key）；
3. 按实测值补 `_OPENAI_COMPATIBLE_PRESETS`（`src/gateway/models.py`）并加
   `tests/test_gateway_models_api.py` 断言。

---

# 已明确延后、不得遗忘

> 以下事项**不是**「以后有空再说」，而是已经判定本轮不做、但没有取消。
> 每项写清：为什么延后、依赖什么、什么算做完（可验证的验收标准）。
> 任何人重新捡起这些工作前，先读这一节，不要重复发明或悄悄降级标准。

| 编号 | 事项 | 状态（四类口径） |
|---|---|---|
| D-1 | 自升级 + 受控自改代码 | 部分完成（自升级链已提交；受控自改代码 **决定延后**，见下） |
| D-2 | 管家主面板（一句话 → 分派员工 → 结果汇总） | 部分完成（代码与测试已入库；真实一句话链路未实跑） |
| D-3 | 三个快效技能（定时提醒、本地语音输入、操作前预览） | 部分完成（提醒已入库；语音、预览 **决定延后**） |
| D-4 | 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠） | 决定延后（安全门槛未闭合，理由见下） |
| D-5 | 30 方向战略落地 | 部分完成（真有 8 / 半成品 11；不做项 **决定延后**） |
| D-6 | 情报中心后续阶段（漏洞查询、缺口对照、更新提醒） | 决定延后（依赖 advisory 接线与产品形态，理由见下） |
| D-7 | 知识闭环（入库 UI + 检索 + 对话注入） | 部分完成（代码已入库，真实 E2E 未验证） |
| D-8 | 进程内排程器（定时/循环 Goal） | 部分完成（`3bda8c7d` 已入库，真实定时验收未做） |
| D-9 | 备份 / 恢复 / 导出（一键化） | 部分完成（CLI 已操作真实归档 `dcf025c3`；无设置页/恢复流程） |
| D-10 | 产物索引与执行回溯 | 部分完成（`c14ab20c` + `82f95450`；权限面已审计：HTTP 无跨账号列出/下载入口，下载面按账号分区；未做 UI） |

### 四类归属统计（2026-11-06，按 `docs/strategy-priority-matrix.md` `9772b80a`）

口径：**已完成 / 部分完成 / 决定延后或不做 / 未验证（附原因）**；不再允许
「待办」「进行中」这类既无主人也无理由的状态。

| 登记范围 | 已完成 | 部分完成 | 决定延后或不做 | 未验证 |
|---|---|---|---|---|
| D-1～D-10 交付待办 | 0 | 8（D-1、2、3、5、7、8、9、10） | 2 主条目（D-4、D-6）+ 3 子项（自改代码、语音、操作前预览） | 0 个独立条目（原因在正文） |
| 30 方向（矩阵口径） | 0（未跑运行时 E2E，不标已完成） | 8（「现在」8 项 = in-progress） | 13（「不现在做」= 不做 8 + 仅设计 5） | 0 |
| **合计（去重前）** | **0** | **16** | **18**（2 主条目 + 3 子项 + 13「不现在做」） | **0** |

> 合计是登记条目数，不是独立能力数：D-7 与方向 3、D-8 与方向 8 等有重叠。
> **「下一阶段」9 项（2、5、7、9、16、17、20、24、29）是 planned-next、计划做，不是
> 「决定延后」**，故不在上表「决定延后或不做」列里；它们记「待做」，见
> [总任务台账](MASTER-TASK-LEDGER.md) D-04。原表把 9 项并入延后且合计写 24，与
> 「2 主条目 + 3 子项 + 22 方向 = 27」自相矛盾，本次一并修正。

**接手方（按仓库实际流）。** D-1 = upgrade 流；D-2 = console + gateway 流；D-3 =
tasks 流（提醒已落），语音与预览需新任务；D-4 = automation 流；D-5 = 战略流；
D-6 = intel 流；D-7 = knowledge 流；D-8 = tasks 流；D-9 = ops 流；D-10 =
artifacts 流。

### 30 方向逐条落地状态（矩阵 `9772b80a`）

标签口径：**in-progress**（「现在」且未完成）/ **planned-next**（下一阶段）/
**deferred-by-decision**（决定不做）/ **design-only**（只做设计不落地）。
来源均为 `docs/strategy-priority-matrix.md` §1（`:20-52`）。

> **与 T-11「真有 8」不是同一把尺子**：T-11 的「真有 / 半成品 / 假实现 / 无实现」
> 是**静态存在判定**；本节的 in-progress / planned-next / design-only 是**排期优先级**。
> 因此 25 可以是「真有」（有 CLI）同时是 `design-only`（不现在投入）；16、29 同理。
> 「现在 8」= 1、3、8、22、23、26、28、30，与 T-11 的「真有 8」（1、8、16、22、23、25、29、30）
> **集合不同**，两轴不可相加。原文档未写这条，易被误读为矛盾。

| # | 方向 | 标签 | 当前到哪一步 / 理由 |
|---|---|---|---|
| 1 | 通用智能与认知架构 | in-progress | 真实主链（`liuhao.py`/`providers.py`/`planner`/`AgentRuntime`）已在；未跑运行时 E2E |
| 2 | 多智能体协作 | planned-next | 依赖 8、24、3；生产只走单员工 resolver，committee/blackboard 仅测试引用 |
| 3 | 长期记忆与知识图谱 | in-progress | documents→chunk→embedding→vector_store 真链路；`/v1/chat` 已接 `build_knowledge_context`；缺真实 embedding E2E 与入库 UI 完整闭环 |
| 4 | 自主研究与科学发现 | design-only | 零实现；只设计状态机与安全边界 |
| 5 | 多模态智能 | planned-next | 依赖 1、3；`perception.py` 仅文本，图像/音频/视频显式未实现 |
| 6 | 电脑自主操作 | deferred-by-decision | 三闸未全开返回 `[]`，唯一后端 `fake.py`；放开 OS 控制风险大于收益 |
| 7 | AI 软件工厂 | planned-next | 依赖 26；仅受限 Python + 单文件写入，不做任意仓库改码/PR |
| 8 | 自动化工作流 | in-progress | Goal/AgentRuntime 真；进程内 SQLite 排程器 `3bda8c7d` 已入库；缺真实定时 Goal 跑满周期 + 重启恢复 |
| 9 | 全球信息情报 | planned-next | 依赖 8、3、25；只做本地订阅摘要，不做情报平台 |
| 10 | 数字孪生与仿真 | deferred-by-decision | 零实现，单机无采集面 |
| 11 | 物联网 | deferred-by-decision | 无 MQTT/Modbus/OPC-UA/Zigbee 代码，缺设备与 7×24 运维 |
| 12 | 机器人与具身智能 | deferred-by-decision | 无 ROS/Gazebo/运动控制，安全关键作动需硬件与认证 |
| 13 | 云端与边缘计算 | design-only | 只设计可选出口；默认形态单进程单端口、不依赖 Docker |
| 14 | 空间智能 | deferred-by-decision | 无 GIS/地图/点云/3D 代码，缺空间数据与 GPU |
| 15 | 工业自动化 | deferred-by-decision | 无 PLC/SCADA/OPC-UA，缺设备与资质；法律责任边界 HUMAN DECISION REQUIRED（本项目尚无已实现的责任框架） |
| 16 | 企业管理智能体 | planned-next | 依赖 22、8；内部运营可用，外部连接器只设计（依赖 27） |
| 17 | 金融经济分析 | planned-next | 依赖 16、23；只保留 token 成本账本，交易/投资建议不做 |
| 18 | 个性化教育 | deferred-by-decision | 38 个行业模板仅测试导入，无学生模型/课程 |
| 19 | 创意生产 | deferred-by-decision | 插件未加载、无生成执行路径；需 GPU/云 API 且竞争无胜算 |
| 20 | 个人数字生活管理 | planned-next | 依赖 8、3、28；画像与会话已真，提醒运行时已入库，日程未做 |
| 21 | 分布式系统 | design-only | 只设计一致性契约；无多节点部署与集群调度 |
| 22 | 网络安全与数字身份 | in-progress | security/gateway/审批真实；外部 IdP/KYC 不做 |
| 23 | 数据基础设施 | in-progress | 6 迁移 + 主库 + 8 个 SQLite；路径收口与备份进行中，不做数仓 |
| 24 | 自适应与持续优化 | planned-next | 依赖 8、23；反馈服务仍是内存 dict，`evolution.py` 仅测试引用 |
| 25 | 开源技术情报 | design-only | 保留 CLI；无控制台/调度，快照 2026-09 已过期 |
| 26 | 数字资产管理 | in-progress | 产物索引 `c14ab20c` 已入库；缺权限过滤/UI，不做重型 DAM |
| 27 | 隐私计算与可信 AI | design-only | 只设计 PII 强制点；MPC/TEE 单机做不出真实隐私计算 |
| 28 | 高可用与灾难恢复 | in-progress | `backup.py` 加密归档真实但无生产调用方；缺调度与恢复流程，不做复制/切换 |
| 29 | 跨平台生态 | planned-next | 依赖 1/8、22；只维护 PWA/SDK/桌面集成，不做原生 App |
| 30 | 人机协同决策 | in-progress | 审批签发/列表/撤销 + enforcement + 审计链真实；committee 未接线 |

### 决定不做 / 只做设计的合并清单（矩阵 §2）

| 判定 | 项 | 理由（一句） |
|---|---|---|
| deferred-by-decision（不做） | 6 电脑自主操作、10 数字孪生、11 IoT、12 机器人、14 空间智能、15 工业自动化、18 教育、19 创意 | 风险大于收益 / 无采集面 / 缺设备与运维 / 安全关键作动 / 缺空间数据 / 缺工业资质 / 模板 Demo / 无 GPU 竞争力 |
| design-only（只做设计） | 4 科研、13 云边、21 分布式、25 开源情报、27 隐私计算 | 零实现或与单机形态冲突 / 单机单进程 / 低频工具保留 CLI / MPC/TEE 无落地条件 |

---

## 5. 队列迁移的 rename 掉电持久性在 Windows 上只有 best-effort

**What happens.** `src/tasks/_queue_paths.py` 的队列迁移（`tasks.db` / `tasks.journal` /
`reminders.db` / `reminders_outbox.jsonl`）在 `os.replace` **之前**对临时文件做了
`fsync`（Windows 上用 `O_RDWR` 打开后走 `FlushFileBuffers`），**文件内容本身已经落盘**。
但 rename 之后对**目录**的 `fsync` 在 Windows 上不可用：`os.open(<目录>, O_RDONLY)`
拿不到可用的目录句柄，`src/common/atomic_write.py` 的 `_fsync_directory` 在那里是
best-effort、静默跳过。因此：

* 进程级故障（崩溃、被强杀）：安全。临时文件 + 原子 rename 保证要么旧状态、要么新状态；
* 整机掉电：rename 这一步是否已持久化**没有保证**。可能回到 rename 前（新位置没有、
  旧位置仍在），也可能已经生效。两种状态都不损坏数据，但下次启动可能再迁一次。

**Impact.** 数据不丢、不损坏；最坏情况是多做一次复制迁移，多花一点启动时间。
POSIX（Linux / macOS）不受影响：`_fsync_directory` 在那里真正执行。

**How to close it.** 只有改成 `ReplaceFile` + 卷句柄 `FlushFileBuffers` 方案（替代
`os.replace` + `_fsync_directory`），并在 Windows 上实测掉电/断电注入，才算关闭。
在此之前不得把这条路径写成「掉电安全」。

**Evidence.** 迁移完整性、跨进程互斥、持锁强杀恢复与孤儿 staging 清理的用例在
`tests/tasks/test_queue_data_root_migration.py`；实测数据记录在
[test-isolation-gap.md](test-isolation-gap.md)。

---

## D-1 自升级 + 受控自改代码

**当前状态（2026-11-06 对齐）。** 自升级链已**提交**：`85e1f639` / `3afa5a56` /
`4b519bbe` 引入 `src/upgrade/`（manager/dataguard/backups/selfcheck），并带
`tests/test_upgrade_selfcheck.py`。但 CI run `42127243953` 上
`test_unreadable_database_fails_the_check` 仍红（提交态失败，不是工作树问题），
因此只能记「部分完成」，不能记「已完成」。

**受控自改代码：决定延后（deferred-by-decision）。** 让产品改自己的代码并热更新
是最高风险能力：一旦被提示注入或错误模型输出驱动，等于把「已被审计约束的系统」
换成「能重写审计约束的系统」。这不是缺时间，而是**决定不现在做**。

**依赖什么。**
1. 发布物签名与哈希校验（安装器/打包流）；
2. 审批中心（`src/gateway/policy.py`）与人工确认票据（`src/automation/confirm.py`）；
3. 版本回滚与数据库迁移向后兼容（`alembic/versions/` 需先解决 SQLite-only DDL）；
4. 执行围栏（`LIUHAO_EXECUTOR_FENCE` 相关，见 `scripts/verify_deployment_gate_vars.py`）。

**验收标准。**
- 升级包必须有签名，签名校验失败时拒绝安装并留审计；
- 自改代码必须走「生成 diff → 人工审批 → 沙箱测试 → 原子替换 → 失败自动回滚」；
- 端到端演练一次：升级失败后系统回到原版本且数据无损；
- 全流程写入审计链并可查询。

## D-2 管家主面板（一句话 → 分派员工 → 结果汇总）

**当前状态（2026-11-06 对齐）。** 已**提交**：`94003942` 建管家面板，
`3510cab3` 接线「点名派给员工 / 失败任务重试 / 增量进度 / 待办审批」，前端
`apps/console/console/src/pages/Butler.tsx` 自述链路为
`POST /v1/goals(background) → GET /v1/goals/{id} 增量 → 汇总`，并带
`Butler.test.tsx`、`lib/butler.test.ts`。**未验证**的是真实一句
「一句话 → 建目标 → 分派 → 执行 → 汇总」的端到端运行（前端单测不等于链路可用），
因此记「部分完成」，不记「已完成」。

**依赖什么。**
1. 目标/规划链路（planner + workflow）真实可用并支持多员工分派；
2. 员工状态与执行结果的结构化汇总接口；
3. 控制台管家视图（不是聊天页改名）。

**验收标准。**
- 用户只输入一句话，系统创建目标、生成任务、指派 ≥1 名员工并开始执行；
- 执行完成后在同一会话返回结构化汇总（谁做了什么、结果、失败原因）；
- 可单独指派某个员工并从 UI 看到其当前任务与结果；
- 全链路数据落库，刷新页面/重启服务后仍可见。

## D-3 三个快效技能（定时提醒、本地语音输入、操作前预览）

**当前状态（2026-11-06 对齐）。**
- **定时提醒：已入库，部分完成。** `manage_reminders` 成为第 10 个内置工具
  （`src/ai/tools/`，`a5508a2a` 前后任务流提交），底层是 `3bda8c7d` 的进程内
  SQLite 排程器，并带 `tests/tasks/test_reminders.py`。**未验证**「创建后重启
  服务仍按时触发」的真实定时端到端。
- **本地语音输入：决定延后。** 需要离线 ASR 模型接入产品进程与设备权限/隐私边界，
  本机仅有开发侧 `local-asr` 技能，未接线。
- **操作前预览：决定延后。** 现有只有确认票据里的文本 `preview` 字段
  （`src/automation/confirm.py:31`、`src/automation/contracts.py:156`），没有面向
  UI 的结构化预览与撤销；且它依赖 D-4 的真实自动化后端同时上线。

**依赖什么。**
- 提醒：持久化调度（进程重启不丢）+ 通知渠道；
- 语音：本地 ASR 模型与设备权限、隐私边界（音频不落盘）；
- 预览：自动化动作计划的结构化渲染 + 审批票据 + 撤销。

**验收标准。**
- 提醒：创建后重启服务仍按时触发，可在 UI 查看/取消；
- 语音：离线识别一段中文音频为文字并可直接发送，音频不外传；
- 预览：高风险动作执行前展示「将做什么、影响范围、如何撤销」，未确认不执行。

## D-4 自动化真实后端（浏览器 / 安卓 / Windows 桌面键鼠）

**决定延后（deferred-by-decision）。** 模块自述「本轮只提供 fake 后端，不接真实键鼠」
（`src/automation/__init__.py:1-6`、`src/automation/gate.py:19`）。
真实键鼠/浏览器/安卓控制涉及权限、误操作破坏性与安全边界，必须与审批、
围栏、急停一起上线，不能单独开。

**依赖什么。**
- 三重闸门（装配 / 后端 / 主体）已存在，需要真实后端实现并仍默认关闭；
- 审批票据与急停（`src/automation/estop.py`）在真实动作路径上验证；
- 操作前预览（D-3）与审计（`src/automation/audit.py`）。

**验收标准。**
- 至少一个真实后端（建议浏览器）在隔离环境完成一次可复现任务；
- 未审批动作一律拒绝，急停 1 秒内生效；
- 每个动作产生审计记录，可回放定位；
- fake 后端保留用于测试，生产默认关闭。

## D-5 30 方向战略落地

**当前状态（2026-11-06 对齐）。** `docs/strategy-30-directions.md` 已完成逐方向核查，结论是
**真有 8 个**（1、8、16、22、23、25、29、30）、**半成品 11 个**（2、3、7、13、
17、20、21、24、26、27、28）、**假实现 4 个**（5 多模态、6 电脑操作、18 教育、
19 创意）、**完全没实现 7 个**（4 科研、9 情报平台、10 数字孪生、11 IoT、
12 机器人、14 空间智能、15 工业自动化），逐条证据见该文档 `:48-86` 矩阵。
文档 `:224-233` 明确声明**没有跑运行时 E2E**；五大中心被判定为「命名即空壳」，
不应拆成五套系统（`:135-147`）。所以本轮只做信息架构归位，不做 30 个方向的
全量工程落地。

**依赖什么。**
- 知识入库/检索注入对话（D-7，`src/knowledge/` 已存在但控制台未接）；
- 反馈持久化（`src/feedback/feedback_service.py:13` 仍是进程内 `dict`）；
- 排程与备份（D-8、D-9）；
- 假实现方向的真实链路（多模态/电脑操作/教育/创意各自有独立的验收标准）。

**验收标准。**
- 「真有 8」要有可操作入口与真实链路验证，不是只过静态检查；
- 「半成品 11」要么接线要么在 UI/文档明确标注未接线；
- 「假实现 4」不得出现在对外能力清单；
- 30 方向矩阵的每一行都能指向代码路径或明确的「不做」理由。

## D-7 知识闭环（入库 UI → 检索 → 对话注入）

**当前状态。** `69db011e` 已把知识中心做成可用的导入/文档/分块预览/检索测试
界面；此后 `79bd69c8`（文档台账、完整分块、事务删除、上传、异步任务）、
`095ecdb9`（相关性门限与拒答阈值）、`8858c77e`（检索无相关内容时跳过注入）、
`82404fdb`（拒答契约与注入门禁测试）继续补齐。`/v1/chat` 通过
`build_knowledge_context` 注入检索上下文（`src/gateway/chat.py:159,185`，
带 `use_knowledge` 开关）。**仍未验证**的是真实 embedding provider 下的端到端
链路（上传→切分→向量→对话引用来源），以及「未配置 provider 时返回 503」的
实际行为。`tests/knowledge/test_documents_admin_api.py` 复跑 **17 passed**
（此前 422 vs 400 的失败已收口，见 requirements T-19）。

**依赖什么。** 可用的 embedding provider（未配置时必须返回 503「未启用」，不得 mock
伪装）；向量模型已就绪但向量库打不开时必须返回 503「向量库不可用」，不能并成一句
「未启用」；对话侧引用来源展示。

**验收标准。** 上传文档 → 真实切分 → 真实 embedding → 落库 → 对话能引用
检索片段并显示来源；未配置 provider 时明确 503 而非假装可用；向量库打不开时明确
503「向量库不可用」，同样不得伪装可用。
**触发条件。** 见 K-1。

## D-8 进程内排程器（定时/循环 Goal）

**当前状态。** `3bda8c7d` 已入库进程内 SQLite 排程器（重试退避、死信、
重启恢复），代码在 `src/tasks/`，单测 `tests/tasks/test_local_scheduler.py`、
`tests/tasks/test_taskiq_queue.py`。**仍未验证**的是「真实定时 Goal 跑满一个
周期并重启恢复」的端到端行为（单测只覆盖调度器本身，不等于 Goal 链路可用）。

**依赖什么。** 调度器接入 Goal 创建入口（当前需要显式调用），SQLite 持久化
调度表、单进程锁、失败重试与审计。
**验收标准。** 创建定时 Goal 后重启服务仍按时触发；同一时刻不重复执行；
失败有重试与审计记录；不引入 Redis/外部队列（保持零运维）。
**触发条件。** 见 K-2。

## D-9 备份 / 恢复 / 导出（一键化）

**当前状态。** `dcf025c3` 已让 CLI 备份命令操作**真实归档**（`src/cli/backup_commands.py`：
SQLite `VACUUM INTO`、归档元数据随包、跨进程可读）；本轮实测命令路径可用。
但 `scripts/ops/backup.py:11` 仍自述「CLI but no production caller wired」，
且**没有**设置页入口、没有恢复流程、没有版本兼容校验 —— 所以只是「CLI 真了」，
不是「一键化可用」。

**依赖什么。** 数据库在线备份一致性、恢复校验、导出格式与权限控制。
**验收标准。** 设置页一键备份 → 产出可校验文件 → 新环境一键恢复到可用状态；
恢复失败可回滚；全过程留审计。
**触发条件。** 见 K-3。

## D-10 产物索引与执行回溯

**当前状态。** `c14ab20c` 增加只读产物索引 CLI 与工作区报表，`82f95450`
把产物索引接入执行链（**可选、默认行为不变**，显式开关才生效），单测
`tests/artifacts/test_artifact_index.py`、`test_artifact_report.py`、
`test_artifact_wiring.py`。**仍未做**：控制台入口、执行回放界面。

**权限面审计（2026-11-07）。** 产物索引只有本机 CLI / 报表入口，HTTP 没有
任何"列出产物索引或按下标下载"的路由（`/v1/artifacts` 返回 404）；真正的产物
下载面是 `/v1/files/content`，已按 `accounts/<owner>/` 分区，跨账号读取返回中性
404。文件列表的 `rel_path` 现为工作区相对路径（含账号前缀），前端回填到
list/read 后仍落在正确账号空间。回归见
`tests/test_cross_account_isolation.py::TestArtifactAccountIsolation`。

**依赖什么。** 执行记录结构化、产物命名与归属、控制台/回放入口（下载面已按账号
分区，索引本机入口不跨账号）。
**验收标准。** 给定目标或执行 ID，能列出其产物、来源步骤与时间；点开可预览；
越权不可见。
**触发条件。** 见 K-3。

---

## 四步用户链路暴露的缺口（本轮实测）

> 来源：`docs/END-USER-GUIDE.md` 的四步走查（静默安装 → 首次启动 → 填 Key →
> 发第一条消息）。以下均为**未修或未复验**的事实，不得当成已完成。

| 编号 | 缺口 | 影响 | 状态 |
|---|---|---|---|
| E-1 | ~~`packaging/stage_payload.py` 的延迟依赖修复尚未提交~~ **已解除**：修复随 `b789d244` 入库（含 `jiter`、`httpx2/httpcore2/truststore` 与运行自检） | — | 已提交（`b789d244`） |
| E-2 | 首次运行向导只保存 Key，不做连通性测试 | Key 无效/模型名错要到发第一条消息才暴露 | 未改产品；文档改为引导去「模型管理 → 测试连接」 |
| E-3 | 本机非 Ollama 的 OpenAI 兼容端点：聊天可用，但 `/v1/models/{id}/test` 仍按 SSRF 拒绝 | 「测试连接」与聊天两套校验不一致，本地 LM Studio 用户会被误判 | 安全相关，本轮未改 |
| E-4 | ~~静默卸载（`/VERYSILENT`）停在“保留数据”确认框~~ **已修**：`installer/liuhao.iss` 的 `CurUninstallStepChanged` 在 `UninstallSilent` 时直接退出，按安全默认保留数据，不再弹确认框 | 批量部署/自动化卸载不再挂住 | 已修（安装器实测 `/VERYSILENT` 卸载可正常结束）；交互式卸载的“删除数据”分支已单独实测 |
| E-5 | 真实厂商 Key 未由本流复验 | 本轮只用本地 OpenAI 兼容桩验证 HTTP 请求路径 | **已关闭（2026-11-08）**：安装版真实 DeepSeek Key 已取证——`dist/release-gate/smoke/20261108-real-key/CONCLUSION.md`（未重启后端 PID 16404→16404，`POST /v1/chat` 200 / 1.90s；流式 12 帧、工具调用、Key 脱敏均 PASS）与 `20261108-034245-real-key/live_summary.txt`（8 项 PASS）。原「无原始输出」的限制已被上述证据取代；Gemini 成功路径仍未验证，见 §2 |
| E-6 | `packaging/smoke_payload.py` 接受 MockProvider 通过 | 打包冒烟无法发现真实 provider 依赖缺失（`httpcore2/jiter` 就是这样漏出的） | 未改；真实链路验证在仓库外的 E2E 脚本 |
| E-7 | 前端正在重写，安装包内是当前 `console/dist` | 前端稳定后必须重建安装包，否则界面过时 | 待重建 |
| E-8 | 安装包未由**公开权威 CA** 签名 | Windows 首次运行提示「未知发布者」，部分杀软误报 | **已部分关闭**：项目自签名证书已实装，`Get-AuthenticodeSignature dist\installer\LiuHao-Setup-0.1.1.exe` = `Status=Valid`，主体 `CN=鎏灏智能中枢, O=LiuHao AI OS, C=CN`；未导入信任仍提示未知发布者，SmartScreen 信誉不足。公开 CA 签名仍未做。安装器首屏文案仍写「未做代码签名」，见 E-24 |
| E-9 | 真实双击路径未逐字实跑：本轮 E2E 用 `launch_installed.py --skip-wizard --no-browser` 复用向导函数，没有在命令行窗口里逐条键入答案，也没有复验「向导成功后浏览器自动打开」 | 向导提示文案、输入容错与自动开浏览器三处只有代码依据，没有本轮实测输出 | 未验证；组件函数与 UI 可达性已单独验证 |
| E-10 | ~~测试删除护栏在“仓库位于 temp 根下”时整体放行~~ **已修**：`tests/conftest.py` 改为 fail-closed。放行只认两种归属：目录项与解析后目标都在声明的临时根内（`gettempdir` 或显式 `--basetemp`），或仓库内路径经 `allow_repo_deletion` 登记；仓库归属优先于临时根，符号链接两侧都校验，无法确认归属一律拒绝。顺带补上原护栏漏掉的 `os.rmdir` 接口 | 事故记录（修复前）：回归工作树放在 `D:\cache\temp\liuhao-regression` 时，测试清理逻辑真的删掉了 `README.md`、`oss-ecosystem/capabilities/*.yaml`、`src/knowledge/memory.py`，连带 19 failed + 9 errors 全是假失败 | 已修（`392055f7`）。`tests/test_cwd_delete_guard.py` 覆盖：仓库在 temp 根下、无标记目录、链接指向外部、`os.rmdir`、正常 `tmp_path` 清理，整文件 26 passed。同类扫描：全仓无第二处“按路径推断归属”的删除；scripts 里的删除都基于显式 `mkdtemp`/具名目录 |
| E-11 | `tests/kernels/network/test_network.py::TestRouting::test_add_route_and_deliver` 在全量负载下会抖动 | 全量回归 1 次失败（delivered 变 failed）；单跑 2.67s 通过，属回环 HTTP 在满负载下的时序抖动 | 未修；已记录，单跑通过 |
| E-12 | 全量回归基线落后交付 HEAD：跑在 `cc3af30d`，报告时 HEAD 已到 `de1b0d87`（12 个提交） | 全量结果不覆盖期间的 desktop 启动门、升级 API、可观测性、reminder、console 错误读取器修复 | 已补跑这些提交改动的 15 个测试文件（123 passed / 80.85s），但未再跑第二次全量 |
| E-13 | ~~`dist/installer` 为空、清单里的数值对应文件从未存在~~ **已更新（2026-11-09）**：`dist/installer` 顶层现有 0.1.1 的交付物——`LiuHao-Setup-0.1.1.exe` 29,567,136 字节（`49BC50C8…B4DF`）、`liuhao-0.1.1-upgrade.zip` 32,178,761 字节（`9EABA197…3224`）、`SHA256SUMS.txt`、`README-DELIVERY.txt`。但源码已到 `0.1.2`，**0.1.2 包尚未产出**（`dist/` 下按文件名搜索 `*0.1.2*` 为 0），且这版 0.1.1 产物未绑定源码提交（顶层无 `DELIVERY.txt`） | 交付清单不再 404；但读者可能把 0.1.1 产物误当成本次 0.1.2 构建结果，或以为产物可追溯到某个提交 | 0.1.2 包验收通过并原子替换后，回填路径/字节/SHA256 + 对应源码提交，并重跑同一 E2E 绑定到该提交 |
| E-14 | **手机端真机未验证**：服务侧 LAN `192.168.31.171:8000` 返回 200，但手机侧「打开 → 登录 → 发消息」从未跑过。登录阻塞（`LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 未设置时身份行全被拒）**已改**：`src/common/integrity_key.py` 在安装版启动器与源码启动器首次启动时自动生成并注入密钥，落盘数据目录 `config/integrity.key`（POSIX 0600 / Windows `icacls` 仅当前用户），覆盖全新安装、旧数据升级（无签名行补签）、密钥丢失（明确恢复提示，不静默）三条路径；回归 `tests/test_identity_key_bootstrap.py` | 手机侧仍无设备；「密钥不再阻塞」不等于「手机可登录」 | **部分关闭**：密钥自举已实现并有回归（18 passed，含向导/启动器）；**真机登录+发消息仍为未验证**。关闭条件不变：真机在同一 Wi-Fi 完成一次登录+发消息并留存日志/截图 |
| E-15 | **交叉验证 `3fa88be0` 的 C3/C12 FAIL、31 failed**：账号/知识切片提交了测试与调用方，却把被调用实现（`src/ai/model_router.py` 的 `visible_to`、`src/gateway/ai_management.py` 的 `GoalViewer` 与结构化错误体）留在工作区 | 27 条失败 + 1 条采集错误同根因；同一 HEAD 上测试必红；该轮报告只能算**过程凭证，不是交付凭证** | **已关闭（2026-11-06）**。两切片实现已由 `24fca472` / `31e66269` 补齐，`72fd9b17` 上 `--full` 的 C3 与 C12 均为 **7639 collected / 0 failed / 0 errors / 61 skipped**；证据 `docs/evidence/regression-baseline-coverage-2026-11-06.md` |
| E-16 | **最终冻结 HEAD 没有交付级全量凭证**：现有全量是 `bb6b654c` 基线（7458 passed）与 `3fa88be0` 过程凭证（C3/C12 FAIL），均不是最终 HEAD | `RELEASE READY=False`；不能据此对外宣布可交付 | **已完成**（2026-11-06）：冻结 hash `72fd9b17` 上 `--full` 得 `PASS=10 FAIL=0 BLOCKED=1(C11) NOT_VERIFIED=1(C9) ERROR=0`。裁决后 `RELEASE READY=False` 的理由**只剩 C9**（桌面分发形态无真实 CD，属形态差异）；C11 已裁决（[ADR-HC01](adr/ADR-HC01-baseline-binding.md)），机器 `check_hc01` 仍按冻结门设计打印 `BLOCKED`。证据 `docs/evidence/regression-baseline-coverage-2026-11-06.md` §8。**2026-11-07 本轮 26 个提交后 tip 已变为 `b4406497`**：上句凭证只对 `72fd9b17` 成立，新 hash 需重跑 `--full` 后才可再次称「交付级凭证」 |
| E-17 | **打包白名单必须覆盖「方法体内延迟导入」的依赖**：`src/ai/providers.py` 在 `provider.chat()` 内部自建 `httpx.Client`，启动图与 openai 自带的 `httpx2` 都覆盖不到；同类的还有 anthropic / python-docx(+lxml) / pypdf / sqlalchemy(+greenlet) / taskiq。它们都是 `requirements.txt` 的运行期硬依赖，缺了不是优雅降级，而是真实使用时 `ModuleNotFoundError` | 安装包在干净机器上「对话 / 目标拆解 / 知识导入 / 任务代理」整条链路失败；冒烟测试用 MockProvider 发现不了（见 E-6） | **已修**（`3ec7d755`、`b5ee295c`）。**发现方式**：真 Key 对话直接报 `No module named 'httpx'`。**防法**：`_MEASURE_CODE`、`EXPLICIT_DISTS`、`_validate_runtime` **三处必须同步** —— 只加白名单不加测量/自检代码，构建期不会真的把包带进 payload。**实测数字**：构建期运行时自检 58→63 个发行包（httpx/httpcore），补齐全量后 63→72 个发行包；离线运行时实测 `sqlalchemy 2.0.52`、`anthropic 1.3.0` 等均可 import |
| E-18 | **`src/upgrade/startup_probe.py` 把 `/openapi.json` 当健康兜底路径**（原实现：对 `health_paths` 中每条路径只发一次请求、单请求超时 1.0s、采纳首条 2xx 即退出，冷启动首请求常超过 1.0s，于是 `/v1/health` 超时后直接采纳 `/openapi.json`，把“路由已挂载”当“lifespan/数据库已就绪”） | 安装后自检的“服务正常”结论可能来自错误探针；`http_status=200` 成立但 `health_path` 暴露真相 | **已修（`22c2e867`）**。**改法**：① 优先路径改为**轮次重试**：一轮 = 所有优先路径各一次（默认 `/v1/health`、`/health`、`/healthz`），单请求超时保持 1.0s（不因冷启动砍小），轮间退避 0.25s；优先阶段同时受 `startup_timeout_seconds`（90s）与 `health_probe_budget_seconds`（90s）约束，约合 27 轮全超时，轮次与预算均写在 `StartupCheckConfig` 注释里；② 只有连续 2 轮**所有优先路径都明确返回 404/405**（`health_fallback_after_missing_rounds`）才允许尝试 `/openapi.json`，超时、连接失败、5xx 一律继续重试，绝不提前降级；③ 兜底成功时 `StartupReport.health_path_is_fallback=True`，并进入 `to_dict()`、`service_detail` 与自检日志（“route table only, lifespan not proven”），不再当作普通健康通过；④ 超时文案带上实际重试轮次。**测试**：`tests/test_upgrade_selfcheck.py` 23 passed（含新增“前两次超时、第三次 200 ⇒ 仍报 `/v1/health`”与“全部 404 ⇒ 兜底且 flagged”）；`tests/test_upgrade_api.py` 同跑 35 passed。**剩余**：仅在自检沙箱内验证，未在真实安装包冷启动路径上复测（安装器/打包由另一路负责，本轮未动 `installer/`、`packaging/`） |
| E-19 | **未设置 `HUMAN_IDENTITIES_INTEGRITY_KEY` 时，身份页警告泄露内部细节**（关联 E-14）：`GET /v1/identity/principals` 的 `warnings` 原样返回环境变量名、注册表绝对路径、`fail-closed` / `HC-11` 代号与 `scripts/register_human_identity.py`；控制台 `plainNote` 见“整段英文”就把警告替换成与密钥无关的权限串说明，用户既看不到真相、又被说错 | 首启/密钥丢失时普通用户看到机器串或错误解释；真机登录前的第一印象受损 | **已修（`8c04bbcc`）**。警告改为中性中文：① 未启用完整性校验 → “已登记账号暂时不会被接纳，这不等于「没有账号」，启用完整性校验并重新登记后即可恢复”；② 被拒收行 → “有 N 个已登记账号未通过完整性校验，因此不在清单中：…”；③ 启动时登记被拒 → 只给结论，原始原因留在诊断日志（内核已 `logger.error`）。机器可读字段 `registry.integrity_key_env` 保留供诊断工具，界面不渲染。回归 `tests/gateway/test_account_wording.py::test_registry_warnings_are_plain_chinese_when_integrity_key_is_unset`（含中文用户名+空格路径、含堆栈的拒绝原因；纯中文、无机器串、无层级词）；真实 TestClient 复测（临时数据目录、未设密钥）warnings 已为中文。**剩余**：未在真实安装包上删除密钥后复测首启（安装器由另一路负责） |
| E-20 | **中文/空格/非默认盘符路径矩阵：运行时链路已过，安装器落盘与卸载仍未验证**（本轮用「正式 payload 结构 + `scripts/launch_installed.py` + 独立数据目录 + 独立端口」模拟安装版；未运行安装器，遵守安装器由另一路负责的边界） | 用户把程序装到中文/空格路径、数据目录在中文用户名或 D:/E: 盘时可能起不来、写错位置或升级失败 | **部分完成（2026-11-07 实测）**。已实测 4 组并全部通过：① 中文用户名（`LOCALAPPDATA=C:\Users\张三\AppData\Local`，数据目录由默认规则推导）；② 中文+空格安装路径 + 中文数据目录；③ E: 盘 + 中文数据目录；④ D: 盘 + 中文数据目录。每组：首启 ready 4.782–5.297s；控制台 `GET /` 200；注册/登录 200；保存模型配置 200 且 `model_registry.sqlite3` 落在数据目录；`python -m src.upgrade check` 退出码 0 / `up_to_date`；停止后程序目录无 `data/`、无 `.pyc`，密钥/凭据/身份库/模型库/日志/升级状态全部在数据目录（19 个文件）。**未验证（不写成通过）**：a) 真实安装器在中文/空格路径落盘、快捷方式目标、写入开始菜单/卸载登记——复现条件：用 `dist\final-72fd9b17\installer` 以 `/DIR=<中文 空格路径>` 静默安装到临时目录后核对；b) 卸载后程序目录/数据目录残留（含“删除数据”选项）——同上安装后执行卸载并分别列出两目录；c) 浏览器自动打开的可视化确认（本轮以控制台 HTTP 200 代替，桌面交互未肉眼验证）。**已排除的假缺陷**：本轮模拟初次漏拷根级 `error_types.py` 导致启动失败；正式 payload `dist\final-72fd9b17\payload\app\error_types.py` 存在，补齐后四组全过，非产品缺陷 |
| E-21 | **8GB / 16GB 低配内存没有对应硬件，未实测**（本机为 32GB / Xeon E5-2673 v3 12 核 24 线程） | 无法给出低配机器的真实冷启动与首次对话数字；只能外推，不能当验收结论 | **部分完成（2026-11-07 实测）**。本机 3 次冷启动（独立数据目录、独立端口、mock 模型）：ready 4.875–5.031s（`/v1/startup` 自报 4.205–4.302s）；ready 后控制台首字节 0.516–0.562s；首次对话 2.968–3.125s（服务端 `LiuHaoAssistant.chat` 日志 56ms，其余为首次懒加载）；整进程树 RSS 峰值 212.2–212.9MB，网关单进程 176.2–177.0MB。**外推（未验证）**：内存侧 213MB 远低于 8GB，不是瓶颈；瓶颈更可能在 CPU/磁盘冷读，24 线程数字不能外推到 2–4 核，故不给 8GB/16GB 的“预计秒数”当结论。**要补的观测项**：① 冷启动到 `/v1/startup` ready 的分阶段耗时（`stages`）；② 控制台首字节；③ 首次对话（区分懒加载与服务端耗时）；④ 网关 RSS/整树 RSS 峰值与系统可用内存最低点；⑤ 是否发生换页/磁盘 IO 抖动、单核 CPU 占用；⑥ 8GB 上 1 会话 + 控制台并发，16GB 上 3 会话并发。复现条件：在 8GB（2–4 核）与 16GB（4–8 核）机器上按本轮同一脚本（正式 payload + 独立数据目录 + 独立端口）各跑 3 次 |
| E-22 | **安装版系统层体验（托盘 / 开机自启 / 后台驻留 / 单实例）在 0.1.1 新启动器上首次实测**（用正式 payload 结构 + 桌面壳 `scripts/liuhao_desktop.py` + 独立数据目录 + 独立端口模拟安装版；遵守「不动业务逻辑、不动前端视觉」的边界） | 第二次双击会起第二个网关并报端口冲突；托盘右键没有日志入口；托盘退出被误报成「未优雅收尾」——三项都会让普通用户以为程序坏了 | **已修 3 个缺陷（`3d3af233`）**：① 桌面壳此前没有自身单实例，第二次启动会再建一个托盘窗口、再让网关锁失败；改为按数据目录派生命名互斥量（`Local\LiuHaoDesktopShell_<sha1>`，`ERROR_ALREADY_EXISTS`），第二实例经 `RegisterWindowMessage("LiuHao.ShowStatusWindow")` 唤起已有窗口，找不到窗口则打开 `run/instance.json` 公布的 url，实测 0.91–1.22s 内以退出码 0 结束，托盘数 / 监听数均保持 1；② 托盘窗口类名原为随机 uuid、跨进程找不到，改为稳定派生 `LiuHaoTray_<sha1(数据目录)>`，并补右键「打开日志目录」（1005，实测新增 1 个资源管理器窗口）；③ `stop_process` 只读 `desktop-gateway.log`（WARNING+），而 uvicorn 的 `Application shutdown complete` 写在数据目录 `logs/liuhao.log`，导致托盘退出被误报「未确认优雅收尾」；改为同时读应用日志，实测 `graceful=True / shutdown_确认=True / 0.67s`。**其余实测**：关窗后窗口隐藏、网关继续服务（`/v1/startup` 200、python 子进程 1、托盘 1）；托盘退出后端口释放、WAL 清空、重启后账号/会话仍在；只强杀托盘时网关因 `LIUHAO_PARENT_PID` 自行退出，再次启动正常（无残留锁）；自启注册项写入 `HKCU\...\Run`（命令指向 `runtime\pythonw.exe scripts\liuhao_desktop.py --autostart`），在 `cwd=C:\Windows\System32` + 仅系统环境变量下等效启动成功，关闭后注册项删除。回归 `tests/desktop/test_desktop_system_layer.py` 新增 6 条（互斥量/类名稳定性、真实托盘窗口唤起、交接兜底、日志菜单、停止确认来源）。**未验证（不写成通过）**：a) 真实重启机器（只做了等效注册项启动）；b) 真实安装器快捷方式（`{app}\LiuHao.bat`）在已有实例运行时的完整路径——工具终端没有交互 stdin，控制台启动器在首次向导处被 `[FAIL] 首次运行需要在命令行窗口里完成向导` 阻断，未走到 `start_liuhao` 的「已在运行」分支；c) 通知区图标的人工可视化确认（程序侧已验证窗口/菜单消息/唤起可达，F-4 仍保持未验证）；d) `LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY` 与实际密钥不一致时的英文原始提示（`REFUSING registry row ... Remedy: set that variable ...`，含环境变量名与绝对路径）与 `LiuHao.bat` 无论何种结局都打印 `LiuHao stopped. Press any key to close.`——两者分属身份内核与 packaging 生成物，超出本任务边界，只登记不改。**普通用户会不会被卡住**：不会。单实例、关窗驻留、托盘退出、自启、强杀恢复五条主路径都能走完；剩余风险只在「双击快捷方式时是否停在向导」这一未验证路径，以及关控制台窗口时那句英文提示的观感 |
| E-23 | **0.1.0 的 `/v1/health` 把版本号报成 `1.0.0`**（实测：0.1.0 安装实例自报 `1.0.0`，与 `LIUHAO_RELEASE.json`/`pyproject.toml` 的 `0.1.0` 不符）。原因是旧版 health 处理器读错了版本来源，属于 **0.1.0 自身缺陷**，不是升级链引入的回归 | 对运行中的 0.1.0 做版本探测/故障诊断会拿到错误版本，可能误判「装错了版本」或让升级前置判断失真；升级到 0.1.1 后该值正确 | **已修（0.1.1）**：health 改为报告真实运行版本，回归随升级/健康检查测试覆盖。**0.1.0 不回补**：已发布的 0.1.0 二进制不可改，回补需要重发旧版本号，收益为零且会让版本语义更乱；用户升级到 0.1.1 即消失。后续如需支持「识别 0.1.0 旧实例」，只能按已知缺陷解释，不得据此判定升级失败 |
| E-24 | **交付记录与安装器文案滞后于产物**（按边界不手改构建产物与 `installer/**`）：① `dist/installer/README-DELIVERY.txt` 写「`dist\release\LIUHAO_RELEASE.json` 目前没有 `manifest_signature`」，实际顶层 `manifest_signature` 存在，且 `releases[0].signature=true`（`signature_algorithm=ed25519`）；② `installer/liuhao.iss:1054`、`:1060` 首屏写「本安装包未做代码签名」，实际 exe 为自签名且 `Status=Valid`；③ `dist/smoke-report.json` 的 `chat_note` 写「FAIL 属预期」，容易被误读成本次结果（本次 `login`/`chat` 均为 `PASS`）；④ 本文件 E-8 的早期写法「安装包未签名」已滞后 | 读者会把「没有签名 / 冒烟失败」当成现状，误判发布状态 | **已登记，按边界不手改**：①②属安装器/构建产物（其他线负责），③④属文档措辞（本轮已在 README、`INSTALL-WINDOWS.md`、`DELIVERY-CHECKLIST.md` 改正；`smoke-report.json` 本身保留不改写，见 README 的处置说明）。关闭条件：重建安装器时同步文案，或由构建线确认 `README-DELIVERY.txt` 与清单签名状态一致 |
| E-25 | **开发路径扫描「运行文件命中 2」的逐点定性**（来源 `dist/release/dev-path-report.txt:3,9-10`，扫描时间 `2026-11-08T12:23:16Z`）：① `scripts/collect_diagnostics.bat:12` 的 `%APP_DIR%\.venv\Scripts\python.exe` 是**运行时展开**的兜底路径，仅当安装树缺 `runtime\python.exe` 时命中，且只在失败分支 `echo` 到控制台，不写入诊断 zip、不进入日志；② `src/logging_config.py:189` 的仓库绝对路径示例位于 `RedactingFormatter` 类 **docstring**，是脱敏规则的举例说明，不是日志输出模板；运行期 `format()` 调用 `redact_log_text()`，`src/common/redaction.py` 的 `_ABS_PATH_PATTERNS` 会把任意本机绝对路径折叠为 `<本地路径>/文件名` | 两处都不会把开发/安装绝对路径写进用户可见日志或交付物；**判定：无害**。扫描器按「文本中出现仓库路径字面量」计数，不区分 docstring 与运行时变量展开，这 2 条属已知误报 | **已处置（2026-11-09）**：`src/logging_config.py:189` 的绝对路径示例已改为占位写法 `` `D:\\<仓库>\\.venv\\...` ``（**仅注释**，`1 insertion / 1 deletion`，文件行数不变，运行逻辑零改动；改后该文件已无仓库名字面量）。`scripts/collect_diagnostics.bat:12` **明确不改**：`%APP_DIR%` 是运行时展开，安装树缺 `runtime\` 时它是必要兜底，删掉会让诊断脚本在异常环境直接失败；且该字符串只在失败分支 `echo` 到控制台，不落日志、不进诊断包。重新扫描与命中数更新由发布主线在 0.1.2 打包时执行（预计运行文件命中 2→1，余下 1 条为本条保留项） |
| E-26 | **0.1.1 构建未启用 `-StrictReleaseDevPaths`，扫描不阻断构建**：`scripts/release/package-frozen.ps1:425-426` 的 `$buildArgs` 只传 `-Version`/`-OutRoot`/`-PythonExe`，没有该开关；能力本身已具备——`packaging/build_windows_installer.ps1:46`（参数定义）、`:265`（转成 `--strict-dev-paths`）、`packaging/make_release.py:633`（运行文件命中即失败）、`:778`（参数定义） | 源码里的开发路径字面量可随包交付而构建仍成功；「命中 0/2」只能靠人读报告，不会拦截 | **已登记缺口，本轮未修**（边界：不改 `packaging/**`）。**谁负责/在哪开**：发布打包线在 `scripts/release/package-frozen.ps1:425-426` 的 `$buildArgs` 增加 `"-StrictReleaseDevPaths"`（或在该脚本内默认强制），使每次真实构建都把运行文件命中当失败。落地前必须先处置 E-25 的 2 处，否则构建会被预期地打断。**不加的后果（当前事实）**：未传 `-StrictReleaseDevPaths` 时 `--strict-dev-paths` 不生效，`packaging/make_release.py:633` 的失败分支不会触发——**这道门是关着的**：本次构建扫出 2 处运行文件命中仍然放行并产出了包，此后任何新增的开发路径字面量同样不会阻断出包。**修复后的验收口径**：① `dist/release/dev-path-report.txt` 的「运行文件命中」应为 0（或逐条写明豁免项与理由）；② 人为植入一处命中做反例时，构建必须**非零退出**，且不得在 `dist/installer` 留下新产物或 `SHA256SUMS.txt`（不得静默产出包） |
| E-27 | **`packaging/PAYLOAD-CONTENT.md:85-86` 的计数声明与产物不符**：文档写「本轮打包扫描：运行文件命中 0」，而同目录交付物 `dist/release/dev-path-report.txt:3` 实测「运行文件命中：2」 | 属「不可复算的计数声明 / 文档与产物不一致」：读者会以为源码已无开发路径字面量，实际仍有 2 处（见 E-25） | **已登记，按边界不改**（`packaging/**` 归另一线）。关闭条件：E-25 残余命中清零并重跑扫描后，把 L85-86 改为可复算的实测值；若保留豁免，必须写明豁免项与理由。**更正归属**：由发布主线在出 0.1.2 包时一并更正（本文件只登记需求，不改 `packaging/**`） |

---

## 2026-11-07 本轮缺口状态（与台账 §10 对齐）

> 基线 `b4406497`（`origin/p36`）。下表只回答「关没关、证据在哪」，逐条修复说明见
> [MASTER-TASK-LEDGER.md](MASTER-TASK-LEDGER.md#10-本轮修复登记2026-11-07) §10。
> **代码层已修 ≠ 交付层已验证**：凡标「部分」的，必须按右列条件复验后才可改为关闭。

### 发布回归红（`b4406497`，CI `42386438527`）——先修这些才能谈关闭

两腿数字一致：`17 failed / 7705 passed / 40 skipped / 3 xfailed`；Guardrails、Guardrails Extended、Security Scan、Linting、Observability、P0 Gate 均被 CRITICAL-5 看门狗判红。本地同 venv 复跑 16 failed，**确认是真回归，不是 CI 环境问题**。

| 编号 | 失败面 | 用例数 | 根因 | 修复方向（超出 docs-only 边界） |
|---|---|---|---|---|
| F-01 | 升级状态/备份/自检落点 | 9 | `6a2e361e` 的 `tests/conftest.py` autouse fixture 全局 `LIUHAO_DATA_DIR=tmp_path/data`，升级器优先读该变量 | fixture 放行升级用例，或升级测试显式传 `data_dir` |
| F-02 | profile API | 3 | `b4d4052b` 让 `src/gateway/chat.py` 直取 `assistant.provider`，`FakeAssistant` 无此属性 | `getattr` 兜底或补齐 Fake |
| F-03 | 会话列表全量顺序为空 | 1 | `e474b562` 会话键/迁移在用例间留下状态，单跑通过、全量失败 | 迁移幂等 + 用例隔离 + 补重启列用例 |
| F-04 | 部署门禁变量判定 | 2 | `e474b562` 新增 `PERSONAL_CONTEXT_DB_PATH` 未登记 `SEED_GATES`/`ALLOWLIST`，另有 4 处引用行号漂移 | 登记变量并更新引用行 |
| F-05 | 依赖诚实性 | 1 | `6a2e361e` 新增测试文件带 UTF-8 BOM（`EF BB BF`） | 去 BOM 重存 |


### 本轮关闭（有可复现回归）

| 缺口 | 状态 | 验证位置 |
|---|---|---|
| 员工库/身份库并发写互相覆盖 | **已修**（`c9a83cd5`） | `tests/ai/test_employee_store_concurrency.py` |
| 凭据/API Key/主密钥/planner 状态并发写丢键 | **已修**（`4fa50ff9`） | `tests/test_persistence_concurrency.py` |
| 设置/注册表/RBAC/审计水位/插件/可观测性并发写 | **已修**（`f206cc51`） | `tests/test_persistence_concurrency_secondary.py` |
| 账号根初始化失败后锁死 | **已修**（`6a2e361e`）；⚠ 连带回归 F-01 | `tests/gateway/test_account_root_init_recovery.py` |
| 老根无恢复码、无法本机自救 | **已修**（`49bf52bd`） | `tests/gateway/test_account_recovery_legacy_upgrade.py` |
| 日志多进程轮转丢/重记录 | **已修**（`6a2e361e`、`fd396351`） | `tests/observability/test_logging_multiprocess.py`、`test_logging_rotation.py` |
| 管家结论被并发终态覆盖（目标/任务终态回退） | **已修**（`b61bf26a`） | `tests/test_persistence_concurrency.py` |
| 摘要把全角标点/句子截断 | **已修**（`f8f5d73b`） | `tests/ai/test_llm_support_summary_guards.py` |
| 目标/聊天持久化与流式失败被静默吞掉 | **已修**（`301eded5`） | `tests/gateway/test_goal_truth_concurrency.py`、`test_chat_failure_truth.py` |
| 聊天回显上游英文异常、演示模式无标识 | **已修**（`b4d4052b`、`1c802f17`）；⚠ 连带回归 F-02 | `tests/gateway/test_chat_failure_truth.py`、`test_failure_copy.py` |
| 项目/知识库/产物跨账号越权 | **已修**（`913e9b5a`、`682ddb06`） | `tests/test_cross_account_isolation.py`、`tests/knowledge/test_documents_admin_api.py` |
| 身份页警告泄露环境变量/绝对路径（E-19） | **已修**（`8c04bbcc`） | `tests/gateway/test_account_wording.py` |
| 交付面文案含内部术语、许可证未随包 | **已修**（`f54c14b1`、`b4406497`） | `packaging/licenses/*`、`uiCopyGate.test.ts` |

### 本轮部分完成 / 仍为已知

| 缺口 | 状态 | 还差什么 |
|---|---|---|
| 老记忆迁移与会话列表重启为空 | **部分**：实现已入库（`e474b562`），**未新增自动化回归**，且回归红 F-03/F-04 | 补迁移幂等与会话重启用例，登记 `PERSONAL_CONTEXT_DB_PATH`，并在真实数据目录复验 |
| 安装器 D2–D5、D 盘默认路径 | **部分**：代码已修（`9e537633`），现有 0.1.0 包构建于 11-06 17:29，早于该提交 | 用新提交重建安装包并按 E-20 的 a/b/c 三项复验 |
| 长跑治理（审计增量校验/保留水位/目标归档） | **部分**：代码已修（`fd396351`）并有回归 | 在 `b4406497` 上重跑 2h soak，确认 p50/内存/DB 增长不再随时长上升 |
| 首启自检探针（E-18） | **部分**：代码已修（`22c2e867`） | 在真实安装包冷启动路径复测，而非只在自检沙箱 |
| 最终包凭证（E-13、E-16） | **部分**：E-16 凭证绑定 `72fd9b17`（**已作废**），`b4406497` 的 CI 为 failure；新 tip `416f7109` 的本轮凭证见台账 §11.2 | 在**通过 P-1～P-7 的冻结 hash** 上重跑 `--full`，并把路径/字节/SHA256/源码提交回填 `DELIVERY-CHECKLIST.md` §0 |
| E-5 真实厂商 Key / Anthropic 原生端点 | **仍开放** | 真实 Key 跑通首条消息与失败分类并留原始输出 |
| E-8 安装包代码签名 | **仍开放** | 证书与签名流水线；失败拒绝安装并留审计 |
| E-14 手机真机登录 | **仍开放**（仅密钥自举已关闭） | 真机同一 Wi-Fi 完成登录+发消息 |
| E-20 安装器中文/空格/非默认盘符落盘与卸载 | **仍开放** | 真实安装器 `/DIR=<中文 空格路径>` 安装 + 卸载残留核对 |
| E-21 8/16GB 低配 | **仍开放** | 低配硬件实测冷启动/首条消息/换页 |
| F-1～F-6 真实故障面 | **仍开放** | 真睡眠、真断网、真 ENOSPC、真托盘、双窗口、真时钟 |
| 席位/授权（seat）类 | **仍开放**：本仓库无席位计费/授权数/并发席位实现 | 明确席位模型、授权校验点与回归 |
| 仅 Windows x64 | **仍开放**：无 macOS/Linux/arm64 产物 | 目标平台产物 + 干净机安装复验 |
| 真实远端升级源（C9） | **仍开放**：只在本机安装树验证 | 真实 CD 源拉取/回滚并留存证据 |

## 2026-11-08 新 tip（`416f7109`）缺口登记

> 台账里对应 [§10.3](MASTER-TASK-LEDGER.md#103-2026-11-08-新登记缺口七项逐项写死关掉标准)（G-9～G-12）。
> **登记不等于可以忽略**：G-9/G-10 是「结论缺失」，其余是「环境/形态缺口」；两类都可带缺口发布，
> 但必须随交付说明披露。

| 编号 | 缺口 | 为什么仍然开着（事实） | 关掉标准 |
|---|---|---|---|
| G-9 | **Gemini 真实成功调用未验证** | 真实请求能到 Google 并回 `400 API_KEY_INVALID`（证明客户端接线是真的）；成功路径只有 `tests/test_provider_google_genai_parsing.py` 的假客户端。本机无有效 `GOOGLE_API_KEY`/`GEMINI_API_KEY` | 真实 Key 成功一次非空回复并留证（时间戳/耗时/模型 id/回复摘要，不含 Key） |
| G-10 | **自定义 Base URL 的 SSRF 风险——结论待评估** | 有保存/探测面防护（`src/gateway/models.py:807-855` 公网校验、`:712` 拒跨域重定向）；未回答 DNS 重绑定、`allow_private=True` 的调用路径边界、允许/拒绝清单。**不写成「风险已消除」** | 书面结论 + 私网被拒/DNS 重绑定被拒两条实测（细则见 [DELIVERY-CHECKLIST §7.2](DELIVERY-CHECKLIST.md#72-自定义-base-url-的-ssrf-风险结论待评估不写成已通过)） |
| G-11 | **24h 浸泡进行中** | 长跑 harness 正在跑（跨日结束）；2h soak 与 5 账号数字已有，24h 结论未出 | 跑完并给 p50/p95、RSS、审计库/WAL 增长曲线证明不随运行时长线性上升；中途崩溃算不通过 |
| G-12 | **8/16GB 低配未实测** | 本机 32GB / 24 逻辑核；低配只有「不外推」说明 | 8GB、16GB 各跑首启 + 基础对话 + 30 分钟常驻，记录峰值 RSS 与换页/OOM |
| G-3 | **仅 Windows x64** | 只有 Windows x64 安装树；无 macOS/Linux/arm64 | 目标平台产物 + 干净机复验 |
| G-4 | **无真实 CD / 无真实远端升级源** | 升级回滚只在本机安装树（`c9-*`/`c9b-*`）；没有任何一次从真实 CD 拉取、校验、回滚的实跑 | 真实 CD 源可拉取/可校验/可回滚并留证；否则 C9 只能 `NOT VERIFIED` |
| G-6 | **手机未验证** | 服务侧 LAN 200 已验，真机打开/登录/发消息无设备 | 同一 Wi-Fi 真机完成一次登录 + 发消息并留屏/日志 |

**新增缺口统计（2026-11-08）：7 项登记、0 项关闭。**


---

## 看起来有、实际不生效（最容易误判进度）

| # | 现象 | 真相 | 证据 |
|---|---|---|---|
| R-1 | 顶层 61 个 `plugins/` 目录 | 插件管理器默认只扫 `src/plugins`，顶层目录运行时不加载；主程序只注册 builtin | `src/plugins/manager.py:48`；实测 61 个目录 |
| R-2 | `solutions/industry/` 38 个行业方案 | 只有 `tests/test_all_industry_solutions.py` 导入，`src/` 无引用，产品运行时不加载 | `tests/test_all_industry_solutions.py:32+`；`src/` 检索无命中 |
| R-3 | `src/automation/` 体系完整 | 后端只有 `fake.py`，三重闸门默认关闭，无真实浏览器/键鼠/安卓执行 | `src/automation/backends/`、`src/automation/gate.py:19`、`src/automation/__init__.py:1-6` |
| R-4 | ~~「知识中心」只是端点清单~~ **已解除** | `69db011e` 后已是导入/文档/分块预览/检索测试界面，且 `/v1/chat` 注入检索上下文；剩余是真实 embedding E2E 未验证 | `apps/console/console/src/pages/Directory.tsx`、`src/gateway/chat.py:159` |
| R-5 | 反馈服务有接口 | 进程内 `dict` 存储，重启即丢 | `src/feedback/feedback_service.py:13` |
| R-6 | 控制台 dist 已构建 | 源码改动后是否重建未验证 | `docs/strategy-30-directions.md:232` |

---

## 明确不做清单（不是延后，是不做）

来源：`docs/strategy-30-directions.md:188-220`。以下六类**不进入路线图**，
除非重新裁决并给出安全/合规前提：

1. **电脑自主操作**（无审批的任意桌面/浏览器操作）；
2. **任意仓库自动改代码 / 自动提 PR**；
3. **金融交易**；
4. **外部 KYC**；
5. **全网常驻采集**；
6. **机器人 / IoT / 工业执行**（依赖物理硬件与现场责任）。

## 五大战略中心的决策

只做**信息架构、命名与权限边界**，不拆五套后端。理由：现状是 1 进程 1 主库
+ 至少 9 个 SQLite，与「双击即用、零运维」冲突；且中心边界不干净（知识被执
行链消费、安全拦执行、进化评估执行）。详见
`docs/strategy-30-directions.md:42-46`、`:87-147`。若未来改为多服务，必须先
解除「零运维」承诺并给出迁移与运维方案（触发条件 K-6）。


## D-6 情报中心后续阶段（漏洞查询、缺口对照、更新提醒）

**决定延后（deferred-by-decision）。** `src/intel/` 本轮只做发现、许可证判定与提案，漏洞查询接口
**明确未接线**：`src/intel/scanner.py:298`、`:428` 记录
`error_reason="advisory_endpoint_not_wired_in_this_round"`。`src/intel/` 与
`docs/oss-radar/`（8 份）现已提交跟踪，但更新提醒与缺口对照仍无调度与通知。

**依赖什么。**
- GitHub Advisory / OSV 真实查询与限流配额（`src/intel/github_client.py` 已有客户端骨架）；
- 与现有能力的缺口对照（`src/intel/capabilities.py`）接入真实能力注册表；
- 定时扫描与通知（依赖 D-3 的调度能力）。

**验收标准。**
- 对 ≥1 个真实依赖仓库跑出真实 advisory 结果（非占位）；
- 缺口对照输出「已有 / 缺什么 / 建议是否采用」，unknown 许可证仍拒绝；
- 版本更新与漏洞变化能生成可订阅提醒；
- 报告可追溯到来源、版本、许可证、扫描时间。

---

## 取证沙箱删除不是安全擦除；凭证 ACL 已在保留期内收紧（2026-11-09，**状态：残余 2 保持结论；残余 1 已消除**）

### 残余 2：`rmtree` 删除 `liuhao-maintenance-evidence` 不等于安全擦除

**知道什么。** [temp_sweep.py](file:///d:/LiuHao-AI-OS/src/common/temp_sweep.py#L326-L341)
的 `_remove_quarantined()` 先把过期取证沙箱原子改名隔离，再交给
`shutil.rmtree`。`rmtree` 只删除目录项、释放簇，**不覆盖数据块**：在同一个
卷上，删除后的主密钥、令牌、凭据库内容仍可能被磁盘/日志/卷影副本恢复出来。
风险类型：**数据残留（data remanence）**，影响面限于能接触该磁盘或卷影副本的
人；不是远程可利用漏洞。

**为什么不在代码里修。** 三个原因都不是"以后再说"：

1. **应用层覆盖无法证明物理擦除。** SSD 的 wear leveling、NTFS 日志、
   copy-on-write、TRIM 都可能让"覆写同一逻辑偏移"落到别的物理块。把每个
   文件写成随机字节只会得到一个**看起来**做了擦除的实现——这正是本仓库
   反复拒绝的"没验过就说安全"。
2. **正确工具在部署层，不在应用层。** 能给出可验证保证的是全盘加密
   （BitLocker）配合 `cipher /w` 擦除卷空闲空间；这属于安装/运维契约，写进
   `src/` 反而会把部署前提藏起来。
3. **对取证目录做覆盖有二次风险。** 保留期内沙箱可能正在被取证进程使用；
   覆盖内容会破坏证据本身，与"宁可不删"的判据冲突。

**谁在什么条件下才修。** 如果取证沙箱将来落在**没有全盘加密**的机器上，或
合规要求"可证明擦除"，再处理：前提是确认沙箱已过保留期且无进程占用，然后用
`cipher /w` 处理所在卷，或改为逐文件调用已有的
`src/common/windows_acl.py:secure_delete_file()` 并在文档里同时写明它
**不能**覆盖 SSD 物理块。现有
[`secure_delete_file`](file:///d:/LiuHao-AI-OS/src/common/windows_acl.py#L147-L176)
只用于少量密钥文件，不是通用方案。

### 残余 1（已消除）：保留期内所有持密文件都收紧到当前用户

**知道什么。** 上一轮只收紧了 `data/master.key` 与 `auth_secrets.json`，同一
沙箱里的 `root.token`、`member.token`、`identities.json`、`model_config.sqlite3`
（含 `-wal`/`-shm`，`api_key_enc` 列）仍在 30 天保留期内继承
`%LOCALAPPDATA%\Temp` 上的孤儿 SID
`S-1-5-21-1173064321-3619361309-2059095201-2888008584`。

**已消除。** `_EVIDENCE_SECRET_FILES`（
[temp_sweep.py:136](file:///d:/LiuHao-AI-OS/src/common/temp_sweep.py#L130-L139)）
按真实沙箱逐项枚举后覆盖 8 个持密文件；
`_tighten_evidence_secrets()`（
[temp_sweep.py:306](file:///d:/LiuHao-AI-OS/src/common/temp_sweep.py#L306-L323)）
对每个文件用受保护 DACL 只保留当前用户。受控前后实测：继承态 5 ACE /
`control=0x8404` / 含孤儿 SID / 全部 `inherited=True` → 收紧后 1 ACE /
`control=0x9404` / 当前用户 / `inherited=False`，文件内容哈希与 mtime 不变。
非持密文件（`alerts.json`、`account_memory.sqlite3`、`rbac.json` 等）不被
触碰。24h 缓存命中时收紧照旧执行（`sweep_stale_temp_artifacts` 在跳过 pid
扫描时仍调用 `sweep_stale_evidence_sandboxes`）。

**仍存在的边界（不是本轮遗漏）。** `chrome-profile/` 是浏览器自己的目录，
可能含 cookie/会话，但不是本产品的凭据存储；递归改写运行中浏览器的 ACL 会
与浏览器抢文件，因此**不**纳入逐文件收紧，也不在 `_EVIDENCE_SECRET_FILES`
里。若将来要求连它一起收，需要先确认浏览器未运行，并单独设计递归策略。

**行为边界。** 收紧发生在**启动时**；保留期内取证进程新写入的凭证文件会
重新继承父目录 ACL，最长等到下一次启动才被收紧。我们没有该沙箱生产者
（仓库内无创建代码，见 `docs/known-gaps.md` 本条目），无法加"写入后自收紧"。
切 DACL 对**已打开的句柄**不构成破坏：Windows 访问检查发生在 `open` 时，
替换 DACL 不吊销现有句柄；同用户的新 `open` 仍拿到 `FILE_ALL_ACCESS`。
`tests/test_temp_sweep.py::test_open_handle_survives_acl_tightening` 实测
持句柄读取不受影响。

---

## 验收提醒

- 以上延后项**不得**在交付报告里被写成「已完成」；
- 若某项被提前捡起，先更新本文件的状态与验收标准，再动代码；
- 需求逐条状态以 [需求追溯表](requirements-traceability.md) 为准。

---

## 故障面未执行边界（2026-11-06，**状态：未验证**）

> 2026-11-06 补测的五类故障面里，以下场景**没有真跑**（原因：共享机器 / 无独立可写满卷 /
> 前端与账号树正被另一路修改）。它们是 **未验证**，不是已通过项；交付报告里不得写成
> 「已验证」。现有替代证据见 [fault-surfaces-2026-11-06.md](evidence/fault-surfaces-2026-11-06.md) §7。

| 编号 | 未执行场景 | 原因 | 现有替代证据 | 状态 |
|---|---|---|---|---|
| F-1 | 真正 OS 睡眠 / 唤醒 | 共享机器，会中断其它正在跑的验收与切片 | 网关进程 suspend 8s 后 `/v1/health` 前后各 200（0ms）；端口未丢 | **未验证** |
| F-2 | 真拔网线 / 切 Wi-Fi / DNS 变更 | 同上 | 本地假服务断开 `ConnectTimeout` 2000ms；半开黑洞 `ReadTimeout` 2030ms；恢复后 16ms 自愈 | **未验证** |
| F-3 | 真实磁盘满 `ENOSPC` | 本机无独立可写满卷，格式化会破坏环境 | 注入 `OSError(ENOSPC)`：审计旧文件不变、内存事件回滚 | **未验证** |
| F-4 | 真实托盘图标点击（GUI） | 无头/远程会话无法点托盘 | `GatewayService.start()` 在 CLI 持锁时真实拒绝且不拉子进程 | **未验证** |
| F-5 | 两个真实浏览器窗口同时发消息 E2E | 前端/账号树正由另一路修改，按 owner 要求不动 | 同进程双线程并发 400/400 条、重复 0；跨进程锁与升级互斥有真进程用例 | **未验证** |
| F-6 | 真实全局系统时钟跳变 | 改全局时钟会影响同机其它进程 | 进程内锚点模拟前进/后退，覆盖 JWT/吊销/审计链；非改系统时钟 | **未验证** |

**什么算关掉这些条目**：F-1/F-2 需要在可独占的机器上真睡眠、真断网后各跑一次端到端；
F-3 需要一块可写满的独立卷；F-4 需要在有桌面会话的机器上真实点击托盘；F-5 需要前端冻结后
开两个浏览器窗口做一次并发发送；F-6 需要在一台可牺牲的机器上真实改系统时钟并验证登录态。
在此之前，状态保持 **未验证**。

---

## 索引门禁用裸文件名匹配（2026-11-08，**状态：已知未修**）

`scripts/verify_docs_index_coverage.py` 判断「某篇文档是否被 `docs/README.md` 收录」时
**只比文件名，不比路径**。后果：`docs/a11y-keyboard-audit-20261108/report.md` 与
`docs/a11y-sr-hc-keyboard-20261108/report.md` 因为撞上 `docs/ui-audit-20261106/report.md`
被判定「已收录」，实际从未进过索引。本轮已把这 3 篇同名文件显式列进 §4.20，
但**匹配规则本身没改**。

**为什么不顺手改。** 改成全路径匹配会一次性暴露更多历史漏项（同名的
`README.md`、`report.md`、`index.md` 类文件），属于独立事项，需要单独一轮核对与补录；
混在「修门禁误报」这一批里做，等于把一个可验证的小改动变成一次无法复核的大翻修。

**什么算关掉它。** 覆盖率判定改为「规范化后的仓库相对路径」匹配，且改完后
`verify_docs_index_coverage` 在补录完成前**必须红**（证明它真的开始看路径了）。

---

## 满盘 503 响应缺 `correlation_id`（2026-11-08，**状态：已知未修**）

磁盘满（`ENOSPC`）时身份/撤销库不可用，网关返回 503，但响应体**只有 `code` / `message` /
`retry_after`，缺 `correlation_id`**，用户报障时无法把「这一次 503」与日志里的那一行对上。
同一套错误模型里其他 5xx 都带该字段，属遗漏而不是设计。

**排期。** 与 `main.py` 其后批次一起补（统一 503 响应体 + `Retry-After` 头），
不单独开批。验收标准：构造一次 `ENOSPC`，503 body 含 `code` / `message` /
`correlation_id` / `retry_after` 四字段，且 `correlation_id` 能在日志中检索到同值。

---

## `config\current_url.txt` 写不写取决于启动方式（2026-11-08，**状态：已核实；文档按启动方式分述**）

此前这条写的是"安装版不生成 `config\current_url.txt`"，**与代码不符**（文档线核实后更正）：
是否生成取决于**走哪条启动路径**，不是取决于"源码版 / 安装版"。

**写点。** 唯一的写入在 `scripts/start_liuhao.py:140-149`，触发条件是 `LIUHAO_DATA_DIR` 已设置。

| 启动方式 | 是否写 | 依据 |
|---|---|---|
| 安装版 `LiuHao.bat` | **写** | `scripts/launch_installed.py:500-503` 设置 `LIUHAO_DATA_DIR`，并在 `:1026-1037` 调 `start_liuhao.main()` |
| 源码 `start-liuhao.bat` | **默认不写** | 默认不设 `LIUHAO_DATA_DIR`，写点条件不成立 |
| 托盘壳（桌面图标目标） | **不写** | `scripts/liuhao_desktop.py:108-153` 不设该变量、不走 `start_liuhao.main()`；`installer/liuhao.iss:180-190` 指向它 |

**提示（保留）。** 该文件可能是**旧端口或已死 pid 的残留**，不作权威；请以状态窗口底部
「控制台：」一行为准。[DELIVERY-CHECKLIST.md](DELIVERY-CHECKLIST.md) §1 与
`packaging/stage_payload.py` 生成的 `START-HERE.txt` 已按此口径表述，本轮**不改 `src/desktop`**。

**结论。** 面向用户的文档不再笼统说"安装版不写"，而是按启动方式分述；用户找地址一律看
状态窗口「控制台：」。若后续要让托盘壳也写该文件，需在 `scripts/liuhao_desktop.py` 内设置
`LIUHAO_DATA_DIR` 并走同一写点，再统一三种启动方式的行为。

---

## console devDependencies 的 2 条 critical（2026-11-08，**状态：已知未修；本轮风险接受**）

`apps/console/console` 的 `npm audit --json` 命中 5 条：`tinypool`（CRITICAL，GHSA-5gmw-xhrv-c9v3 /
GHSA-85c8-ppgw-ccpr，修复 >2.1.1）、`vitest`（CRITICAL，GHSA-82fw-gwwq-j7x9 叠加 `tinypool`，
修复 ≥5.1.0）、`vite`（HIGH + 6 条 moderate/low，修复 ≥8.3.6）、`source-map-js`（HIGH，修复 ≥1.2.2）、
`@vitest/mocker`（MODERATE，修复 ≥4.1.12）。其中 `vitest` / `tinypool` 是仅有的 2 条 critical。

**为什么可接受。** 这 5 条全部落在 devDependencies，只在 `npm run dev` / `npm run test` 的本机
进程里成立；`npm audit --omit=dev` 为 **0**，生产 5 个依赖（`react` / `react-dom` / `ws` 等）
无一命中，且 devDependencies 不随 `stage_payload.py` 进入安装包。攻击面要求"有人在本机跑
dev server 且被不可信输入触达"，与安装版用户无关。

**升级路径与代价。** `vitest` 必须 major 到 `5.1.0`（`@vitest/mocker` 随之换档、`tinypool`
由 >2.1.1 版本替代），牵动 console 全部单测（约 500+ 条，含 `*.test.tsx` 与 `styles.a11y.test.ts`），
需整轮重跑与快照/断言复核；`vite` 8.2.2 → 8.3.6 与 `source-map-js` → 1.2.2 属语义化范围内，
会随出包线重新生成 lock 时自然升上去，不需要单独排期。本轮裁决：**不升 major，风险接受**，
等出包线修完 lock 脱节后再评估。

**什么算关掉它。** `vitest` 升到 ≥5.1.0 且 `npm audit --omit=dev` 与全量 `npm audit` 均为 0，
`npm run build` + `npm test` 全绿。

---

## 后端 3 个包 7 条命中（2026-11-08，**状态：已知未修；全部不可达，风险接受**）

P0/P1 修复后，`pip-audit` 对 `requirements.txt`（210 个发行包）剩余 **7 条 / 3 个包**，全部
不在代码路径上：

| 包 | 版本 | 条目 | 严重度 | 修复版本 | 卡点 |
|---|---|---|---|---|---|
| `opentelemetry-api` | 1.44.0 | CVE-2026-114603 | MEDIUM 5.5 | 1.45.1 | 需 OTel 整组成套（`sdk` / `exporter-*` / `semconv 0.66b1` / `instrumentation 0.66b1`） |
| `pydantic-ai-slim` | 2.40.0 | PYSEC-2026-4284/4285/4286/4290/4291 | HIGH 7.9（最高） | 1.107.6 / 2.44.0 / 2.52.0 / 2.53.0 | 需 5 包联动 |
| `strawberry-graphql` | 0.327.0 | PYSEC-2026-4302 | LOW 3.8 | 0.327.2 | 被 `arize-phoenix==0.327.0` 精确 pin |

**可达性为否的依据。** `pydantic-ai-slim` 全仓 0 处 import；`strawberry-graphql` 只经
`arize-phoenix` 的 GraphQL 服务端，该服务端从未启动；`opentelemetry-api` 的攻击面是入站
W3C `traceparent` 头解析，本仓未注册 propagator、不从入站头 `extract`。

**升级路径与代价。** `pydantic-ai-slim` 升到 2.53.0 需同批 `anthropic>=1.9.0`（现 1.3.0）、
`openai>=3.19.0`（现 3.13.0）、`google-genai>=2.25.0`（现 2.22.0）、`pydantic-graph==2.53.0`、
`genai-prices>=0.1.9`，且仍受 phoenix extras 约束；`strawberry-graphql` 须先把
`arize-phoenix` 改为 optional extra 或等上游放宽 pin；OTel 只能在打包前整组升。

**本轮另一项裁决：依赖瘦身不执行。** 删除 `langchain` / `langchain-community` /
`python-json-logger` 的实际可删面是 **6 包 ≈11.20 MB**（不是 13 包/14.77MB——`aiohttp 3.14.4`
被 `taskiq 0.13.0` 无条件要求，其 `yarl/multidict/frozenlist/propcache/aiohappyeyeballs/aiosignal`
链必须保留），11.2 MB 换不来发布风险，**发版后再做**。

---

## SBOM 口径与版本漂移（2026-11-08，**状态：已知；下次打包必须重生成**）

`docs/compliance/sbom-cyclonedx.json`（2026-11-08T00:42Z，95 个组件）与 `requirements.txt`
（210 个发行包）对账：覆盖 **66/210**，缺 **144**；另有 **2 处真漂移** —— `certifi`
SBOM 记 `2026.7.22`（锁已 `2026.10.23`）、`pyjwt` SBOM 记 `2.13.0`（锁已 `2.15.1`）。

**144 条缺口不是真缺失，是口径不同。** SBOM 由 `dist/release-gate/sbom_audit.py` 从离线
payload 的 `app/runtime/Lib/site-packages` 采样，口径是"随包发出的运行期发行包"；lock 是
解析结果全集，payload 是真实 import 图命中子集。缺口三类：① 只进 lock 的构建/测试工具
（`pytest` / `hypothesis` / `ruff` 等）；② payload 运行期不触发的可选链（`arize-phoenix*`
及其 `scipy/pandas/pyarrow/scikit-learn` 等）；③ 运行期 0 import 的拟淘汰集
（`langchain*` / `python-json-logger` / `httpx-sse`）。

**2 处漂移是时间差，不是口径差。** SBOM 生成于 P0 升级之前，采到的是升级前 payload 快照。

**生成时机必须固定在：payload 组装完成之后、Inno Setup 编译之前。** 过早（锁文件/`pip install`
阶段）采到的是"锁里写的"而非"包里装的"，正是本次漂移的产生方式；过晚（Inno 已编译）则无法
在封包前拦截不一致。出包线应把"重新生成 SBOM + 与 payload 版本逐条比对"设为封包前置门禁。

**什么算关掉它。** 一次完整打包流程中，SBOM 由 payload 生成、且与 `runtime/Lib/site-packages`
实际版本零漂移；对账脚本明确标注"payload 口径"，不再把 lock 全集差异报成缺失。


---

## 手工 `uvicorn` 直起会让审计降级并把运行库写进程序目录（2026-11-08，**状态：已知未修；不加拒跑门**）

**知道什么。** 不经 `scripts/launch_installed.py`、直接在安装树里执行
`runtime\python.exe -m uvicorn src.gateway.main:app` 时：`AUDIT_DB_PATH` 未设置 →
审计写降级；`_default_revocation_path()` 回落到 `<程序根>\.liuhao_jwt_revocation.db`
→ 吊销库落在程序目录；`model_registry.sqlite3`（含 `-wal`/`-shm`）与
`data\observability\alerts.json` 也写进程序目录的 `data\`。

**为什么不修（不加拒跑门）。** 真实启动路径有两条（托盘壳与 `launch_installed.py`），
两者都经 `launch_installed.py:526-548` 显式设置 `LIUHAO_JWT_REVOCATION_DB` /
`AUDIT_DB_PATH` / `LIUHAO_WORKSPACE_ROOT`，落点正确。而「直起 uvicorn」是测试与
诊断常用手段，加硬拒跑会误伤它们在共享机器上的排障；收益（防住一种非交付路径的
误用）低于代价。

**谁在什么条件下才修。** 只有在**安装后验收或用户报障**里出现真实案例 ——
即用户按文档操作却把运行数据写进了程序目录、或审计缺行导致问题无法追溯 ——
才由启动器负责人加一道「非启动器直起时重定向数据落点或打印显式警告」。
在那之前，状态保持已知未修。

---

## production 第二次启动会复用持久化的 JWT/服务密钥（2026-11-08，**状态：已知未修；真实产物未验证**）

**知道什么。** 首次启动生成的 JWT 签名密钥与服务密钥落盘在数据目录 `config\`，
后续启动按「有则复用、无则生成」取用，不会每次重启轮换。这保证了同一台机器上
已登录会话在重启后仍有效，但也意味着**密钥一旦泄露，重启不会自愈**。

**为什么这轮不修。** 改成「每次启动轮换」会让所有已登录会话在重启后失效，
属于产品行为变更，不能作为收尾顺手改；并且本轮**没有在真实安装产物上验证过**
这条路径（冒烟里的 S-12 只覆盖「机器级密钥在二次启动时被复用」的观测，
不等于「密钥持久化策略已按预期工作」）。

**谁在什么条件下才修。** 由安装冒烟脚本那条线在真实安装产物上补一条观测
（二次启动后比对 `config\` 下的密钥指纹与首次是否一致、以及会话是否仍可用），
拿到真实结果后再决定是保持复用还是提供「轮换密钥」入口。


---

## 插件加载器无隔离，且 0.1.2 不随包发布插件故用户不可达（2026-11-09，**状态：已知未修，修复补丁已就绪留待 0.1.3**）

**知道什么。** 顶层插件与内核同进程、同权限运行，没有沙箱：`requires_internet` 声明原本未被消费、`stop()` 原本不被调用，`plugins/excel/excel_plugin.py` 里存在一处 `eval()` 可被诱导执行任意代码（RCE）。

**为什么这版可以先不发。** `installer/liuhao.iss` 的 `[Files]` 没有任何 `plugins\**` 条目，安装版根本没有 `plugins\` 目录，运行时也就不存在可被加载的第三方插件；仓库里那批顶层插件还全部停在旧基类 `plugins.plugin_base.BasePlugin`（0 个实现 `src.plugins.base.Plugin`），本来也激活不了。结论：这条 RCE 在 0.1.2 安装版里**不可达**，用户没有暴露面。控制台「应用」页已如实写明本版本暂不提供第三方插件市场，不留点了没反应的入口。

**为什么不顺手打补丁。** 四份安全基线补丁含行为变更（`unregister` 成功后现在会真删注册行，旧实现保留 UNLOADED 行），前端与运维脚本可能依赖「卸载后还能 list 到该行」。出包前引入这类行为变更风险高于收益。

**谁在什么条件下才修。** 与插件市场一起在 0.1.3 上：加载器隔离 + 声明消费 + `stop()` 生命周期 + 移除 `eval()`。

---

## 插件库当前仅内置示例能力，旧插件不显示且接口返回 404（2026-11-09，**状态：已知未修，适配留到 0.1.3**）

**知道什么。** 0.1.2 安装版控制台「应用」页只有内置的示例能力；仓库里那批旧插件既不显示、也没有任何提示，直接调用相关接口返回 404。

**为什么这版不修。** 旧插件与当前接口契约不兼容，0.1.2 不放适配层；现在把它们列出来只会多出一批点了就报错的入口。

**谁在什么条件下才修。** 0.1.3 提供旧插件适配与可用性标注（哪些可激活、哪些只是登记、哪些不可用）；在此之前，文档与页面都不承诺插件数量或可用性。

---

## 删除会话与删除项目没有审计事件（2026-11-09，**状态：已知未修**）

**知道什么。** 这两个动作不是埋点埋错，是根本没有埋点：发生后在审计里查不到是谁删的。0.1.2 只补了「删画像 / 删事实 / 删偏好」的请求方归因，以及自动清理与裸删除显式记 `system`。

**为什么这版不修。** 属数据模型与审计口径变更，需要先定「删除会话/项目该记哪些字段、保留多久」，不适合在出包窗口里改。

**谁在什么条件下才修。** 0.1.3 与审计导出一起做。

---

## 已装成的多副本共用一条卸载键（2026-11-09，**状态：既有环境无法追溯修复**）

**知道什么。** 0.1.1 及更早没有多副本检查：实测同一用户装第二份 `LiuHaoAlt` 会改写唯一卸载键，卸掉第一份后 `LiuHaoAlt` 成孤儿。0.1.2 起安装器用同一个 `AppId`，并在 `PrepareToInstall` 里查 HKCU/HKLM 该 AppId 的 `InstallLocation`，与目标目录不同即阻断（提示改回原路径升级，或先卸载再换位置）。但**在 0.1.2 之前已经装成的多副本**仍然只有一条卸载键：卸载其中任意一份，另一份的卸载入口就没了，变成孤儿，事后无法从注册表追溯。

**为什么不修。** 历史状态没有留下可判定归属的证据，脚本无法安全地猜哪一份该保留。

**谁在什么条件下才修。** 只能在用户报障时按「先备份数据目录、再手工清理另一份」处理。

---

## 发布来源门禁的边界：三者同时被完整伪造时只能保证自洽（2026-11-09，**状态：设计边界，不修**）

**知道什么。** `scripts/release/verify_publish_source.py` 保证的是：待发布 exe 只能在 `dist\installer\` 顶层、非归档区不许有第二个同名不同哈希的 exe、`SHA256SUMS.txt` 首行与实际 exe 和 `FREEZE-RECORD.json` 三处一致。它能防「拿错包 / 拿旧包 / 只改了一处数值」，但**不能证明来源**：如果有人把 exe、SHA256SUMS、FREEZE-RECORD 三样同时换成一套完整伪造的组合，脚本只能确认三者自洽。

**为什么不修。** 要真正证明来源只能走签名链验签（校验 Authenticode 证书与时间戳），那是独立的一条线，且自签名证书本身不能建立公开信任。

**谁在什么条件下才修。** 与代码签名证书一起做；在那之前不要把它宣传成「万无一失」。

---

## 同一台电脑的多个 Windows 账户共享同一份数据与密钥（2026-11-09，**状态：已知未修，按用户隔离留到 0.1.3**）

**知道什么。** 当机器存在**可写**的非系统盘时，两个 Windows 账户先后安装，会默认落到同一个 `D:\LiuHao` 与 `D:\LiuHaoData`，**包括同一份 `master.key`**。后装的账户打开鎏灏，能看到并解密同一套数据。`ConflictingInstallDir` 只拦「安装目录不同」的多副本，拦不住「同一个目录被两个用户先后安装」。

**为什么这版不修。** 安装目录与数据目录目前是机器级路径，不做用户级隔离；改成按用户隔离会改变升级与备份的路径推导，属于行为变更，不适合在出包窗口里做。

**影响边界（不夸大，也不淡化）。** 这不是「同一台机器上任何账户都能随便读」：数据目录与密钥仍受 Windows 文件权限约束，只有被安装器授予访问权的账户才读得到。但在上述安装方式下，后装账户确实会获得同一份数据和密钥的访问权，等同共用一套实例。本次实测机器上 `D:\` 根对标准用户只读，所以未实际暴露；换成可写非系统盘时这个前提不成立。

**规避方式。** 一台电脑只保留一个用于鎏灏的 Windows 账户；确实需要多人共用时，等 0.1.3 提供按用户隔离的数据目录后再装。

**0.1.3 待办。** 数据目录与 `master.key` 按 Windows 用户隔离（包含升级、备份路径推导的兼容处理）；在此之前，安装说明与发布口径都不承诺多账户隔离。

---

## 0.1.1 → 0.1.2 是单向门：升级前置保护由正在运行的那一版执行（2026-11-09，**状态：设计边界，不修**）

**知道什么。** 旧文档清理与数据格式门禁都写在 0.1.2 及以后的代码里，执行者是升级发生时**正在运行的那一版**。从 0.1.1 升到 0.1.2 走的是安装器覆盖安装，那一刻运行的是 0.1.1——它没有这段代码，所以这一步不会清理 0.1.1 曾随包发出的旧文档，也不会在覆盖前执行数据格式检查。只有从 0.1.2 往后再升级，才会带上这些保护。

**为什么是单向门。** 升级会覆盖程序目录；升级后再回到 0.1.1，安装器与程序都不提供自动回滚，数据也可能已经被新版本写过。真出问题只能手动处理：用升级前自己保留的数据副本与旧版安装包恢复。文档不承诺「一键回退」。

**影响边界。** 新版本首次启动时的数据版本守卫属于新版本自己的代码，仍会运行；它拦的是「数据版本高于程序支持」这类情况，不等于替旧版本补做了升级前检查。旧文档清理只作用于安装目录里不该再出现的文档，不动数据目录。

**怎么办。** 升级前先退出程序，把整个数据目录复制一份到别处；需要回退时用这份副本和旧版安装包手动恢复。发布说明与安装向导都按「升级不可自动撤销」来写。
