# invalid_key「测试连接」失败路径复现结论（CONCLUSION）

- 日期：2026-11-09
- 仓库：D:\LiuHao-AI-OS（只读，未做任何修改；未动 dist/）
- 复现时 HEAD：`8450009b74f37ef81ee29da7295d876b02b4edf9`（分支 p36；任务给定 ef1d8b21，HEAD 已被其他线推进）
- 证据文件：[failure-invalidkey-2026-11-09.json](failure-invalidkey-2026-11-09.json)（含请求/响应原文、端口、时间戳、审计事件、Key 全量扫描、断言明细）

## 结论：复现成立，三点断言全部 ✓，未发现产品缺陷

## 实测环境（全程与在跑的其他线隔离）

| 项 | 值 |
| --- | --- |
| 上游桩 | `stub_server.py` 监听 `127.0.0.1:11434`（SSRF 白名单端口，探测后确认空闲），对 `/v1/chat/completions` 固定返回 HTTP 401，响应体仿 OpenAI 文案并**故意回显 Bearer Key**（探针设计：原文含 Key 才能验证脱敏） |
| 网关实例 | `.venv` python `-m src.gateway`，env `LIUHAO_DATA_DIR=outputs\data`、`PORT=18760`、`HOST=127.0.0.1`，独立数据目录与端口，未杀任何既有进程 |
| 夹具 Key | `sk-invalid-TESTKEY9f2c8b1a4d6e`（非真实凭据） |
| 调用链 | 真实 HTTP：`POST /v1/accounts/register` → `POST /v1/auth/login` → `POST /v1/models/test`（provider=openai, model=gpt-4o-mini, base_url=http://127.0.0.1:11434/v1） |

上游到达的实证（**如实校准**）：evidence.json 的 `stub_received` 字段落盘为 `null`（桩侧回执捕获未成功），不能拿它当凭证；但网关侧两处实证证明 401 真实到达并被归类——`probe_log_lines`：`model probe failed provider=openai model=gpt-4o-mini reason=invalid_key detail=ProviderHTTPError: 模型服务返回 HTTP 401，详情已记录到服务端日志。`；以及响应 `technical_detail` 含脱敏 `sk-***` 的上游原文摘录（`Incorrect API key provided: sk-*** You can find your API key at https://platform.openai.com/account/api-keys.`），只有上游 401 原文按探针设计回显了 Bearer Key，才可能产生这串脱敏摘录。

## 三条断言与证据引用

### (a) 错误分类归到 invalid_key —— ✓
- 响应原文（`steps.test_connection.raw_response`，HTTP 200）：`{"ok":false,"reason":"invalid_key","hint":"API Key 无效：请检查是否填错或复制不完整","detail":"API Key 无效：请检查是否填错或复制不完整",...}`
- 证据引用：`assertions.a_invalid_key_classification.pass = true`
- 服务端日志（`probe_log_lines`）：`model probe failed provider=openai model=gpt-4o-mini reason=invalid_key detail=ProviderHTTPError: 模型服务返回 HTTP 401，详情已记录到服务端日志。`
- 审计链（`audit_event.rows[0]`，`audit_store.db`）：`outcome=failure`，details=`{"action":"ai.model_config.test_connection",...,"provider":"openai","reason":"invalid_key","vendor":"local"}`，不含任何 Key 字段。
- 分类链路（只读核对）：`src/ai/providers.py::classify_upstream_error`（401 且无吊销词 → `invalid_key`，异常携带 `upstream_kind`）→ `src/gateway/models.py::_classify_error` 优先读 `upstream_kind` → `_PROBE_HINTS["invalid_key"]` 中文可行动文案。

### (b) 文案中性 —— ✓（主文案；technical_detail 见下）
- 主文案 `reason/hint/detail/notice` 全部为中性中文，`hint` 与产品预设 `_PROBE_HINTS["invalid_key"]` 逐字一致；不含 `Incorrect API key provided`、`platform.openai.com`、`invalid_api_key` 等上游 401 原文标记（`assertions.b_neutral_message.main_copy_neutral=true`）。
- 全部用户可见字段（含 technical_detail）无 Key 明文（`key_plaintext_in_any_field=[]`）。
- **如实记录**：`technical_detail`（界面可展开的「技术详情」）为 `"Incorrect API key provided: sk-*** You can find your API key at https://platform.openai.com/account/api-keys."`——Key 已被 `sanitize_upstream_detail` 掩成 `sk-***`，但保留了脱敏后的上游句子与 OpenAI 官网链接。这是产品显式设计（`src/gateway/models.py::_probe` 注释："上游原文只进可展开的「技术详情」（只 message、已脱敏、≤120 字）；主文案必须是可行动的中文"）。若验收口径要求**任何**可见字段均不得回显上游原文，则该项需产品侧裁决；按主文案口径判定为 ✓。

### (c) 数据目录无 Key 明文 —— ✓
- 扫描范围：`LIUHAO_DATA_DIR` 全部 21 个文件（含 `logs/liuhao.log`、`audit_store.db` 及其 `-wal`/`-shm`、`model_registry.sqlite3`、`rbac.json`、`auth_secrets.json` 等），按原始字节匹配 Key 的 UTF-8 与 UTF-16LE 两种形态。
- 结果（`key_scan`）：`key_plaintext_hits=[]`，`utf8_count` 全为 0；运行期被网关持有的 `run/instance.lock` 在网关关停后补扫（1 字节，0 命中），无未解决文件。
- 上游 401 原文（含 Key）只存在于**网关进程内存**与**桩的日志/内存**（本探针自建），未落产品数据目录。

## 未覆盖处（如实标注）

1. 仅覆盖 `provider=openai`（OpenAI 兼容 SDK 路径）的 401；其他 provider（anthropic/gemini/ollama 等）的 invalid_key 归类未在本机逐一复现，但分类函数（`classify_upstream_error` / `_classify_error`）为同一条共用路径。
2. 仅覆盖「无吊销词」的 401；`key_revoked`（401+revoked）分支未复现。
3. `technical_detail` 是否允许保留脱敏上游摘录，属验收口径问题，不在本机裁决。
4. 复现时 HEAD 为 `8450009b`（任务书给定 `ef1d8b21` 期间其他线已推进仓库）；未做版本间差异比对。

## 产物清单

- `outputs/evidence.json` —— 请求/响应原文、端口、时间戳、桩收到的请求、审计事件、Key 全量扫描结果、断言明细
- `outputs/scripts/probe_ports.py` —— 白名单端口探测
- `outputs/scripts/stub_server.py` —— 回环 401 上游桩
- `outputs/scripts/run_case.py` —— 端到端驱动（启动网关/断言/落盘证据）
- `outputs/scripts/inspect_audit.py` —— 审计库只读检查
- `outputs/data/` —— 本实例独立临时数据目录（网关关停后原样保留作为证据）
- 未改动 D:\LiuHao-AI-OS 任何文件；未杀任何既有进程；未占用其他线端口。
