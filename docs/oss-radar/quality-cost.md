# 「开源技术雷达」第 8 路：质量、成本与可靠性

> 数据快照：2026-11-06。star、最近提交、许可证、公告均来自当日实际抓取：GitHub REST API（repos / commits / contents / security-advisories）、仓库 LICENSE 文件、OSV API、仓库目录树。
> 边界：本报告只做调研，不运行候选代码、不引入依赖、不改业务代码、不提交 Git。所有“未获取/未验证”项在第 5 节列出。
> 说明：star 与提交时间是快照，会持续变化；许可证以仓库根 LICENSE/COPYING 文件为准，不以 README 或 GitHub 侧栏为准。

---

## 0. 结论先行

1. **先补“度量”，再谈“优化”。** 鎏灏现在没有“换模型后功能掉了多少”的回归评测，也没有请求级 token/费用账本。这两件事不依赖任何新框架，应该先做。
2. **评测最小闭环 = 固定数据集 + 固定基线 + 三类指标 + 门禁。** 外部只建议引入 promptfoo（dev-only，跑模型对比）和 DeepEval（Python CI，跑工具调用/RAG 指标）。不建议把评测框架接进生产请求链路。
3. **成本计量的关键不是“算钱”，而是“记录真实 usage”。** 当前链路用的是字符数估算 + 最大输出预算，且配额在内存里，重启即归零。应先做 SQLite 账本与真实 usage 采集，再接价格表。
4. **可撤销要分三类，不能一概而论。** 文件/DB 可做快照与补偿；模型调用本身不可撤销（只能丢弃输出、回滚下游、保留审计）；外部副作用（发邮件、付款、调用第三方 API）只能审批前置 + 补偿，做不到真正 undo。
5. **多设备同步不引入云，最稳的是“文件级快照同步”，不是同步活 SQLite。** Syncthing 可做电脑↔手机的文件级同步与版本恢复；cr-sqlite 可做行级 CRDT，但当前成熟度不足；croc 近期有接收端任意文件覆盖/删除/RCE 公告，不建议直接用于接收。
6. **离线降级不是“缓存所有 API”。** 应明确能力矩阵：本地模型、历史只读、草稿、本地知识库继续可用；云端模型、云 Embedding、联网工具、同步明确禁用，并在 UI 说明原因，禁止静默降级到 mock。
7. **可靠性不要新增框架，先接线已有模块。** `src/resilience/` 已实现重试、熔断、舱壁、限流、降级链，但生产代码零引用；`src/workflow/checkpoint.py` 的 SQLite CheckpointManager 也零引用。先接线，再考虑 DBOS。

---

## 1. 该域能力现状表

| 能力 | 已实现（文件路径） | 实际接线状态 | 主要不足 |
|---|---|---|---|
| 模型/提示词回归评测 | `src/evaluation/benchmark_runner.py`、`src/evaluation/metrics.py`、`src/ai/eval_report.py`、`src/ai/vhl_benchmark.py`、`src/mlops/experiment_tracker.py` | PARTIAL。BenchmarkRunner 可跑数据集并写单条结果 JSON；`ExperimentTracker` 以内存 dict 保存；CLI 是假数据 | 没有接真实 Provider 的适配层；没有“baseline vs candidate”模型切换对比；没有工具调用成功率、RAG 忠实度、检索命中率；没有评测结果 SQLite；`src/cli/benchmark_commands.py:63-95` 种入假 run，`:128-141` 用 `hash(name)` 生成分数，不可用于验收 |
| 推理性能 / 首字延迟 | `src/ai/providers.py` 的 `chat_stream()`；`src/performance/cache.py` 的 `LRUCache/DiskCache/MultiTierCache/BatchCache` | PARTIAL。流式输出存在；缓存层未接主链路（仅测试/基准引用） | 没有 TTFT、tokens/s、首包时间、排队时间指标；没有记录 Ollama 返回的 `prompt_eval_count/eval_count`；没有 `num_ctx`、`keep_alive`、KV cache 类型、并行度配置；没有量化后质量回归 |
| 成本 / token / 预算 | `src/ai/liuhao.py`（估算 + 预留 + `account_spend`）、`src/ai/economy.py`、`src/kernels/resource/__init__.py`、`src/cost/cost_tracker.py`、`src/sre/cost/cost_manager.py`、`src/api/cost_api.py`、`config/default.yaml` 的 cost 段、`docs/QUOTA-ACCOUNTING-DESIGN.md` | PARTIAL。聊天入口会按“字符数/4 + 最大输出 token”估算并预扣；资源配额是内存 dict；CostTracker/BudgetEngine/cost_api 均零生产引用；cost 配置段未接线 | 没有真实 usage；没有请求级账本；没有价格快照；没有输入/输出分开计价；中文按 chars/4 明显低估；按最大输出预扣会高估且不结算差额；重启后配额归零；无成本 UI |
| 可解释性 | `src/kernels/audit/`（哈希链审计）、`src/gateway/policy.py`（策略决策）、`src/observability/tracing.py`（OTel，默认关闭）、`docs/POLICY-RULE-OBSERVABILITY-DESIGN.md` | PARTIAL。审计与策略拒绝理由存在；追踪默认关闭 | 没有记录“本次回答用了哪个 prompt 版本、哪些检索文档、哪个工具 schema、模型路由原因”；没有面向用户的“为什么这么做”视图；审计是事后账本，不是决策解释 |
| 可撤销 | `src/model_registry_mlops.py`（模型版本 promote/rollback）、`src/ai/evolution.py`（仅状态流转）、`src/workflow/checkpoint.py`（SQLite CheckpointManager，零引用）、`src/kernels/execution/_journal.py`（执行日志，已接目标恢复）、`src/kernels/execution/__init__.py:1633-1647`（`create_checkpoint()` 仅内存 dict） | NOT DONE（通用撤销）；PARTIAL（目标级恢复） | 没有文件写入前快照、没有 DB 补偿事务、没有工具副作用逆操作、没有“撤销本次自动化动作”的入口；`create_checkpoint()` 注释声称 for rollback，但全文件没有 rollback |
| 多设备同步 | `apps/console/console/public/sw.js`、PWA manifest、手机 HTTPS 启动脚本 | NOT DONE | 没有设备配对、设备注册/吊销、增量同步、冲突解决、二维码配对；没有明确“哪些数据可同步”的边界 |
| 离线与弱网降级 | `apps/console/console/public/sw.js` | PARTIAL。Service Worker 只缓存外壳/静态资源，明确不缓存 API | 没有离线能力矩阵、没有离线横幅、没有本地只读缓存、没有待发送队列/outbox、没有“本地模型可用但云端不可用”的中间态 |
| 可靠性工程 | `src/ai/providers.py`（重试）、`src/resilience/`（重试/熔断/舱壁/限流/降级链，零生产引用）、`src/gateway/rate_limiter.py`、`src/kernels/execution/_journal.py`、`src/gateway/ai_management.py:202-229`（journal 已接）、`src/workflow/checkpoint.py` | PARTIAL | Provider 重试无 jitter、无熔断、无 deadline、无幂等键；`src/resilience` 全模块未接；`src/workflow/checkpoint.py` 未接；`src/tasks/models.py` 只有数据模型，没有队列/worker/后台重跑；journal 明确不做副作用去重（见 `_journal.py:29`） |

### 1.1 零引用/假实现点名

这些不是“候选不足”，而是当前实现与本报告判断直接相关的事实：

- `src/resilience/`：实现了 `Retry`、`CircuitBreaker`、`Bulkhead`、`TokenBucketRateLimiter`、`FallbackHandler`、`DegradationChain`，但除测试外无生产引用。
- `src/cost/cost_tracker.py`：实现了预算、告警、预测、CSV/JSON 导出，但零生产引用。
- `src/sre/cost/cost_manager.py`：零引用，且内存态。
- `src/api/cost_api.py`：不在 `src/gateway/main.py` 的挂载路由中。
- `src/performance/cache.py`：`MultiTierCache` 等未接聊天主链路，仅测试/基准使用（`docs/CACHE-LAYER-DESIGN.md` 有说明）。
- `src/workflow/checkpoint.py`：SQLite `CheckpointManager` 未接工作流执行器。
- `src/kernels/execution/__init__.py:1633-1647`：`create_checkpoint()` 只是把 dict append 到 `ctx.checkpoints`，没有持久化，也没有回滚消费方。
- `src/cli/benchmark_commands.py`：CLI 列表是假数据，分数由 `hash(name)` 派生，不能作为任何“模型变好/变差”的证据。
- `config/default.yaml` 的 `cost.*` 段：当前未接线，不能误以为预算已生效。

---

## 2. 候选清单（按“值得引入程度”排序）

排序原则：先解决当前最大缺口；优先能独立封装、许可证清晰、维护活跃、无高危公告、能写回归测试的候选；能用现有模块接线的，不新增依赖。

### 2.1 候选元数据总表

