# 模型厂商接口漂移韧性审计（只读）

- **审计日期**：2026-11-08
- **审计对象**：`src/ai/providers.py` 及各 provider 解析/流式路径、`src/ai/liuhao.py`、`src/gateway/chat.py`、`apps/console/console/src/lib/chatClient.ts`、`ChatPanel.tsx`
- **代码状态**：针对当前**工作区**版本（`src/ai/providers.py` 相对 HEAD 有未提交改动；本报告所有数据均在该状态下实测）
- **运行环境**：本机 `.venv`：`openai 3.13.0`、`anthropic 1.3.0`（要求 `httpx2.Client`）、`requests 2.34.2`
- **只读声明**：未修改仓库任何代码；一次性 mock 与复现脚本全部在 `D:\_audit\provider-drift`，未提交仓库

---

## 1. 结论：哪几种上游变化会让用户看到假失败或白屏

按“用户实际感受”排序（严重度高 → 低）：

| # | 上游悄悄发生的变化 | 用户看到什么 | 性质 | 严重度 |
|---|---|---|---|---|
| 1 | 返回 200 但 `content` 为 `null` / 空串 / `choices` 为空；或拒答只给 `refusal` 字段 | 气泡里只有一个 `…`，或干脆空白；后端记 `completed`，还照常落盘、照常记估算用量 | **假成功 / 白屏** | 🔴 |
| 2 | 流式连接“干净地”提前结束（半截 JSON、缺 `[DONE]`/`done`、缺换行、中间夹坏帧） | 拿到半截回复就结束，没有任何“内容可能不完整”的提示；坏帧之前已发出的文字留在界面 | **静默截断** | 🔴 |
| 3 | 200 + `{"error": {...}}`（Ollama / Moonshot / DeepSeek） | 原始 JSON 被当正文直接上屏；模型管理“测试连接”还会显示成功 | **脏数据上屏 + 假绿** | 🔴 |
| 4 | OpenAI 把 `content` 改成 content parts（`list`） | 对话直接报“这一轮没有成功”；而同一份响应走 `generate_with_retry` 却被判成功——两条链路结论相反 | **假失败** | 🔴 |
| 5 | Anthropic 在首位多返回一个 `thinking` / `tool_use` block，或 `content` 为空、`text=null` | 合法响应被判失败（`AttributeError` / `IndexError`），并重试 4 次 | **假失败** | 🔴 |
| 6 | 上游持续发心跳（`: keepalive` / `event: ping`）但不出内容 | 后端读超时永不触发（实测 6 秒心跳期间 1.5s 超时形同不存在），前端没有取消按钮，界面永久“生成中…” | **永久等待** | 🔴 |
| 7 | Anthropic SDK 大版本变化（本机 `1.3.0` 只认 `httpx2.Client`） | 每个 Anthropic 请求在发出前就 TypeError，0 个上游请求，约 3.5s 后通用失败 | **假失败（全 provider 不可用）** | 🔴 |
| 8 | Moonshot / DeepSeek 429 额度耗尽 | 与限流、401、400 显示同一句通用失败文案（这两个 provider 没有 `upstream_kind`） | **分类丢失** | 🟡 |
| 9 | OpenAI/Anthropic 的 5xx，或任意 provider 的请求超时 | 一次用户操作最多打 12 个上游请求（SDK 3 次 × 业务 4 次），超时可能已在厂商侧计费 | **计费放大** | 🟡 |
| 10 | 返回恶意 HTML / `<script>` | Console 全部按 React 文本节点渲染，不执行、不注入；风险为 0（旧 `frontend/` 组件未上线） | **安全** | 🟢 |

一句话版本：**最危险的不是“报错”，而是“把空/半截/错误 JSON 当成功”**（第 1–3 条），以及**“把合法的新格式当失败”**（第 4–7 条）。

---

## 2. 非流式解析：逐条实测

### 2.1 OpenAI（`response.choices[0].message.content`）

代码：[providers.py#L554](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L554)（`generate`）、[providers.py#L585](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L585)（`chat`）

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| O1 | `{"choices":[{"message":{"role":"assistant","content":null}}]}` | `chat` 返回 `""`；`parse_tool_call("")` → `None`；轮次 `completed`；界面空白气泡 `…`；**仍落盘、仍记用量** | 视为“无输出”失败并给出可行动提示；不落盘空回复、不计费 |
| O2 | `content: ""` | 同 O1（空串被 `or ""` 吞掉） | 同 O1 |
| O3 | `{"choices":[]}` | SDK 归一化为 `choices=None` → `TypeError: 'NoneType' object is not subscriptable`；`chat` 直接失败；`generate_with_retry` **4 个请求**后通用失败 | 识别为“上游无候选/被拒”，一次请求即给出明确原因，不重试 |
| O4 | 缺 `message` 字段 | `AttributeError: 'NoneType' object has no attribute 'content'`；4 个请求后失败 | 同 O3 |
| O5 | `content` 为 content parts：`[{"type":"text","text":"block-text"}]` | `chat` 返回 `list` → `parse_tool_call` 抛 `AttributeError: 'list' object has no attribute 'strip'` → **合法响应被判失败**；而 `generate_with_retry` 把 `list` 当**成功**返回（1 个请求） | 兼容 content parts，抽取全部 text 片段拼接；两条链路结论一致 |
| O6 | 200 + `{"error":{"message":"You exceeded your current quota","code":"insufficient_quota"}}` | SDK 宽松解析 → `choices=None` → `TypeError`；**4 个请求**后 `upstream_error`，配额语义完全丢失 | HTTP 200 里出现 `error` 必须当失败；先分类（quota）再脱敏，不重试 |
| O7 | `usage` 缺失 | 不影响解析；但计量静默为 0（未告警） | 至少记一条“用量缺失”诊断，避免账单对不上 |
| O8 | 多返回 `choices[1]` | 静默只用第一个（符合预期） | 保持 |

> 关键结构缺陷：`chat` / `chat_stream` **不经过** `generate_with_retry`（[providers.py#L275](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L275)），所以 `ProviderEmptyResponseError` 这个唯一空响应保护在真实对话链路上完全失效——对话走的是 `chat`（[liuhao.py#L690-715](file:///D:/LiuHao-AI-OS/src/ai/liuhao.py#L690-L715)）。

### 2.2 Anthropic（`response.content[0].text`）

代码：[providers.py#L736](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L736)

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| A1 | `content:[{type:"text",text:"first"},{type:"text",text:"second"}]` | 只返回 `first`，第二块**静默丢失** | 拼接所有 text block |
| A2 | `content:[{type:"thinking",...},{type:"text",text:"answer"}]` | `AttributeError: 'ThinkingBlock' object has no attribute 'text'`；4 个请求后失败 | 跳过非 text block，取所有 text |
| A3 | `content:[{type:"tool_use",...}]` | 同 A2（`ToolUseBlock` 无 `.text`） | 识别 tool_use 并走工具调用，或明确“模型只返回了工具调用” |
| A4 | `content:[]` | `IndexError: list index out of range`；4 个请求后失败 | 视为空响应失败，一次请求给出原因 |
| A5 | `content:[{type:"text",text:null}]` | 返回 `None` → `parse_tool_call(None)` 抛 `AttributeError`；`generate_with_retry` 判空后 4 个请求失败 | 类型校验 + 中性失败 |
| A6 | 生产传输层（不 mock）：`anthropic 1.3.0` + 代码传 `httpx.Client` | `TypeError: Invalid 'http_client' argument; Expected an instance of 'httpx2.Client'`；**0 个上游请求**，约 3.5s 后失败 | 按 `anthropic` 实际版本构造 client（或锁版本）；启动自检提前暴露 |

> A6 不是“厂商接口变了”，而是**依赖漂移**，但结果一样：用户看到“模型不可用”。代码位置 [providers.py#L695-L701](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L695-L701)。

### 2.3 Google Gemini（`response.text`）

代码：[providers.py#L784](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L784)

`genai.Client(api_key=...)` 忽略 `base_url`，无法指向 mock，**未实测**，仅静态判定：

| # | 上游变化 | 静态判定 | 期望行为 |
|---|---|---|---|
| G1 | 安全拦截 / 空 candidates | `response.text` 可能返回 `None` 或抛 `ValueError`，无类型判断、无脱敏、无状态分类 | 与 OpenAI/Anthropic 同一套“空即失败 + 分类先于脱敏” |
| G2 | 429 / 403 | 异常消息原样冒泡；`classify_error` 靠字符串匹配，可能误判；且 4xx 可能被重试 | 与统一错误模型对齐 |

### 2.4 Ollama（`result.get(...)` 兜底 `str(result)`）

代码：[providers.py#L841](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L841)（generate）、[providers.py#L894](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L894)（chat）

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| L1 | `/api/chat` 200 + `{"error":"model 'drift-x' not found"}` | `chat` 走 `.get("message",{})...get("content", str(result))` → 返回**原始 JSON 字符串**，界面直接展示 | 200 带 `error` 必须抛失败并分类（`model_not_found`） |
| L2 | `/api/chat` 缺 `message` | 返回 `"{'model': 'mock', 'done': True}"` 原文上屏 | 同 L1 |
| L3 | `message.content = null` | `chat` 返回 `None` → `parse_tool_call(None)` → `AttributeError` → 失败气泡（**合法响应被判失败**） | 按“空回复”处理，一次请求给提示 |
| L4 | `message.content = [{type:"text",text:"..."}]` | 返回 `list` → `AttributeError` → 失败气泡 | 兼容 content parts |
| L5 | `/api/generate` 200 + `{"error": ...}`（无 `response`） | `generate_with_retry` 返回**原始 JSON 且判成功**（1 个请求） | 同 L1 |
| L6 | 非 429/401 的 4xx/5xx | `ProviderHTTPError` 已保留 `upstream_kind`（正确）；但业务重试仍会打 4 次 | 4xx 不重试 |

### 2.5 Moonshot / DeepSeek

代码：[providers.py#L1032](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L1032)、[providers.py#L1102](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L1102)

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| M1 | 200 + `{"error":{...}}` | `return ...get("content", str(result))` → **原始 JSON 当回复**；`generate_with_retry` 判成功（1 个请求） | 同 L1 |
| M2 | `content: null` | `chat` 返回 `None` → `AttributeError`；`generate_with_retry` 4 个请求后空响应失败 | 空即失败，一次请求 |
| M3 | 200 + HTML（非 JSON） | `JSONDecodeError`；4 个请求后 `upstream_error` | 识别“非 API 响应（被网关/WAF 拦截）”，不重试 |
| M4 | HTTP 429（rate / quota） | `Exception("Moonshot error 429: <原始 body>")`；`reason=None`；**quota 与 rate 在前端都是通用文案**；重试 1 次 | 保留 `upstream_kind`（先分类再脱敏），429 quota 显示“额度用完” |
| M5 | HTTP 401 / 400 / 500 | 重试 **4 个请求**；401 无 `upstream_kind` | 401 不重试；保留 `key_revoked` / `invalid_key` 分类 |

> M4/M5 的异常消息含**上游原始 body**（[providers.py#L1034](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L1034)），`_log_llm_call_failure` 用 `exc_info=True` 写入 `error.log`（[liuhao.py#L107-179](file:///D:/LiuHao-AI-OS/src/ai/liuhao.py#L107-L179)）。不上屏，但有日志泄露面。

---

## 3. 流式解析：逐条实测

### 3.1 OpenAI 流（SDK 内置 SSE）

代码：[providers.py#L590-655](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L590-L655)

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| S1 | 正常 `data:` 分块 | 2 token、`completed` | 保持 |
| S2 | 200 + `{"error":...}`（非 SSE） | SDK 抛 `APIError`；`chat_stream` 中性化后失败；**1 个请求**，无原始 body 上屏 | 分类（quota/401）后一次失败 |
| S3 | 纯 JSON（非 SSE，200） | 0 token、`completed` → **空白气泡** | 识别“不是 SSE”，失败并提示上游格式变化 |
| S4 | 纯文本（非 SSE，200） | 0 token、`completed` → 空白气泡 | 同 S3 |
| S5 | `data: {...}` 半截 + 无换行 EOF | 只得到 `head`，无异常、`completed` → **静默截断** | 未收到结束标记视为不完整，标记失败 |
| S6 | 半截 + 有换行无空行 | 同 S5 | 同 S5 |
| S7 | 正常 `head` 后夹坏 JSON 帧 | 已发出 `head`，随后 `JSONDecodeError` → `error`：用户先看到半截再看到失败 | 坏帧计数并报“流损坏”；已出内容保留但明确标注不完整 |
| S8 | `: keepalive` 注释心跳 | 正常完成（1 token），心跳被忽略（正确） | 保持；但需要空闲超时（见 S15） |
| S9 | `event: ping`（无 data） | `JSONDecodeError` → `error`（**合法心跳被判失败**） | 忽略无 data 的 event 帧 |
| S10 | `event: ping` + `data: {...}` | 正常完成 | 保持 |
| S11 | 流式 content parts（token 为 `list`） | 先发出 `list` token → 前端显示 `[object Object]` 类脏数据；随后 `"".join` `TypeError` → `error` | 只产出文本片段，非文本块跳过 |
| S12 | 所有 delta 为空 | 0 token、`completed` → 空白气泡 | 空流失败 |
| S13 | `choices: []` / 缺 `choices` | 0 token、`completed` → 空白气泡 | 同 S12 |
| S14 | 真正断连（`RemoteProtocolError`） | `error`（正确） | 保持 |
| S15 | 心跳持续 6 秒、`timeout=1.5` | **6.14s 后仍正常完成**（只发 `: keepalive`，读超时从不触发） | 增加“无 token 空闲超时”，而不是依赖 socket 读超时 |
| S16 | 完全无字节（`ReadTimeout`） | 1.61s 失败（正确） | 保持 |
| S17 | 首次请求 429/500 | 3 个请求（SDK 内部重试）后失败；不再有第二轮 fallback 请求（[L615-631](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L615-L631) 已收紧为仅 400 才退） | 429 quota 不重试 |

### 3.2 Ollama 流（`requests.iter_lines` 手写 NDJSON）

代码：[providers.py#L914-985](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L914-L985)

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| N1 | 正常 NDJSON | 2 token、`completed` | 保持 |
| N2 | 200 + `{"error":...}` | 0 token、`completed` → **空白气泡**，错误对象被忽略 | 见到 `error` 即失败 |
| N3 | 有内容但缺 `done:true` | 只得到 `head`、`completed` → 静默截断 | 缺终止帧按不完整处理 |
| N4 | 半截 JSON 后 EOF | 只得到 `head`、`completed` | 同 N3 |
| N5 | 全程非 JSON | 0 token、`completed` → 空白气泡 | 识别“非 NDJSON”失败 |
| N6 | `head` 后一行坏 JSON | `except JSONDecodeError: continue`（[L965-966](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L965-L966)）→ 只留 `head`、`completed`，**无任何异常** | 坏帧计数，超阈值报错 |
| N7 | 真正断连（`ChunkedEncodingError`） | `error`（正确） | 保持 |
| N8 | 心跳 6 秒 + `timeout=1.5` | **6.02s 后正常完成**（读超时被心跳喂饱） | 空闲超时 |
| N9 | 无字节 | 1.5s `ConnectionError`（正确） | 保持 |

### 3.3 DeepSeek 流（手写 SSE）；Moonshot 走默认降级

代码：[providers.py#L1104-1130](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L1104-L1130)；Moonshot 继承 [providers.py#L392-399](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L392-L399)（整段回复当单 token）

| # | mock 输入 | 当前实际行为（实测） | 期望行为 |
|---|---|---|---|
| D1 | 正常 `data:` 流 | `["Hello"," world"]` | 保持 |
| D2 | 半截 JSON 后 EOF | `["head"]`、无异常 → 静默截断 | 缺 `[DONE]` 视为不完整 |
| D3 | 纯文本（非 SSE） | `[]`、无异常 → 空白气泡 | 识别非 SSE |
| D4 | 200 + `error` 对象 | `[]`、无异常 → 空白气泡，错误被丢弃 | 失败并分类 |
| D5 | content parts | token 为 `list` → 界面脏数据 + `join` `TypeError` | 只产出文本 |
| D6 | 空 delta / 空 choices | `[]`、无异常 → 空白气泡 | 空流失败 |
| D7 | 中间坏 JSON 帧 | `except JSONDecodeError: continue` → 静默跳过 | 报“流损坏” |

> Moonshot 无原生流式实现，`chat_stream` 降级为一次性整段——不存在“半截流”问题，但也失去逐字输出；`supports_streaming=True` 的能力声明与实际行为不一致。

---

## 4. 错误分类：429 / 401 / 403 / 5xx 是否稳定

### 4.1 分类先于脱敏（历史坑复核）

链路：[`classify_upstream_error`](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L104) → [`_neutralize_http_error`](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L156) → `ProviderHTTPError(upstream_kind=...)`；`_turn_failure_reason` 直接读 `upstream_kind`（[liuhao.py#L96-104](file:///D:/LiuHao-AI-OS/src/ai/liuhao.py#L96-L104)）；done 帧透传 `reason`（[chat.py#L892-904](file:///D:/LiuHao-AI-OS/src/gateway/chat.py#L892-L904)）；前端按下表映射（[chatClient.ts#L306-321](file:///D:/LiuHao-AI-OS/apps/console/console/src/lib/chatClient.ts#L306-L321)）。

**实测（OpenAI）**：

| 上游 | `status_code` | `upstream_kind` | 前端文案 |
|---|---|---|---|
| 429 + `insufficient_quota` | 429 | `quota_exhausted` | 额度已用完 → 去充值 ✅ |
| 429 + `rate_limit` | 429 | `rate_limited` | 被限流 → 稍后重试 ✅ |
| 401 revoked | 401 | `key_revoked` | Key 已吊销 → 重新生成 ✅ |
| 401 invalid key | 401 | `invalid_key` | 同 Key 类 ✅ |
| 402 | 402 | `quota_exhausted` | 额度 ✅ |
| 404 model | 404 | `model_not_found` | 默认文案（未单列）⚠️ |
| 500 | 500 | `upstream_error` | 默认文案 ✅ |

**历史坑是否还在**：`error_types.classify_error` 仍是对**脱敏后的中文消息**做字符串匹配（[error_types.py#L83-97](file:///D:/LiuHao-AI-OS/error_types.py#L83-L97)）。实测把 `HTTP 429` 的中性消息再分类，会得到 `rate_limited`——即“额度耗尽被判成限流”的机制**仍然存在**。当前之所以没爆发，是因为 `reason` 走 `upstream_kind`、不走这条重分类；一旦有人用 `classify_error` 生成用户文案，坑会立刻复活。

**分类真空白区**：Moonshot / DeepSeek / Google 抛的是普通 `Exception`，没有 `upstream_kind`。实测 Moonshot 429 quota → `reason=None` → 前端只能显示“这一轮没有成功，请稍后重试”，用户不会知道是欠费。

### 4.2 分类对重试策略的影响（实测请求数）

| 场景 | OpenAI（SDK 3 次） | Anthropic | Ollama | Moonshot/DeepSeek |
|---|---|---|---|---|
| 429 quota | 3 个请求（业务不重试） | 未测 | 1（业务不重试） | 1 |
| 401 revoked | **4** | 未测 | **4** | **4** |
| 400 | **4** | 未测 | **4** | **4** |
| 402 | **4** | — | — | — |
| 404 | 1（4xx 整数状态码被正确短路） | — | — | — |
| 500 | **12** | 未测 | **4** | **4** |

> Anthropic 的错误场景无法实测：生产传输层在发出请求前就 TypeError（0 个上游请求），HTTP 错误根本到不了分类逻辑。

根因：[error_types.py#L88-95](file:///D:/LiuHao-AI-OS/error_types.py#L88-L95) 对 401/403 返回的 `ProviderAPIError` **没有 `status_code`**，于是 `isinstance(last_error.status_code, int)` 为假（[providers.py#L329-333](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L329-L333)），落进通用重试分支。

---

## 5. 超时 / 重试 / 退避 / 计费放大

| 项 | 现状 | 实测/判定 | 期望行为 |
|---|---|---|---|
| 业务重试 | `max_retries=3`，指数退避 1/2/4s，**无 jitter**（[providers.py#L316-340](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L316-L340)） | 超时：**12 个上游请求 / 14.56s**；500：**12 个请求** | 单一重试层；对不可恢复错误（4xx、quota）零重试 |
| SDK 重试 | OpenAI/Anthropic SDK 默认 `max_retries=2` | 与业务层相乘 = 最多 12 次真实请求 | 显式设 `max_retries=0`，由业务层统一控制 |
| 超时 | `requests` 读超时 30s（per-read） | 心跳可无限续命：6s 心跳期间 1.5s 超时无效（S15/N8） | 全局 deadline + 空闲（无 token）超时 |
| 流式 fallback | 仅 HTTP 400 才重发（[L615-631](file:///D:/LiuHao-AI-OS/src/ai/providers.py#L615-L631)） | 429/500 不再翻倍（修复前曾 6 个请求） | 保持 |
| 空响应重试 | `generate_with_retry` 判空重试 4 次 | 空内容/拦答/多 block：**4 个请求** | 空响应最多重试 1 次，或直接失败（可能已计费） |
| 计费口径 | `completed` 空回复也记 `tokens_out=max(1, reply_chars//4)`（[chat.py#L905+](file:///D:/LiuHao-AI-OS/src/gateway/chat.py#L905)） | 空白气泡也会产生一条“用量” | 空回复不记账；实际用量优先取上游 usage |
| 取消 | 后端无取消/断流 API；前端 `streamChat` 无 `AbortController`、无超时（[chatClient.ts#L93-133](file:///D:/LiuHao-AI-OS/apps/console/console/src/lib/chatClient.ts#L93-L133)）；无停止按钮 | 上游不断流时无法自救 | 前端 abort + 后端检测客户端断开并终止生成 |

**计费放大结论**：超时和 5xx 是主要放大器（12 次）。超时往往意味着上游**已经收到并可能计费**（长响应被客户端提前放弃），重试会重复计费。建议：超时/5xx 总尝试次数上限 3（含 SDK），且对流式生成禁止自动重试。

---

## 6. 流结束与前端状态机

| 场景 | 当前实际行为 | 期望行为 |
|---|---|---|
| 后端异常且未 `finished` | `event_stream` 直接 `return`，**不发 done 帧**（[chat.py#L761-889](file:///D:/LiuHao-AI-OS/src/gateway/chat.py#L761-L889)） | 无论如何都发 done（`status=error`），让前端有确定终态 |
| 前端未收到 done | 仅把 `streaming=false`，**不设置 error**（ChatPanel L377 附近） | 未收到 done 视为失败 |
| 空 `completed` | 渲染 `m.content || '…'`（[ChatPanel.tsx#L483](file:///D:/LiuHao-AI-OS/apps/console/console/src/components/ChatPanel.tsx#L483)） | 空回复显示明确“模型未返回内容”，而不是省略号 |
| token 为 list | `last.content + evt.content` → `[object Object]` | 后端保证 token 必为字符串 |
| 失败文案 | `done.reason` + `error`（quota/401/429 可区分） | 保持，并给 Moonshot/DeepSeek/Google 补 `upstream_kind` |

---

## 7. 恶意 HTML / 脚本的渲染安全

| 检查点 | 结论 |
|---|---|
| Console 是否使用 `dangerouslySetInnerHTML` / `innerHTML` | **没有**（`apps/console/console/src` 全目录 0 匹配） |
| 模型正文渲染 | React 文本节点；`<script>`、`<img onerror>` 只会显示为字面文本 |
| 工具输出 | `<pre>` 文本（`toolOutputText` 对结构化输出做 `JSON.stringify`） |
| Butler 链接 | 仅 `https?://` 文本转 `<a>`（[butler.ts](file:///D:/LiuHao-AI-OS/apps/console/console/src/lib/butler.ts)） |
| 旧 `frontend/src/components/ChatMessage/ChatMessage.tsx` | 把模型输出里的 `[text](url)` 直接放进 `href`，**未校验 scheme**，存在 `javascript:` 风险；当前 console 未引用、未发布 |
| `plugins/markdown/markdown_plugin.py` | HTML 已转义，但 `href` 未校验 `javascript:`；未进入 console 链路 |

**结论**：当前上线界面（Console）对模型返回的恶意 HTML/脚本是**安全的**；风险仅在未发布的旧组件与未接入的 Markdown 插件，属于“以后接上就会踩”的预留坑。

---

## 8. “测试连接”假绿

`_probe` 只要 `provider.generate()` 返回非空就算成功（[models.py#L779+](file:///D:/LiuHao-AI-OS/src/gateway/models.py#L779)）。

实测：

| provider | mock 输入 | `_probe` 结果 |
|---|---|---|
| Ollama | `/api/generate` 200 + `{"error":"model 'drift-x' not found"}` | `ok=true`，detail 里显示**原始 error JSON** |
| Moonshot | 200 + `{"error":{...}}` | `ok=true`（原始 JSON 当回复） |
| DeepSeek | 200 + `{"error":{...}}` | `ok=true` |
| OpenAI | 200 + `error` 对象 | `ok=false`（`TypeError` → `upstream_error`，约 3.5s） |

**后果**：模型管理页显示“连接成功”，用户回到对话页立刻失败——这是最容易让用户认为“系统坏了”的组合。

期望：测试连接必须校验“响应是合法协议形态且无 error 字段”，而不是“非空即成功”。

---

## 9. 审计方法与复现

所有脚本在 `D:\_audit\provider-drift`（**不属于仓库**），使用 `d:\LiuHao-AI-OS\.venv`：

- `mock_provider.py`：`127.0.0.1:19013` 假上游，按请求体 `model="drift-<scenario>"` 选择场景；覆盖 `/v1/chat/completions`、`/chat/completions`、`/v1/messages`、`/api/chat`、`/api/generate`；支持 `: keepalive`、`event: ping`、半截帧、断连、心跳 stall 等。
- `run_audit.py` → `results.json`：全部 provider 的解析/流式/错误场景，复刻 `LiuHaoAssistant.chat_stream` 状态判定。
- `run_extra.py` → `extras.json`：`generate_with_retry` 的请求放大。
- `run_micro.py` → `micro.json`：拒答、200+quota、400 model。
- `run_timeout.py`：`hang_json` + `timeout=0.3` → **12 个请求 / 14.56s**。
- `run_stream_extra.py` → `stream_extra.json`：DeepSeek/Moonshot 流式（Moonshot 为降级路径）。
- `run_probe.py`：测试连接假绿验证。

复现：

```powershell
cd D:\_audit\provider-drift
D:\LiuHao-AI-OS\.venv\Scripts\python.exe run_audit.py
D:\LiuHao-AI-OS\.venv\Scripts\python.exe run_extra.py
D:\LiuHao-AI-OS\.venv\Scripts\python.exe run_micro.py
D:\LiuHao-AI-OS\.venv\Scripts\python.exe run_timeout.py
D:\LiuHao-AI-OS\.venv\Scripts\python.exe run_probe.py
```

> 端口 19013 曾被两个残留进程重复绑定导致 502；复现前先确认 `netstat -ano | findstr 19013` 无监听。

---

## 10. 限制与未覆盖

1. **Google Gemini** 无法用 `base_url` 指向 mock（SDK 忽略该参数），仅做静态判定，未实测。
2. **流式 OpenAI** 由 SDK 解析，mock 只能模拟字节流；`s_stall` 证明“心跳掩盖读超时”，但无法证明真实网络的中间设备行为。
3. **计费**只能从请求次数推断，无法验证厂商是否真的对超时请求计费。
4. **Anthropic 传输层失败**是当前 venv 的依赖组合事实；部署环境若锁定了不同 `anthropic` 版本，需以部署环境复测。
5. 本报告**不含修复方案实施**，且未改动任何仓库代码；工作区 `src/ai/providers.py` 的未提交改动不属本次审计产出。