| # | 候选 | Star | 最近提交 | 技术栈 | 许可证（LICENSE 文件） | 维护状态 | 已知公告 | 核心源码目录 |
|---|---|---|---:|---|---|---|---|---|---|
| 1 | [promptfoo](https://github.com/promptfoo/promptfoo) | 26,880 | 2026-11-05 | TypeScript / Node | MIT | 活跃，2026-11 仍在提交；929 open issues | npm 包 OSV 0 条 | `src/assertions/`、`src/evaluator/`、`src/providers/`、`src/redteam/` |
| 2 | [DeepEval](https://github.com/confident-ai/deepeval) | 19,319 | 2026-11-05 | Python / pytest | Apache-2.0 | 活跃；807 open issues | PyPI OSV 0 条 | `deepeval/metrics/`、`deepeval/evaluate/`、`deepeval/test_run/`、`deepeval/dataset/` |
| 3 | [genai-prices](https://github.com/pydantic/genai-prices) | 409 | 2026-11-05 | Python + 多语言价格数据 | MIT | 活跃，Pydantic 团队维护；52 open issues | PyPI OSV 0 条 | `prices/`、`packages/python/`、`packages/js/` |
| 4 | [tokenizers](https://github.com/huggingface/tokenizers) | 11,222 | 2026-11-05 | Rust + Python bindings | Apache-2.0 | 活跃；199 open issues | PyPI OSV 0 条 | `tokenizers/`（Rust）、`bindings/python/` |
| 5 | [DBOS Transact Python](https://github.com/dbos-inc/dbos-transact-py) | 1,666 | 2026-11-04 | Python + SQLite/Postgres | MIT | 活跃；9 open issues | PyPI OSV 0 条 | `dbos/_core.py`、`dbos/_recovery.py`、`dbos/_queue.py`、`dbos/_sys_db_sqlite.py` |
| 6 | [Syncthing](https://github.com/syncthing/syncthing) | 90,127 | 2026-11-02 | Go | MPL-2.0 | 活跃；398 open issues | Go 模块 OSV 4 条，均为 2017–2022 年已修复问题 | `lib/model/`、`lib/versioner/`、`lib/sync/`、`cmd/` |
| 7 | [llama.cpp](https://github.com/ggml-org/llama.cpp) | 133,470 | 2026-11-05 | C/C++ | MIT | 极活跃；2,570 open issues | 仓库 security-advisories 13 条，含 RPC 未授权 RCE、GBUF/GGUF 解析越界、server 越界写 | `src/llama-kv-cache.cpp`、`src/llama-quant.cpp`、`tools/llama-bench/`、`tools/server/`、`tools/quantize/` |
| 8 | [Workbox](https://github.com/GoogleChrome/workbox) | 13,042 | 2026-09-02 | JavaScript / Service Worker | MIT | 活跃；87 open issues | npm workbox-* OSV 0 条 | `packages/workbox-strategies/src/`、`packages/workbox-background-sync/src/`、`packages/workbox-routing/src/` |
| 9 | [Inspect AI](https://github.com/UKGovernmentBEIS/inspect_ai) | 3,187 | 2026-11-05 | Python | MIT | 活跃；496 open issues | PyPI OSV 0 条 | `src/inspect_ai/model/`、`scorer/`、`solver/`、`tool/`、`log/` |
| 10 | [restic](https://github.com/restic/restic) | 37,013 | 2026-11-05 | Go | BSD-2-Clause | 活跃；652 open issues | Go 模块 OSV 0 条 | `internal/repository/`、`internal/restorer/`、`internal/archiver/`、`internal/backend/` |
| 11 | [tenacity](https://github.com/jd/tenacity) | 8,818 | 2026-11-01 | Python | Apache-2.0 | 活跃；86 open issues | PyPI OSV 0 条 | `tenacity/retry.py`、`wait.py`、`stop.py`、`before.py`、`after.py` |
| 12 | [Taskiq](https://github.com/taskiq-python/taskiq) | 2,368 | 2026-10-08 | Python asyncio | MIT | 活跃；143 open issues | PyPI OSV 0 条 | `taskiq/brokers/`、`taskiq/middlewares/`、`taskiq/result_backends/` |
| 13 | [OpenLLMetry](https://github.com/traceloop/openllmetry) | 7,522 | 2026-11-05 | Python / OpenTelemetry | Apache-2.0 | 活跃；840 open issues | PyPI 相关包 OSV 0 条 | `packages/opentelemetry-instrumentation-*/` |
| 14 | [Headscale](https://github.com/juanfont/headscale) | 45,402 | 2026-11-05 | Go | BSD-3-Clause | 活跃；95 open issues | Go 模块 OSV 1 条（2023，日志泄露 bearer token） | `hscontrol/`、`cmd/` |
| 15 | [cr-sqlite](https://github.com/vlcn-io/cr-sqlite) | 3,825 | 2026-08-10 | Rust/C + SQLite 扩展 | MIT | 有提交但最新 release 停在 2024-01；56 open issues | OSV/CVE 0 条 | `core/src/crsqlite.c`、`core/src/changes-vtab.c`、`core/src/ext-data.c`、`py/` |
| 16 | [llama-cpp-python](https://github.com/abetlen/llama-cpp-python) | 10,667 | 2026-10-01 | Python + C++ | MIT | 活跃但 709 open issues | PyPI OSV 2 条：模型元数据 Jinja 模板 SSTI 导致 RCE、任意文件读 | `llama_cpp/llama.py`、`llama_cpp/_internals.py`、`llama_cpp/server/` |
| 17 | [Ragas](https://github.com/vibrantlabsai/ragas) | 16,248 | 2026-02-24 | Python | Apache-2.0 | 8 个月无提交；650 open issues | PyPI OSV 4 条：SSRF、任意文件读等 | `src/ragas/metrics/`、`src/ragas/testset/`、`src/ragas/llms/` |
| 18 | [tokencost](https://github.com/AgentOps-AI/tokencost) | 2,003 | 2025-09-05 | Python | MIT | 14 个月无提交；31 open issues | PyPI OSV 0 条 | `tokencost/`（costs/models） |
| 19 | [tiktoken](https://github.com/openai/tiktoken) | 19,608 | 2026-08-17 | Python/Rust | MIT | 活跃；154 open issues | PyPI OSV 0 条 | `tiktoken/`（core/registry） |

> 测试覆盖：以上仓库均存在 tests/ 或同目录内联测试（Go/Rust 项目为 `_test.go` / `#[test]`）；具体覆盖率均未获取（未运行候选测试，未读取 Codecov 报告）。

### 2.2 九问速答（非深评候选）

以下候选的详细评估见本节；“值得引入程度”较低的项也如实写出不引入理由。

#### #6 Syncthing（深评见 3.5；此条只为对照）
见 3.5。

#### #7 llama.cpp

- **Q1 鎏灏是否已有**：有本地推理链路，`src/ai/providers.py` 的 `OllamaProvider` 调 `/api/chat`、`/api/generate`、`/api/embeddings`；但没有直接打包 llama.cpp，也没有量化/KV cache 参数。
- **Q2 当前不足**：Ollama 默认参数不透明；没有记录 `eval_count`/`prompt_eval_count`；没有 TTFT；没有量化等级与质量回归；没有并发/上下文配置基线。
- **Q3 候选能解决什么**：提供 CPU 推理、GGUF 量化（Q4/Q5/Q8、K-quants）、KV cache 类型/量化、`llama-bench` 可复现吞吐测量、OpenAI 兼容 server、prompt cache。
- **Q4 值得借鉴模块**：`src/llama-kv-cache.cpp`（KV cache 行为）、`src/llama-quant.cpp`（量化）、`tools/llama-bench/`（pp/tg 指标）、`tools/server/`（流式与 usage 返回）、`tools/quantize/`。
- **Q5 能否独立封装**：能。作为独立二进制/子进程，不侵入 Python 业务代码；或用其 server 替代 Ollama 的本地推理端点。
- **Q6 是否破坏接口**：不破坏。Ollama 与 llama.cpp server 都是 HTTP，`src/ai/providers.py` 只需新增 provider 或改 base_url。
- **Q7 许可证**：MIT，允许使用、修改、再分发；无 GPL/AGPL。
- **Q8 安全与维护**：13 条仓库公告，含 RPC 后端未授权 RCE、GGUF 解析越界、server 越界写。**绝不能把 server 暴露到公网，也不能加载不可信 GGUF**。C++ 供应链与构建成本中等。
- **Q9 测试**：新增 `scripts/bench_inference.py`，固定 prompt、固定模型、记录 TTFT/总时/吞吐；对同一组评测用例比较量化前后质量；在 CI 只跑小模型冒烟，不设硬性性能阈值，避免机器差异导致 flaky。

#### #8 Workbox

- **Q1 已有**：`apps/console/console/public/sw.js` 手写 Service Worker，缓存外壳/静态资源，明确不缓存 API。
- **Q2 不足**：没有离线能力矩阵、没有后台同步/重放、没有按用户隔离缓存、没有离线状态 UI；手写 SW 容易漏更新。
- **Q3 解决什么**：成熟的缓存策略（CacheFirst/NetworkFirst/StaleWhileRevalidate）、路由、过期、后台同步队列；减少手写 SW 的边界错误。
- **Q4 借鉴模块**：`packages/workbox-strategies/src/`、`packages/workbox-background-sync/src/`、`packages/workbox-routing/src/`、`packages/workbox-precaching/src/`。
- **Q5 独立封装**：能，但会替换现有 SW 构建流程；建议只用 `workbox-strategies` + `workbox-background-sync`，不整体迁移。
- **Q6 破坏接口**：不破坏后端 API；会改变前端构建与 SW 生命周期，需要版本化与 `skipWaiting` 策略。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条 npm 公告；风险在缓存作用域：不能缓存 `/v1/auth`、`/v1/chat` POST、SSE、带用户隐私的 GET；登出必须清缓存；避免缓存投毒。
- **Q9 测试**：Playwright 离线模式；断言外壳可打开、离线横幅出现、云端功能按钮禁用且给出原因、待发送操作进入队列、恢复网络后按幂等键重放；断言 API 响应不被缓存。

#### #9 Inspect AI

- **Q1 已有**：`src/evaluation/benchmark_runner.py`、`src/evaluation/metrics.py`；有 AgentRuntime 的任务成功评估，但不是模型质量评估。
- **Q2 不足**：没有 agent 轨迹（tool call 序列）评分、没有沙箱执行、没有多模型对比报告。
- **Q3 解决什么**：面向 agent/工具的评测框架，支持 solver/scorer、工具调用轨迹、日志查看、沙箱。
- **Q4 借鉴模块**：`src/inspect_ai/scorer/`、`src/inspect_ai/solver/`、`src/inspect_ai/tool/`、`src/inspect_ai/log/`。
- **Q5 独立封装**：能，作为 dev-only CLI/测试依赖；不接生产链路。
- **Q6 破坏接口**：不破坏；新增评测脚本即可。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条 PyPI 公告；评测会执行模型生成的代码/工具，必须在沙箱内跑；496 open issues，版本需锁定。
- **Q9 测试**：用固定 trajectory fixture 写 scorer 单测；对同一数据集跑 baseline/candidate，输出差异报告；沙箱逃逸用例单独标记 skip（不在 CI 跑真实逃逸）。

#### #10 restic

- **Q1 已有**：`src/backup/backup_manager.py` 有备份/恢复/保留策略的数据结构；但是否接主链路未验证。
- **Q2 不足**：没有内容寻址快照、没有加密仓库、没有增量去重、没有跨设备恢复验证。
- **Q3 解决什么**：加密、去重、增量、可校验的备份仓库；适合灾难恢复和“改动前快照”。
- **Q4 借鉴模块**：`internal/repository/`、`internal/restorer/`、`internal/archiver/`、`internal/backend/`。
- **Q5 独立封装**：能，作为外部二进制；不侵入 Python。
- **Q6 破坏接口**：不破坏；但需要管理仓库密码/密钥，且恢复是整仓/整目录级，不是单次动作级 undo。
- **Q7 许可证**：BSD-2-Clause。
- **Q8 安全与维护**：0 条 Go 模块公告；仓库密码丢失=数据不可恢复；不要把密码写入日志/配置明文。
- **Q9 测试**：临时目录 round-trip：备份→删除→恢复→校验哈希；增量备份后校验去重；错误密码必须失败；恢复路径穿越用例必须拒绝。

#### #11 tenacity

- **Q1 已有**：`src/resilience/retry.py` 已有 `Retry`、`RetryConfig`、`BackoffStrategy`、jitter；`src/ai/providers.py:95-174` 也有手写重试。
- **Q2 不足**：Provider 手写重试无 jitter、无总 deadline、无熔断；两套重试逻辑并存。
- **Q3 解决什么**：成熟的 wait/stop/retry 组合与异步支持；可作为局部补充。
- **Q4 借鉴模块**：`tenacity/wait.py`、`tenacity/stop.py`、`tenacity/retry.py`。
- **Q5 独立封装**：能，但是装饰器式侵入；更适合包在单个 provider 调用外层。
- **Q6 破坏接口**：不破坏；但会与 `src/resilience/retry.py` 重复，违反“优先复用现有模块”。
- **Q7 许可证**：Apache-2.0。
- **Q8 安全与维护**：0 条公告，依赖极小；维护活跃。风险是重复实现导致行为不一致。
- **Q9 测试**：jitter 上下界、最大重试次数、不可重试异常、总耗时上限、异步取消；与现有 `Retry` 行为做对照测试。

#### #12 Taskiq

- **Q1 已有**：`src/tasks/models.py` 只有 `Task/TaskResult/TaskStatus` 数据模型，没有 broker/worker。
- **Q2 不足**：长任务没有队列、没有后台 worker、没有跨进程重跑；API 进程崩溃会中断。
- **Q3 解决什么**：asyncio 任务队列、broker、middleware、result backend；可做后台任务。
- **Q4 借鉴模块**：`taskiq/brokers/`、`taskiq/middlewares/`、`taskiq/result_backends/`。
- **Q5 独立封装**：能，但需要 broker（Redis/RabbitMQ/InMemory）；InMemory 重启丢任务，不适合“长任务续跑”。
- **Q6 破坏接口**：不破坏业务 API；新增 worker 进程与部署依赖。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条公告；broker 需要认证/网络隔离；143 open issues。
- **Q9 测试**：enqueue→worker 执行→结果落库；worker kill 后任务是否重投；重复投递是否幂等；result backend 断连恢复。

#### #13 OpenLLMetry

- **Q1 已有**：`src/observability/tracing.py` 有 OTel 基础设施，默认关闭；`src/kernels/audit/` 有审计。
- **Q2 不足**：没有 LLM/Tool/Retriever 的标准化 span；没有 prompt/token/cost 属性；默认关闭导致线上无追踪。
- **Q3 解决什么**：自动埋点 OpenAI/Ollama 等 SDK，产出 GenAI span；可作为 OTel 语义约定的实现参考。
- **Q4 借鉴模块**：`packages/opentelemetry-instrumentation-*/`，尤其是 Ollama/OpenAI 的 span 属性与 token 记录。
- **Q5 独立封装**：能，但自动 instrumentation 会 monkeypatch SDK，侵入性较强；建议只借鉴属性定义，手写少量 span。
- **Q6 破坏接口**：不破坏业务返回值；会改变全局 SDK 行为与性能。
- **Q7 许可证**：Apache-2.0。
- **Q8 安全与维护**：0 条公告；最大风险是 prompt/completion 被发送到外部遥测后端，必须默认脱敏、本地存储、关闭内容采集。
- **Q9 测试**：断言 span 存在且属性齐全；默认不记录 prompt/completion；采样率生效；后端不可用时不影响主请求。

#### #14 Headscale

- **Q1 已有**：手机 HTTPS/PWA 已有，但没有设备网络层；没有远程访问组网。
- **Q2 不足**：手机只能同局域网访问；没有设备身份、吊销、NAT 穿透。
- **Q3 解决什么**：自托管 WireGuard 协调服务器，让手机安全访问家里的电脑；不解决数据同步。
- **Q4 借鉴模块**：`hscontrol/`（节点/策略/ACL）、`cmd/`。
- **Q5 独立封装**：能，作为独立网络服务；不侵入应用代码。
- **Q6 破坏接口**：不破坏；新增一个常驻服务与设备配置。
- **Q7 许可证**：BSD-3-Clause。
- **Q8 安全与维护**：1 条 2023 公告（日志泄露 bearer token，已修复）；协调服务器被攻破=网络入口失守；需要 ACL、密钥轮换、日志脱敏。
- **Q9 测试**：两台设备加入→ACL 允许/拒绝→吊销设备后不可达；不测真实公网穿透，只做本地 mock 协调。

#### #15 cr-sqlite

- **Q1 已有**：没有跨设备行级同步；`src/kernels/*` 的 SQLite 是单机库。
- **Q2 不足**：只能整库文件同步，活 SQLite/WAL 文件不能安全同步；没有冲突解决。
- **Q3 解决什么**：SQLite 的 CRDT 扩展，支持行级多主复制、变更集合并。
- **Q4 借鉴模块**：`core/src/crsqlite.c`、`core/src/changes-vtab.c`、`core/src/ext-data.c`、`py/`。
- **Q5 独立封装**：能，但需要 SQLite 扩展加载、表结构改造（CRR 表）、Rust/C 构建。
- **Q6 破坏接口**：会破坏现有 schema/查询假设；审计哈希链、配额、账本等表不适合直接 CRDT 化。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条公告；但最新 release 停在 2024-01，成熟度与长期维护风险高；扩展加载本身有供应链风险。
- **Q9 测试**：两端并发写同一行→合并结果确定；离线变更集→上线后收敛；冲突策略单测；绝不拿审计表做实验。

#### #16 llama-cpp-python

- **Q1 已有**：`src/ai/providers.py` 已通过 Ollama 使用 llama.cpp 能力。
- **Q2 不足**：没有进程内嵌入；没有可控的 KV cache/量化参数；依赖 Ollama 守护进程。
- **Q3 解决什么**：Python 直接加载 GGUF，进程内推理；适合便携打包。
- **Q4 借鉴模块**：`llama_cpp/llama.py`、`llama_cpp/_internals.py`、`llama_cpp/server/`。
- **Q5 独立封装**：能，作为可选 backend；与 Ollama 二选一。
- **Q6 破坏接口**：不破坏 `BaseProvider`；新增 backend 即可；但二进制体积/构建复杂度上升。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：2 条严重公告：GGUF 模型元数据中的 Jinja chat template 可导致 SSTI/RCE；任意文件读。必须只加载可信模型、禁用/剥离自定义模板、限制文件访问。709 open issues。
- **Q9 测试**：恶意 GGUF 模板用例必须被拒绝；路径遍历用例必须失败；与 Ollama 输出一致性对照；CPU 内存上限测试。

#### #17 Ragas

- **Q1 已有**：`src/evaluation/metrics.py` 有 BLEU/ROUGE/NDCG 等；`src/knowledge/` 有检索，但没有 RAG 忠实度/答案相关性指标。
- **Q2 不足**：没有 context precision/recall、faithfulness、answer correctness；没有 RAG 数据集生成。
- **Q3 解决什么**：RAG 专用指标与测试集生成。
- **Q4 借鉴模块**：`src/ragas/metrics/`、`src/ragas/testset/`、`src/ragas/llms/`。
- **Q5 独立封装**：能，dev-only；不要接生产。
- **Q6 破坏接口**：不破坏；新增评测脚本。
- **Q7 许可证**：Apache-2.0。
- **Q8 安全与维护**：4 条公告（SSRF/任意文件读）；8 个月无提交；650 open issues。只处理本地/可信数据，锁定版本。
- **Q9 测试**：固定 KB 快照→固定检索结果→指标可复现；SSRF 用例必须拒绝；与人工标注小样本做一致性校验。

#### #18 tokencost

- **Q1 已有**：`src/ai/economy.py` 有价格表/成本计算；`config/model_pricing.json` 有价格配置。
- **Q2 不足**：价格覆盖与更新机制不清晰；没有输入/输出分开计价；没有价格版本快照。
- **Q3 解决什么**：轻量价格计算与 token→cost 转换。
- **Q4 借鉴模块**：`tokencost/` 的价格表结构与模型映射。
- **Q5 独立封装**：能，但只是一张价格表；可直接复用 `genai-prices`。
- **Q6 破坏接口**：不破坏。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条公告；14 个月无提交，价格易过期；不能作为唯一价格源。
- **Q9 测试**：价格表版本测试；未知模型必须返回“未定价”而不是 0；输入/输出分别计价；与供应商账单抽样对账。

#### #19 tiktoken

- **Q1 已有**：`src/ai/liuhao.py` 用 `chars/4` 估算，没有 tokenizer。
- **Q2 不足**：字符估算对中文严重失真；无法区分模型 tokenizer。
- **Q3 解决什么**：OpenAI 系模型的精确 token 计数。
- **Q4 借鉴模块**：`tiktoken/core.py`、`tiktoken/registry.py`。
- **Q5 独立封装**：能，作为可选计数后端。
- **Q6 破坏接口**：不破坏。
- **Q7 许可证**：MIT。
- **Q8 安全与维护**：0 条公告；只覆盖 OpenAI 编码，不适用于 DeepSeek/Qwen 等国产模型；不能拿它冒充通用计数器。
- **Q9 测试**：中英混合样本计数；与 Provider 返回 usage 对照；未知模型回退必须标记 `usage_source=estimate`。

---

## 3. 最值得的 3–5 个深评

### 3.1 promptfoo（评测编排，dev-only）

**仓库字段**：`promptfoo/promptfoo`；26,880 star；最近提交 2026-11-05；TypeScript/Node；MIT；活跃，929 open issues；npm 包 OSV 0 条；核心目录 `src/assertions/`、`src/evaluator/`、`src/providers/`、`src/redteam/`；有 `test/` 与 CI。

**为什么排第一**：它用声明式 YAML 把“同一组用例 × 多个模型/提示词”跑成对比矩阵，并且内置了工具调用、轨迹、成本、延迟断言。对“把 deepseek-chat 换成另一个模型，掉了多少”这个问题，promptfoo 是最短路径，不需要先把评测框架写进鎏灏代码。

**九问**：

1. **已有同类**：`src/evaluation/benchmark_runner.py` 有数据集/结果模型；`src/evaluation/metrics.py` 有 BLEU/ROUGE/NDCG；但没有多模型对比矩阵，也没有工具调用断言。
2. **当前不足**：没有 baseline/candidate 概念；没有工具名/参数/schema 校验；没有轨迹断言；没有成本/延迟断言；CLI 假数据不可信。
3. **候选解决**：同一用例集跑多 provider/model，输出通过率、失败原因、token/成本/延迟；支持 `functionToolCall`、`toolCallF1`、`trajectory`、`cost`、`latency` 等断言；可接 OpenAI-compatible 端点（包括本地 Ollama/网关）。
4. **值得借鉴模块**：`src/assertions/functionToolCall.ts`（工具调用 F1）、`src/assertions/trajectory.ts`（轨迹）、`src/assertions/cost.ts`、`src/assertions/latency.ts`、`src/evaluator/`（矩阵执行与汇总）、`src/providers/`（多 provider 适配）。
5. **能否独立封装**：能。作为仓库外 dev 工具/CI job，不进入 Python 运行时，不新增生产依赖。
6. **会不会破坏接口**：不会。只读调用 `/v1/chat` 或直接调用 provider；不写业务库。评测产物写独立目录/独立 SQLite。
7. **许可证**：MIT，允许使用与修改；无 GPL/AGPL。
8. **安全与维护**：Node 供应链风险；npm 包 0 条公告；929 open issues，需锁定版本；评测会发送真实 prompt 到模型，数据集不能含真实用户隐私；redteam 功能不要对生产端点跑。
9. **集成测试与回归测试**：
   - 固定 3 套用例：chat 质量、工具调用、KB 问答；每套先 30–50 条，按实际方差再扩。
   - baseline 结果提交为 JSON/CSV，CI 跑 candidate 并与 baseline 比较；阈值由人工拍板。
   - 工具调用用例必须断言：工具名、参数 JSON schema、是否多余调用、是否漏调用、是否在允许工具集内。
   - KB 用例必须冻结检索结果（先不重新检索），单独再跑一遍“候选 Embedding + 候选生成”的端到端，避免把检索退化和生成退化混在一起。
   - 用假 provider 做确定性单测；真实 provider 只在 nightly/手动跑，避免费用与不确定性。

**落地方式（最小）**：
- 新增 `docs/eval/` 或 `evals/`（不放进 `src/`），写 `promptfooconfig.yaml`、数据集、断言。
- 新增 `scripts/eval_model_swap.sh|ps1`，启动本地网关，传 `--model candidate`，输出 `eval-results/<timestamp>/`。
- 不修改 `src/` 业务代码；不把 promptfoo 加进 `pyproject.toml`。

**不做什么**：不把 promptfoo 接进 `/v1/chat`；不把 redteam 对公网端点跑；不把评测结果直接写成“模型已通过”的验收结论。

---

### 3.2 DeepEval（Python CI 的评测层）

**仓库字段**：`confident-ai/deepeval`；19,319 star；最近提交 2026-11-05；Python/pytest；Apache-2.0；活跃，807 open issues；PyPI OSV 0 条；核心目录 `deepeval/metrics/`、`deepeval/evaluate/`、`deepeval/test_run/`、`deepeval/dataset/`；有 `tests/`。

**为什么值得**：如果希望评测留在 Python/pytest 体系内，DeepEval 比 promptfoo 更贴近现有 `pytest -q` 工作流。它内置 Tool Correctness、Task Completion、Tool Use、Answer Relevancy、Faithfulness、Contextual Precision/Recall 等指标，能直接在 CI 里 `assert_test`。

**九问**：

1. **已有同类**：`src/evaluation/metrics.py`、`src/evaluation/benchmark_runner.py`；没有工具调用/RAG 忠实度指标。
2. **当前不足**：指标偏文本相似度；没有 LLM-as-judge 的 rubric；没有工具调用参数校验；没有 RAG 上下文指标；没有数据集版本管理。
3. **候选解决**：pytest 风格的评测用例、内置指标、数据集管理、结果持久化、CI 门禁；可自定义 judge 模型。
4. **值得借鉴模块**：`deepeval/metrics/tool_correctness/`、`deepeval/metrics/task_completion/`、`deepeval/metrics/tool_use/`、`deepeval/metrics/faithfulness/`、`deepeval/evaluate/`、`deepeval/test_run/`。
5. **能否独立封装**：能，作为 dev/test 依赖；生产环境不安装。
6. **会不会破坏接口**：不会；只读调用网关/Provider。数据集与结果放独立目录。
7. **许可证**：Apache-2.0，允许使用与修改；无 GPL/AGPL。
8. **安全与维护**：0 条公告；807 open issues；LLM-as-judge 会把 prompt/输出发给 judge 模型，评测数据需脱敏；judge 模型与候选模型不能是同一个（否则自评偏差），至少要用不同模型或规则校验。
9. **集成测试与回归测试**：
   - `tests/eval/test_model_regression.py`：同一数据集跑 baseline/candidate，断言指标差不低于阈值。
   - `tests/eval/test_tool_contract.py`：工具名/参数/schema/权限；用假工具记录调用序列，不执行真实副作用。
   - `tests/eval/test_rag_quality.py`：固定 chunk 快照，断言 faithfulness/context recall；不联网。
   - 指标阈值先“记录”不“阻断”，跑 2–3 周建立基线后再上门禁。

**落地方式**：新增 `tests/eval/` 与 `scripts/eval_regression.py`；用 `provider_for_task()` 注入候选模型；结果写 `data/eval.db` 或 JSON；CI 只在 PR 跑小样本，nightly 跑全量。

**与 promptfoo 的分工**：
- promptfoo：跨模型/跨 prompt 的快速对比矩阵，适合产品/人工评审。
- DeepEval：Python CI 内的硬指标门禁，适合工程回归。
- 两者不要同时作为生产依赖。

---

### 3.3 genai-prices + tokenizers（成本计量底座）

**仓库字段（genai-prices）**：`pydantic/genai-prices`；409 star；最近提交 2026-11-05；Python + 多语言价格数据；MIT；活跃，Pydantic 团队维护，52 open issues；PyPI OSV 0 条；核心目录 `prices/`、`packages/python/`、`packages/js/`。

**仓库字段（tokenizers）**：`huggingface/tokenizers`；11,222 star；最近提交 2026-11-05；Rust + Python bindings；Apache-2.0；活跃，199 open issues；PyPI OSV 0 条；核心目录 `tokenizers/`、`bindings/python/`。

**为什么值得**：成本计量的难点不是乘法，而是“价格数据可信 + token 数可信 + 历史可追溯”。`genai-prices` 提供多语言、带 schema 的价格数据；`tokenizers` 可用本地 `tokenizer.json` 计算真实 token 数（对国产模型也可用，只要拿到 tokenizer 文件）。两者都小、许可证干净、无公告。

**九问**：

1. **已有同类**：`src/ai/economy.py` 有 `BillingEngine/BudgetEngine` 和价格表；`config/model_pricing.json` 有价格配置；`src/ai/liuhao.py` 有估算与预扣；`src/cost/cost_tracker.py` 有预算/告警/预测。
2. **当前不足**：价格是单一 blended 费率；没有输入/输出分开；没有价格版本/生效日期；没有币种；没有缓存命中价；没有真实 token 数；没有持久账本。
3. **候选解决**：`genai-prices` 提供结构化价格（输入/输出/缓存等）与多语言访问；`tokenizers` 提供真实分词计数。两者组合能替代 `chars/4` 估算。
4. **值得借鉴模块**：`prices/` 的价格 schema 与模型映射；`packages/python/` 的查询 API；`tokenizers/` 的 `Tokenizer.from_file` 与 encode；`bindings/python/` 的批量计数。
5. **能否独立封装**：能。价格查询与 token 计数各自是纯函数/小模块，可在 `src/cost/` 内包一层，不侵入业务。
6. **会不会破坏接口**：不会。`BaseProvider` 返回值不变；在 provider 包装层/账本层采集 usage。
7. **许可证**：genai-prices MIT；tokenizers Apache-2.0；均无 GPL/AGPL。
8. **安全与维护**：0 条公告；tokenizers 是 Rust 原生扩展，增加二进制体积；价格数据仍可能滞后于供应商调价，必须允许“本地覆盖价”和“未定价”状态，不能把未知模型按 0 计费。
9. **集成测试与回归测试**：
   - 单测：输入/输出分别计价、缓存命中价、币种、未知模型返回 `unpriced`、价格版本快照。
   - 单测：中英混合、代码、长上下文 token 计数；与 Provider 返回 usage 对照，误差超出阈值时标记 `estimate` 而不是假装精确。
   - 回归：同一请求在价格表更新前后重算，历史账本不得被改写（价格版本随请求落库）。

**落地方式**：
- 新增 `src/cost/usage_ledger.py`（SQLite，独立库或独立表）：`request_id, tenant_id, user_id, provider, model, prompt_tokens, completion_tokens, cached_tokens, usage_source, cost, currency, price_version, latency_ms, ttft_ms, status, correlation_id, created_at`。
- 在 `provider_for_task()` 和 `get_provider()` 两个入口统一包一层 `MeteredProvider`；流式响应从最后一个 chunk 取 usage（Ollama 的 `prompt_eval_count/eval_count`；OpenAI-compatible 的 `usage`，需要 `stream_options.include_usage`）。
- 业务调用点零改动；用 `contextvars` 传 `request_id/tenant/user`，避免每个函数加参数。
- 预算复用 `src/cost/cost_tracker.py` 的预算/告警模型，补 SQLite 持久化；或复用 `src/kernels/resource` 的 reserve/commit 语义，但必须落库。
- 预扣用“prompt token + max_output”做上界，完成后按真实 usage 结算差额；差额为正/负都写一条 `adjustment`，不要静默吞掉。
- UI 显示三层：本次请求（tokens/费用/延迟/首字）、今日/本月累计、预算进度与告警；未知价格显示“未定价”，不要显示 0。

**不做什么**：不引入 LiteLLM 作为运行时网关（理由见 4.1）；不把价格表复制进仓库；不把“估算”标成“精确”。

---

### 3.4 DBOS Transact Python（长任务耐久与补偿）

**仓库字段**：`dbos-inc/dbos-transact-py`；1,666 star；最近提交 2026-11-04；Python + SQLite/Postgres；MIT；活跃，9 open issues；PyPI OSV 0 条；核心目录 `dbos/_core.py`、`dbos/_recovery.py`、`dbos/_queue.py`、`dbos/_sys_db_sqlite.py`；有 `tests/`。

**为什么值得**：鎏灏已经有 `src/kernels/execution/_journal.py` 做目标级恢复，但明确不做副作用去重，也没有通用后台队列。DBOS 用“工作流 + 步骤”的持久化执行模型，天然提供：步骤结果落库、崩溃恢复、幂等重放、队列、定时器。对“长任务跑到一半进程挂了”和“自动化动作做错了能不能补偿”都是直接能力。

**九问**：

1. **已有同类**：`src/kernels/execution/_journal.py`（append-only SQLite 日志，已接 `src/gateway/ai_management.py:202-229`）；`src/workflow/checkpoint.py`（SQLite CheckpointManager，零引用）；`src/resilience/`（重试/熔断，零引用）。
2. **当前不足**：journal 只跳过已完成任务，不做副作用幂等；`create_checkpoint()` 是内存 dict；没有队列/worker；API 进程崩溃后没有后台重跑；没有补偿事务。
3. **候选解决**：持久化工作流/步骤、自动恢复未完成步骤、步骤级幂等、队列、定时任务；可在 SQLite 单机运行。
4. **值得借鉴模块**：`dbos/_core.py`（workflow/step 装饰器与上下文）、`dbos/_recovery.py`（启动恢复）、`dbos/_queue.py`（队列）、`dbos/_sys_db_sqlite.py`（SQLite 系统库）。
5. **能否独立封装**：能，但会改变执行编排方式；建议作为新执行引擎的 pilot，不直接替换现有 Goal/ExecutionEngine。
6. **会不会破坏接口**：如果直接替换，会破坏 Goal/Workflow 的现有持久化与调用约定；如果只包一层“长任务 worker”，接口可保持不变。必须先做双写/影子运行。
7. **许可证**：MIT；无 GPL/AGPL。
8. **安全与维护**：0 条公告；项目年轻（1.6k star）、9 open issues；SQLite 并发/锁与现有 WAL 需要验证；系统库表结构需要迁移管理。
9. **集成测试与回归测试**：
   - 崩溃注入：工作流执行到第 2 步时 kill 进程，重启后从第 2 步继续，第 1 步不重复产生副作用。
   - 幂等：同一 idempotency key 重放两次，外部副作用只发生一次。
   - 补偿：第 3 步失败时执行补偿函数，验证前 2 步被回滚/标记。
   - 并发：两个 worker 同时抢同一任务，只有一个执行。
   - 回归：与现有 journal 的目标恢复行为对照，确保不重复执行已完成任务。

**落地方式**：先做一个“长任务 pilot”（例如批量知识库导入/批量评测），用 DBOS SQLite 跑；不碰现有 `/v1/goals` 主链路。等 pilot 稳定后再评估是否替换 `src/kernels/execution`。

**不做什么**：不把 DBOS 引入 API 进程的每个请求；不把现有审计/配额表交给 DBOS 管理；不做“大爆炸式”替换。

---

### 3.5 Syncthing（无云多设备同步）

**仓库字段**：`syncthing/syncthing`；90,127 star；最近提交 2026-11-02；Go；MPL-2.0；活跃，398 open issues；Go 模块 OSV 4 条（2017–2022，均已修复）；核心目录 `lib/model/`、`lib/versioner/`、`lib/sync/`、`cmd/`；有内联 Go 测试。

**为什么值得**：不引入云服务的前提下，Syncthing 是最成熟的 P2P 文件同步方案：设备 ID 互信、TLS、局域网直连、可选中继、文件版本恢复（trashcan/simple/staggered）。它解决的是“快照文件/导出文件”的同步，不是“活数据库”的同步。

**九问**：

1. **已有同类**：没有同步；只有 `apps/console/console/public/sw.js` 的 PWA 外壳缓存与手机 HTTPS 访问。
2. **当前不足**：手机只能访问同一台机器；没有设备配对、增量同步、冲突处理、版本恢复。
3. **候选解决**：电脑↔手机的文件级持续同步；版本器提供“误删/误改”恢复；局域网直连不依赖公有云。
4. **值得借鉴模块**：`lib/model/`（文件夹/索引/冲突）、`lib/versioner/`（trashcan/staggered/simple 版本恢复）、`lib/sync/`（块同步）、`cmd/`（守护进程与配置）。
5. **能否独立封装**：能，作为外部守护进程；应用只负责导出/导入“同步快照包”。不改业务代码。
6. **会不会破坏接口**：不破坏；但**绝不能把活 SQLite/WAL 文件放进同步目录**，否则会损坏数据库。必须同步导出快照（例如每日 `VACUUM INTO` + 校验和）。
7. **许可证**：MPL-2.0，文件级弱 copyleft。使用未修改二进制通常可行，但分发时需提供 MPL 覆盖文件的源码获取方式；修改 MPL 文件后必须公开修改。无 GPL/AGPL。商业闭源产品集成前建议法务确认。
8. **安全与维护**：4 条历史公告均已修复；守护进程有网络攻击面，需绑定局域网、设备 ID 白名单、禁用公网中继或显式配置；MPL 合规成本低但非零。
9. **集成测试与回归测试**：
   - 两个临时目录模拟两台设备：新增/修改/删除→同步→内容一致。
   - 双方同时改同一文件→产生冲突副本，不静默覆盖。
   - 版本器：删除文件→从 `.stversions` 恢复→哈希一致。
   - 安全：活 DB 文件放入同步目录的用例必须被应用层拒绝（写一条显式测试，防止未来有人误配）。
   - 手机侧只做手工验收（Android/iOS 客户端），不进 CI。

**落地方式**：
- 应用新增“导出同步快照”命令：SQLite `VACUUM INTO` 到临时文件→计算 SHA-256→打包 manifest（版本、设备、时间、哈希）→放到同步目录。
- 手机端只读打开快照；写入仍走电脑端（避免多主写冲突）。
- 第二阶段再评估 cr-sqlite 做行级双向同步。

**不做什么**：不同步 `*.db-wal`/`*.db-shm`；不在同步目录里直接打开数据库；不把审计哈希链做成多主写入。

---

## 3.6 本路五个核心问题

### 3.6.1 模型评测从零到一：最小可行框架

**目标问题**：把 `deepseek-chat` 换成另一个模型后，对话质量、工具调用成功率、知识库回答准确率各掉了多少？

**最小闭环（不引入框架也能做）**：

1. **固定数据集**：三套，每套先 30–50 条，按实际方差再扩。
   - `chat_quality`：真实场景问题 + 期望要点/禁用点 + 语气要求。
   - `tool_calling`：给定工具清单与用户意图，期望工具名、参数、是否应拒绝、是否应追问。
   - `kb_qa`：固定文档快照 + 固定问题 + 参考答案 + 必须引用的 chunk id。
2. **固定变量**：同一 prompt 版本、同一工具 schema、同一 `temperature=0`（若供应商不支持，记录实际值）、同一 `max_tokens`、同一检索结果快照。不要在一次对比里同时换模型和换 prompt。
3. **三类指标**：
   - **规则类（硬指标）**：工具名准确率、参数 JSON schema 合法率、参数完全匹配率、漏调用率、多余调用率、任务完成率、JSON 解析失败率、拒答正确率。
   - **语义类（软指标）**：与参考答案的 embedding 相似度；LLM-as-judge 按 rubric 打分（正确性/完整性/是否有害/是否引用依据）；pairwise 与 baseline 对比（交换顺序消除位置偏差）。
   - **RAG 类**：retrieval hit@k、context precision/recall、faithfulness（回答是否只依据给定 context）、answer correctness、citation 正确率、该拒答时是否拒答。
4. **基线**：每个 `model + prompt_version + dataset_version + runner_version` 组合冻结一份 baseline；baseline 必须人工抽检 10% 后锁定。
5. **差异报告**：输出 `Δ = candidate - baseline`，按套件与指标分开；不要只报一个总分。报告必须写清楚样本量、失败用例 id、失败原因分类（工具选错/参数错/格式错/知识错/拒答错/超时）。
6. **门禁**：先“记录”2–3 周，再按实际方差设阈值。初始建议只看“明显退化”：工具任务完成率、KB 答案正确率、chat pairwise 胜率；任何一项出现方向性大幅下降就阻断切换。阈值必须人工拍板，不在本报告里编造数字。
7. **统计**：同一用例重复 3 次（温度>0 时），报告均值与置信区间；样本量按预期最小退化幅度做功效计算（本报告未做功效计算）。
8. **落库**：独立 SQLite（`data/evaluation.db`）或 JSON；表：`eval_runs`、`eval_cases`、`eval_results`、`eval_baselines`。不要写业务库。
9. **实现落点**：
   - 复用 `src/evaluation/benchmark_runner.py` 的 runner/dataset 结构；
   - 新增 `src/evaluation/model_regression.py`：通过 `provider_for_task()` 调真实 Provider；
   - 新增 `scripts/eval_model_swap.py`：`--baseline-model`、`--candidate-model`、`--suite`、`--output`；
   - 工具调用用例用假工具记录调用序列，绝不执行真实副作用；
   - promptfoo/DeepEval 只作为外部 runner 或 CI 依赖，不接生产。
10. **最小验收**：能回答“三个套件各自的 Δ 是多少、哪些用例失败、失败原因是什么”；不能回答“模型综合能力排名”。

**为什么现在完全没有**：`src/evaluation` 是通用指标库 + runner，没有 provider 适配；`src/cli/benchmark_commands.py` 是假数据；`src/ai/agent_runtime.py` 的 evaluator 评的是任务成功，不是模型质量。

---

### 3.6.2 成本计量：不侵入业务代码的请求级账本

**核心原则**：业务代码只调用 provider；计量发生在 provider 包装层，不在每个业务函数里埋点。

**最小设计**：

1. **单点包装**：在 `src/ai/model_router.py` 的 `provider_for_task()` / `get_provider()` 返回前包一层 `MeteredProvider`，实现与 `BaseProvider` 相同的方法签名。业务调用点零改动。
2. **上下文**：用 `contextvars` 传 `request_id`、`tenant_id`、`user_id`、`correlation_id`、`task_type`。没有上下文的调用标记 `unattributed`，不要伪造用户。
3. **usage 采集**：
   - OpenAI-compatible：读响应 `usage`；流式需要 `stream_options.include_usage=true`，从最后一个 chunk 取。
   - Ollama：读 `prompt_eval_count`、`eval_count`；流式从最终 chunk 取。
   - 如果 provider 不返回 usage：用 tokenizer 计数，`usage_source=tokenizer`；再不行用估算，`usage_source=estimate`。
4. **价格**：`genai-prices` 或本地覆盖表；记录 `price_version`、`currency`、`input_price`、`output_price`、`cached_price`。未知模型 = `unpriced`，不是 0；不要用一个 blended 价同时乘输入输出。
5. **账本**：SQLite 表 `llm_usage`（字段见 3.3），每次请求一行；失败请求也写（`status=error`，tokens 尽可能记录）。按日/月/用户/模型聚合用视图或定时汇总，不在内存里累加。
6. **预算**：
   - 复用 `config/default.yaml` 的 `cost.*` 段（当前是死配置）或 `src/cost/cost_tracker.py` 的预算模型；
   - 预检：估算 prompt tokens + `max_output` 做上界；超预算直接 429/402，返回明确文案；
   - 预扣：reserve；完成后按真实 usage commit，差额写 adjustment；
   - 作用域：全局/租户/用户/模型/任务类型；日/月/一次性；
   - 告警：50%/80%/100%；100% 后硬阻断或降级到本地模型（由配置决定）；
   - 重启持久化：预算用量从账本汇总，不依赖内存 dict。
7. **UI**：本次请求、今日/本月、预算进度、未知价格标记、失败请求也计入；不要显示“0 元”当未知。
8. **回归测试**：单测覆盖输入/输出分开计价、缓存价、未知模型、币种、价格版本、预扣/结算差额、并发写入、重启后配额恢复；集成测试用假 provider 注入固定 usage，断言账本行与聚合值。

**现状差距（必须承认）**：`src/ai/liuhao.py` 目前按 `chars/4 + max_output` 估算并预扣，不是真实 usage；`src/kernels/resource` 配额是内存 dict；`src/cost/cost_tracker.py` 与 `src/cost/cost_api.py` 未接线；`config/default.yaml` 的 cost 段是死配置。详见 `docs/QUOTA-ACCOUNTING-DESIGN.md`。

---

### 3.6.3 可撤销：三类操作的技术边界

| 操作类型 | 能做到什么 | 技术手段 | 做不到的边界 | 建议等级 |
|---|---|---|---|---|
| 自动化动作：文件写/删 | 完整撤销（恢复到操作前内容） | 写前快照（内容寻址/trash）+ 原子替换；记录 pre-image hash | 外部已消费的副本、用户已下载的文件、已被其他进程覆盖的内容 | L3 可做 |
| 自动化动作：数据库写 | 事务内可回滚；已提交可补偿 | 事务 + 软删除 + 审计；补偿事务/反向事件 | 已提交且无逆操作的业务语义（例如已扣款、已发货） | L2 可做 |
| 自动化动作：外部工具/HTTP | 只能补偿或审批前置 | 幂等键 + 审批 gate + 补偿调用 + 人工确认 | 发邮件、付款、删除云资源、第三方已执行的动作；无法真正撤回 | L1/L2 |
| 模型调用 | 不能撤销调用与费用 | 丢弃输出、回滚下游、恢复模型配置、保留审计 | 供应商已计费；已返回给用户的内容；已触发的工具副作用 | L0/L1 |
| 代码自改 | 可回滚（如果先快照） | git 分支/快照提交 + patch + revert；构建产物回滚 | 已部署到外部环境、已发送的版本、已执行的数据迁移 | L3（本仓库目前没有真实自改） |
| 审计日志 | 不可删除，只能追加更正 | 哈希链 + 追加纠正事件 | 不能“撤销”审计本身；否则失去可追溯性 | 必须 |

**鎏灏现状**：
- `src/model_registry_mlops.py` 有模型版本 promote/rollback；这是配置级撤销。
- `src/ai/evolution.py` 只做状态流转，没有真实代码自改。
- `src/kernels/execution/__init__.py:1633-1647` 的 `create_checkpoint()` 是内存 dict，没有消费方，不能当撤销能力。
- 没有文件写前快照、没有 DB 补偿、没有工具逆操作。

**最小落地顺序**：
1. **L0 审计**：所有自动化动作先写审计事件（动作、参数、目标、结果、关联 id）。
2. **L1 回收站/软删除**：文件删除改移动；DB 删除改 `deleted_at`；UI 提供“恢复”。
3. **L2 补偿**：为每个有外部副作用的工具定义 `compensate()`；失败时按逆序补偿；没有补偿函数的工具必须审批。
4. **L3 快照**：文件写前 copy-on-write/内容寻址；代码自改前 git snapshot；提供“回滚到某个快照”。
5. **L4 外部 saga**：只有跨系统、长事务才需要（DBOS/Temporal）；不要为了“撤销”先引入。

**关键边界**：不要向用户承诺“模型调用可以撤销”。正确表述是“本次回答已丢弃/已回滚下游副作用/已恢复模型配置，费用已发生且审计保留”。

---

### 3.6.4 多设备同步：不引入云的可行方案对比

| 方案 | 同步粒度 | 是否持续 | 依赖 | 代价 | 风险 | 结论 |
|---|---|---|---|---|---|---|
| 应用内导出/导入 + 局域网 QR 一次性配对 | 整库快照/领域包 | 手动 | 现有 FastAPI + 前端 QR | 最低；无需新依赖 | 快照期间数据不新；配对 token 必须一次性、短时效 | 第一阶段推荐 |
| Syncthing | 文件级 | 持续 | 外部守护进程 | 中；安装/防火墙/设备 ID | 活 SQLite/WAL 不能同步；冲突副本；MPL 合规 | 第二阶段推荐（只同步快照包） |
| cr-sqlite | 行级 CRDT | 持续 | SQLite 扩展 + schema 改造 | 高；表结构 CRR 化、冲突语义 | 最新 release 停在 2024-01；审计/账本不适合 CRDT | 观察，不现在做 |
| croc | 文件级一次性 | 手动 | 外部二进制 | 低 | 2026 年 4 条接收端公告：任意文件覆盖/删除、`.ssh` 绕过、RCE 链 | 不建议用于接收 |
| Headscale/Tailscale | 网络层 | 持续 | 自托管协调服务器 | 中高；设备/ACL/密钥 | 协调服务器成为入口；不解决数据冲突 | 可选“远程访问”，不是“同步” |
| rclone | 文件级 | 持续/定时 | 外部二进制 + 远端 | 中 | 58 条 Go 模块公告，含 2026 未授权 RCE/路径逃逸 | 不建议作为本产品同步层 |
| 直接同步 SQLite 文件 | 库文件 | 持续 | 文件同步工具 | 低（看起来） | WAL/SHM 不一致、写冲突、库损坏 | 禁止 |

**推荐路径**：
1. **Phase 1（最低风险）**：电脑端导出“领域快照包”（SQLite `VACUUM INTO` + manifest + SHA-256 + 版本），手机端只读打开；通过局域网 HTTPS + 一次性 QR token 配对。写入仍只在电脑端。
2. **Phase 2**：Syncthing 同步快照包，启用 trashcan/staggered 版本恢复；监控冲突副本。
3. **Phase 3（可选）**：只对“设置/草稿/会话摘要”等低冲突表做 cr-sqlite 双向同步；审计、配额、账本保持单写。

**安全要求**：设备配对用一次性短时效 token；设备注册表支持吊销；传输必须 TLS；快照包签名/哈希校验；不要在同步目录放密钥、`.env`、API Key、审计库；手机丢失可远程吊销。

---

### 3.6.5 离线降级：能力矩阵与 UX

**能力矩阵**：

| 能力 | 断网时 | 说明 |
|---|---|---|
| 已加载的本地模型对话 | 可用 | Ollama/llama.cpp 本地推理；若模型未加载则明确提示“本地模型未就绪” |
| 历史会话只读 | 可用（需本地缓存） | 只能读已缓存/本地库；不要缓存鉴权响应 |
| 草稿/未发送消息 | 可用 | IndexedDB/local storage；登出清除 |
| 本地知识库检索 | 可用 | 向量库在本地；Embedding 若需云则禁用 |
| 知识库导入/Embedding | 视配置 | 本地 Embedding 可用；云 Embedding 明确禁用 |
| 云端 LLM | 禁用 | 显示“离线，云端模型不可用”，不转 mock、不无限转圈 |
| 云 Embedding/Rerank | 禁用 | 同上 |
| Web 搜索/联网工具 | 禁用 | 明确列出被禁用的工具 |
| 多设备同步 | 暂停 | 显示上次同步时间；恢复后重试 |
| 审计/本地写入 | 可用 | 本地队列，恢复后提交 |
| 计费/配额 | 本地估算 | 标记 `usage_source=estimate`，联网后不篡改历史 |

**检测**：不要只用 `navigator.onLine`。用三态健康检查：
1. 本地网关可达 + 互联网可达 = `online`
2. 本地网关可达 + 互联网不可达 = `local-only`
3. 本地网关不可达 = `offline`

`/v1/health` 或新增 `/v1/capabilities` 返回各依赖状态（db、ollama、provider、internet）。前端按能力渲染，而不是按一个布尔值。

**UX**：
- 顶部持久横幅：“离线模式：云端模型/联网工具不可用；本地模型与历史可用”。
- 每个禁用按钮 hover/点击给出具体原因与恢复方式，不要只灰掉。
- 待发送操作显示“已排队，联网后发送”，并给出取消入口。
- 不要用“AI 正在思考”动画掩盖离线失败。

**PWA/缓存边界**：
- 静态资源 CacheFirst；只读 GET 可 StaleWhileRevalidate，但必须按用户/租户隔离，登出清空。
- 绝不缓存 `/v1/auth`、`/v1/chat` POST、SSE、包含 token 的响应。
- 待发送队列必须有 idempotency key；恢复后重放，冲突时让用户确认。
- 现有 `public/sw.js` 明确不缓存 API，这是安全基线，不要为了“离线可用”随意放开。

**工作流**：断网时允许创建 Goal 但标记 `queued_offline`；恢复后由用户确认再执行，避免自动重放高副作用动作。

---

### 3.7 补充：可靠性工程的落地顺序

1. **接线现有 `src/resilience/`**：Provider 调用外层套 `Retry(jitter=True)` + `CircuitBreaker` + `Bulkhead`；不要新增 pybreaker/stamina/tenacity 作为全局替换。
2. **deadline**：每个 Provider 请求设置总超时（连接+首包+总时长），流式要有首包超时；超时归类可重试/不可重试。
3. **幂等**：为工具调用和外部副作用引入 `idempotency_key`；账本/审计记录 key；重复请求返回同一结果。
4. **长任务**：短期用 `src/kernels/execution/_journal.py` 的目标恢复；中期评估 DBOS pilot；`src/workflow/checkpoint.py` 的 SQLite CheckpointManager 要么接线，要么明确标注未使用。
5. **限流**：`src/gateway/rate_limiter.py` 目前是进程内；多进程/多设备场景需要持久化或外部存储；至少把“每用户/每模型”的配额与成本预算统一。
6. **可观测**：Provider 指标（请求数、错误率、TTFT、tokens、成本）必须接主链路；现有 `src/observability/metrics.py` 的 provider 指标上下文管理器未见生产调用。
7. **回归**：故障注入测试（超时、503、429、断流、进程 kill、DB 锁）必须进 CI；不能用 mock 只测 happy path。

---

## 4. 明确不建议引入的，及原因

### 4.1 LiteLLM（仅借鉴价格/预算模式，不作为运行时网关）

**仓库字段**：`BerriAI/litellm`；62,778 star；最近提交 2026-11-05；Python/FastAPI；根 LICENSE 为“enterprise/ 之外 MIT，enterprise/ 另行商业授权”；5,732 open issues；PyPI OSV 85 条（2026 年 71 条，含恶意 PyPI 版本/供应链事件）；核心目录 `litellm/router.py`、`litellm/router_strategy/`、`litellm/litellm_core_utils/token_counter.py`、`model_prices_and_context_window.json`（约 3.4MB）。

**不建议原因**：
1. **安全公告量过大**：85 条 OSV，含 2026 年恶意包/供应链事件；作为运行时依赖会把攻击面直接带进单机产品。
2. **与现有架构重复**：`src/model_gateway/` + `src/ai/model_router.py` 已做路由；`src/ai/economy.py` 已做预算；再叠一个网关会增加两套路由/两套计费。
3. **体量与部署复杂度**：作为 proxy 需要额外进程/配置；单机产品不需要。
4. **可借鉴的部分很小**：价格 schema、token_counter、预算 limiter 的思路；这些可以用 `genai-prices` + 自写账本替代。

**九问速答**：
- Q1 已有：`src/model_gateway/`、`src/ai/model_router.py`、`src/ai/economy.py`。
- Q2 不足：现有价格是单费率、无账本；LiteLLM 也不能替我们解决“真实 usage 落库”。
- Q3 解决：统一 provider、价格表、预算/限流、fallback；但大部分与现有重复。
- Q4 借鉴：`router_strategy/budget_limiter.py`、`token_counter.py`、价格表 schema。
- Q5 独立封装：价格/计数可抽出；网关不建议。
- Q6 破坏接口：作为网关会改变调用链/部署；作为库会引入大量依赖。
- Q7 许可证：MIT（enterprise 除外）；可用但需隔离 enterprise 目录。
- Q8 安全/维护：85 条公告、5,732 open issues；维护成本高。
- Q9 测试：若只借鉴价格，写本地价格表单测；不引入 LiteLLM 运行时。

### 4.2 Arize Phoenix（ELv2，不建议嵌入产品）

**仓库字段**：`Arize-ai/phoenix`；12,164 star；最近提交 2026-11-05；Python/React；根 LICENSE 为 Elastic License 2.0（source-available，非 OSI）；1,223 open issues；OSV 0 条；核心目录 `src/phoenix/trace/`、`src/phoenix/evals/`、`src/phoenix/server/`。

**不建议原因**：ELv2 不是 OSI 开源许可证，禁止把软件作为托管/管理服务提供给他人，且不能移除许可证声明；对要分发/商业化的单机产品，法律边界不如 MIT/Apache 清晰。功能上它与 OTel + 自建 trace 表重叠，且体量大。

**九问速答**：Q1 已有 `src/observability/tracing.py` + 审计；Q2 不足是没有 trace UI/决策链；Q3 提供 trace/eval UI；Q4 可借鉴 `trace/` 的数据模型；Q5 可独立部署但重；Q6 不破坏（旁路）；Q7 ELv2 限制托管服务；Q8 体量大、维护成本高；Q9 若只借鉴，写自己的 span 表与 UI 快照测试。

### 4.3 Langfuse（MIT 核心 + EE，重，不建议单机引入）

**仓库字段**：`langfuse/langfuse`；37,061 star；最近提交 2026-11-05；TypeScript/Next.js + worker；根 LICENSE 为 MIT（`ee/` 企业功能另行授权）；1,122 open issues；仓库 security-advisories 4 条（2025-11 至 2026-04，含成员越权读取 LLM API Key、Slack OAuth 未授权关联、SSO 账号接管、跨组织枚举）；核心目录 `web/`、`worker/`、`packages/shared/`。

**不建议原因**：部署组件多（web、worker、Postgres、ClickHouse、Redis），与单机产品定位不符；4 条近期权限/账号接管公告；EE 目录许可证需隔离。可借鉴 trace 数据模型，但不引入运行时。

### 4.4 GPTCache（语义缓存，正确性风险）

**仓库字段**：`zilliztech/GPTCache`；8,219 star；最近提交 2026-09-22；Python；MIT；101 open issues；PyPI OSV 2 条（2026，文件/图片缓存键碰撞导致错误命中）；核心目录 `gptcache/`（cache/similarity/storage/embedding）。

**不建议原因**：语义缓存一旦误命中，会返回错误答案且用户无感；2026 年公告正是缓存键碰撞。对知识库/工具调用场景，缓存 key 必须包含 tenant、user、model、prompt hash、检索 chunk hash、工具 schema version，复杂度高；收益不如先把 prompt cache（供应商侧）和精确缓存做好。

### 4.5 croc（QR/PAKE 传输，但 2026 接收端漏洞）

**仓库字段**：`schollz/croc`；40,868 star；最近提交 2026-11-04；Go；MIT；1 open issue；仓库 security-advisories 4 条（2026-08/09）：接收端任意文件覆盖/删除、`.ssh` 大小写绕过、`croc-stdin-` 覆盖确认绕过、符号链接覆盖任意文件（可链式 RCE）；核心目录 `src/`（croc/tcp/relay）。

**不建议原因**：如果用于手机↔电脑接收文件，正好落在公告攻击面；单机产品不应该把“接收任意文件”作为默认能力。若必须用，必须固定补丁版本、禁用 `--stdin`、限制接收目录、拒绝符号链接、接收前人工确认。

### 4.6 Dulwich（Git 纯 Python 实现，21 条公告）

**仓库字段**：`jelmer/dulwich`；2,295 star；最近提交 2026-11-04；Python；LICENSE 为 Apache-2.0 OR GPL-2.0（双许可，需明确选 Apache）；57 open issues；PyPI OSV 21 条，含 2022 任意代码执行、2026-05 NTFS 路径任意文件写、2026-06 patch 主题未净化；核心目录 `dulwich/porcelain.py`、`repo.py`、`object_store.py`。

**不建议原因**：代码快照/回滚直接用系统 `git` CLI 更安全（成熟、审计充分），不需要为了“纯 Python”引入 21 条公告的库。若便携环境没有 git，再评估固定版本的 dulwich，并强制 Apache 许可选项。

### 4.7 rclone（58 条 Go 公告，且不是 P2P）

**仓库字段**：`rclone/rclone`；60,822 star；最近提交 2026-11-05；Go；MIT；1,279 open issues；Go 模块 OSV 58 条，含 2026 未授权 `options/set` 认证绕过导致命令执行、PowerShell 文件名注入 SFTP 服务端命令执行、路径逃逸、serve restic 根目录逃逸；核心目录 `backend/`、`fs/`、`cmd/serve/`。

**不建议原因**：它不是电脑↔手机 P2P 同步，而是“云/远端存储同步”；作为常驻/可暴露服务时攻击面大。若未来要接 WebDAV/SFTP，必须限定 localhost、只读、固定版本、最小权限。

### 4.8 lm-evaluation-harness（基础模型基准，不是产品回归）

**仓库字段**：`EleutherAI/lm-evaluation-harness`；14,408 star；最近提交 2026-09-14；Python；MIT；1,298 open issues；OSV 0 条；核心目录 `lm_eval/evaluator.py`、`models/`、`tasks/`。

**不建议原因**：它回答“模型 MMLU/HellaSwag 多少分”，不回答“鎏灏的工具调用/知识库回答有没有变差”。可以偶尔跑基础能力参考，但不能作为产品回归门禁。

### 4.9 Temporal Python SDK（能力强但单机过重）

**仓库字段**：`temporalio/sdk-python`；1,246 star；最近提交 2026-11-05；Python；MIT；46 open issues；OSV 0 条；核心目录 `temporalio/worker/`、`client/`、`workflow/`。

**不建议原因**：需要 Temporal Server/数据库/集群，单机产品部署过重；DBOS 用 SQLite 就能覆盖“长任务 + 恢复 + 幂等 + 补偿”的 80%。除非未来要多机分布式 worker，否则不引入。

### 4.10 pybreaker / stamina（与现有 `src/resilience` 重复）

**不建议原因**：`src/resilience/` 已实现 `CircuitBreaker`、`Retry`（含 jitter）、`Bulkhead`、`RateLimiter`、`FallbackHandler`，只是没接线。再引入 pybreaker/stamina 会产生两套熔断/重试语义。先接线现有模块，再按缺口补。

---

## 5. 未验证项清单

以下项目**没有**被本次调研验证，不能当作结论使用：

1. **未运行任何候选的测试/基准**：star、提交、许可证、公告是事实；性能、吞吐、TTFT、量化后质量均为未获取。
2. **未用真实 LLM 跑评测**：本报告没有 baseline/candidate 的任何分数；第 3.6.1 节只给框架，不给数字。
3. **未验证 Provider 真实 usage 字段**：Ollama/OpenAI-compatible 的 `usage`、流式最后 chunk 行为需按实际版本实测；当前 `src/ai/providers.py` 未见采集实现。
4. **未验证 tokenizer 对 DeepSeek/Qwen 的误差**：没有本地 tokenizer 文件；`chars/4` 的具体低估倍率未测量。
5. **未验证 genai-prices 对国产模型的价格覆盖**：价格表覆盖范围未逐模型核对。
6. **未验证 DBOS 与现有 SQLite/WAL/并发模型的兼容性**：需要 pilot 崩溃注入测试。
7. **未验证 Syncthing 在 Windows/Android/iOS 的局域网发现、防火墙、版本恢复差异**：只做了源码与公告评估。
8. **未验证 cr-sqlite 与 Python `sqlite3`/现有 schema 的兼容性**：最新 release 停在 2024-01，需实测。
9. **未验证 Workbox 与当前前端构建/Vite/自定义 SW 的兼容性**：需要构建与离线 E2E。
10. **未验证 DeepEval 对当前 OpenAI-compatible 网关的适配**：自定义 provider/judge 需实测。
11. **未逐条审计候选依赖树**：公告数是 OSV/GitHub API 快照，不等于完整 SBOM 审计。
12. **未获取测试覆盖率**：所有候选只确认存在 tests/ 或内联测试，没有覆盖率数字。
13. **未获取法律意见**：MPL-2.0（Syncthing）、ELv2（Phoenix）、双许可（Dulwich）、MIT+EE（Langfuse/LiteLLM）需法务确认后再分发。
14. **未验证手机端实际体验**：二维码配对、离线缓存、通知、后台进程限制均未在真机测试。
15. **未验证“撤销”在外部副作用上的实际补偿语义**：需要按工具逐个定义补偿函数，本报告只给边界。
16. **未验证价格/预算的并发一致性**：多请求并发 reserve/commit 需要 SQLite 事务与锁测试。
17. **未验证 `src/resilience` 接线后的行为**：熔断阈值、半开、舱壁并发数需按真实 provider 错误率调参。
18. **未验证 `src/workflow/checkpoint.py` 是否可安全接入**：当前零引用，接入前需确认 schema/并发/清理策略。

---

## 6. 最小落地顺序（建议）

不计入“引入候选”，只作为工程顺序：

1. **第 1 周**：接真实 usage + SQLite 账本 + 预算持久化（不改业务调用点）。
2. **第 2 周**：建 3 套评测数据集 + baseline，先“记录”不“阻断”；promptfoo/DeepEval 只做 dev/CI。
3. **第 3 周**：接线 `src/resilience`（Retry/CircuitBreaker/Bulkhead）+ 首字/总时延指标。
4. **第 4 周**：文件写前快照/回收站 + 审计补偿字段；先做 L1，不承诺 L4。
5. **第 5 周**：离线三态 + 能力矩阵 + 明确禁用；不放开 API 缓存。
6. **第 6 周**：导出快照包 + 局域网 QR 一次性配对；Syncthing 只同步快照包。
7. **之后**：DBOS 长任务 pilot；cr-sqlite/llama.cpp 量化只在有明确收益时评估。

---

*本报告只写入 `docs/oss-radar/quality-cost.md`，未修改其他文件，未提交 Git，未运行测试或服务。*
