# 开源技术雷达 · 第 6 路：工程质量、成本与观测

> 范围：监控 / 日志 / 告警 / 可观测性 / 分布式追踪；CI·CD 与运维；自动化测试；模型评测与回归；推理性能；成本与配额计量；搜索与文档解析；工具链；离线与弱网降级。
>
> 调研方法：先读鎏灏现有代码建立能力清单 → 在 GitHub / OSV 检索 → 对每个候选核对 **LICENSE 文件原文**、**仓库真实目录树**、**最近更新时间与 star**、**公开安全公告**。
>
> 核实时间：2026-11-05（本地时区 Asia/Shanghai）。star 数、更新时间来自 `ungh.cc` 代理读取的 GitHub 元数据；许可证逐仓库抓取 LICENSE 文件首段确认；安全公告来自 `api.osv.dev`。
>
> 本轮边界：只读代码 + 只读外部仓库；**未运行任何测试、未启动任何服务、未 clone 第三方仓库、未修改其他文件、未提交 git**。

---

## 0. 结论摘要

1. **已有能力不弱，问题在“接线”而不是“缺失”**：structlog、trace_id、Prometheus 指标、告警规则/存储/投递、就绪探针、OTel tracer、Langfuse/Phoenix 适配器都已写好，但日志配置函数 `configure_logging()` 全仓无调用点、观测适配器未进主链路、成本表 `CostTracking` 已建表却无写入方。**先把死配置接上，比引入新框架收益高一个数量级。**
2. **模型评测是“有零件、无闭环”**：指标库、数据集、基线结构、结果评估内核都有，但 `BenchmarkRunner` 全部状态在内存字典里（`_datasets/_results/_baselines`），没有真实 Provider 执行器、没有基线持久化、没有 CI 阈值。从 0 到 1 的最小方案是 **DeepEval（Apache-2.0）做断言与报告 + 现有指标库做领域打分 + SQLite 存基线 + pytest 标记做夜间门禁**。
3. **成本计量的最短路径在 Provider 边界**：当前四套成本代码并存（`ai/economy.py`、`model_gateway/cost_optimizer.py`、`cost/cost_manager.py`、`sre/cost/cost_manager.py`），且 `BillingEngine` 聚合在 `self._usage` 字典里，重启即失。推荐 **genai-prices（MIT）只做“价格表 + 计价规则”**，在 `Provider.generate_with_retry` 出口统一采集 `usage`，异步落 `cost_tracking` 表——业务代码零改动。
4. **单机桌面产品不需要 10 容器观测栈**：`docker-compose.observability.yml` 已有 Prometheus + Alertmanager + Grafana + OTEL Collector + Tempo + Loki + Promtail + node-exporter + cAdvisor + Phoenix。建议保留为“可选高级栈”，默认交付改为 **文件日志 + 本地指标端点 + 应用内诊断页 + 一键诊断包**。
5. **安全/许可红线**：Loki 与 Renovate 是 **AGPL-3.0**（不打包进桌面发行物）；Phoenix 是 **Elastic License 2.0**（非 OSI，不可作为托管服务对外）；LiteLLM 有 **85 条 OSV 记录**（含 2026 年已修的 SSRF）；`uv` 有 **11 条**（含 Windows wheel 解压路径穿越，fixed 0.12.18）；`vcrpy` 有 **1 条任意代码执行**（fixed 8.2.1）。

---

## 1. 该域能力现状（附文件路径）

图例：🟢 已生效（代码路径可达）｜🟡 已实现但未接线/仅配置｜🔴 缺失｜⚫ 存在但过度

### 1.1 可观测性

| 能力 | 实现位置 | 状态 | 核实说明 |
|---|---|---|---|
| 结构化日志（structlog + JSON） | `src/observability/logging_utils.py`（`configure_logging` L80） | 🟡 | 函数完整（dictConfig L142、RotatingFileHandler L117、10MB/10 份 L121-122），但无任何调用点（`orphan-registry.yaml:78-83` 亦登记为 unused） |
| 第二套日志配置 | `src/logging_config.py`（`setup_logging` L170） | 🟡 | 同样零调用点（`orphan-registry.yaml:70-76`）；且使用**裸 `FileHandler`**（L44-60，`app.log`/`error.log`/`operations.log`），一旦接线即为无轮转文件 |
| 日志轮转配置 | `configs/observability/logging.yml` | 🟡 | 配置文件存在但未被 `uvicorn --log-config` 或代码加载；且 L27-28 把 structlog processor `add_correlation_id` 当 `logging.Filter` 使用，增强配置一旦加载会实例化失败 |
| trace_id / 请求关联 | `src/gateway/main.py`（`logging_middleware` L684） | 🟢 | 写入 `request.state.trace_id` 并回 `X-Trace-ID`；日志与告警共用 |
| Prometheus 指标导出 | `src/observability/metrics.py`；`src/gateway/observability.py`（L75 起） | 🟢 | `/v1/metrics/prometheus` 有令牌或“内网”限制 |
| 指标可信判定 | `src/gateway/observability.py`（`_is_internal_source` L53） | 🟢但判定有问题 | 以 `request.client.host` 是否 loopback/私网判定“可信”，详见 §1.2 |
| 告警规则 / 存储 / 投递 | `src/observability/alerts/{models,store,sinks,execution_binding}.py`；`src/observability/alerts/__init__.py:31` | 🟢 | `main.py:467` 启动时调用 `install_production_rules()`，注册 **6 条**系统健康规则；未配置 sink 时落本地告警库 |
| 告警查询 API | `src/gateway/observability.py`（告警读取入口） | 🟢 | 控制台可读 |
| 健康 / 就绪 / 子系统 | `src/gateway/health.py`；`src/observability/production.py` | 🟢 | `/ready` 做子系统检查；`/health` 不泄漏 |
| OTel 追踪 | `src/observability/tracing.py`；`src/adapters/observability/opentelemetry_adapter.py` | 🟡 | 可选启用，默认 exporter 指向 `http://localhost:4317`（`tracing.py:213/228`） |
| Langfuse / Phoenix 适配 | `src/adapters/observability/observability_adapter.py` | 🟡 | `export_to_langfuse` / `export_to_phoenix` 已实现，**主链路无调用**；两者已是运行期依赖 |
| 观测数据落盘（hash chain） | `src/observability/store.py`（默认 `data/observability/spans.json`） | 🟡 | JSON 整体重写（L109-112），无轮转、无容量上限 |
| 观测栈编排 | `docker-compose.observability.yml` | ⚫ | 10 个服务；对单机桌面过重 |
| 崩溃诊断包 | — | 🔴 | 无 `faulthandler`、无栈转储、无环境快照打包 |
| 日志磁盘上限 / 保留策略 | — | 🔴 | 无总量配额、无按日清理 |
| 应用内诊断页 | — | 🔴 | 控制台无“导出诊断”入口 |

### 1.2 已点缺陷的逐条核实

| # | 声称的缺陷 | 核实证据 | 定性 |
|---|---|---|---|
| D1 | 日志不轮转 | **两套日志配置模块都是死代码**：`src/observability/logging_utils.py:80` 的 `configure_logging()` 与 `src/logging_config.py:170` 的 `setup_logging()` 全仓无调用（`orphan-registry.yaml:70-83` 明确登记）；后者若接线，用的是裸 `logging.FileHandler`（L44-60），照样无轮转。当前日志实际只进 stdout；用户若重定向到文件，则无限增长 | **P0，用户可见**（长期运行撑爆磁盘） |
| D2 | `server.limits.*` 是死配置 | `config/default.yaml:61-64` 定义 `max_body_bytes` / `max_upload_bytes` / `rate_limit_per_minute`，**`src/`、`scripts/`、`tests/` 三处全无引用**；同类死配置还有 `timeouts.*`（L57-60）、`trusted_proxies`（L65）、`compression.*`（L66-69），压缩实际硬编码在 `src/gateway/main.py:679`（`minimum_size=1000`） | **P1**（配置误导运维，上传/请求体无上限） |
| D3 | metrics 用“来源是否私网”判可信 | `src/gateway/observability.py:53` 只看 `request.client.host`；反代（`config/nginx.conf` 的 `proxy_pass`）之后 `client.host` 恒为反代的私网地址，且 `trusted_proxies` 是死配置 → **只要未设 `LIUHAO_METRICS_TOKEN`，所有经反代的外部请求都被判为“内网可信”** | **P0，安全** |
| D4 | 限流只覆盖两条路径 | `src/gateway/main.py:638-643` 按配置前缀挂 `RateLimitMiddleware`；`config/default.yaml:56` 默认 `paths: ["/v1/chat", "/v1/goals"]`。登录、上传、知识库摄入、审计导出均无限流 | **P1** |
| D5 | 全量回归有 15 个失败 | 上下文记录为 `4685 passed / 15 failed（14 既存）`（2026-11-05 会话）。**本轮未复跑**，无法确认修复状态 | **待复核**，不计入结论 |

### 1.3 CI / 测试 / 评测 / 成本 / 解析 / 弱网

| 能力 | 实现位置 | 状态 | 说明 |
|---|---|---|---|
| CI 安全扫描 | `.github/workflows/ci.yml`（Trivy / Bandit / pip-audit） | 🟢 | 非阻断，不 fail build |
| 架构门禁（Semgrep） | `.github/workflows/ci.yml` | 🟢 | 零 findings 才通过 |
| 部署门禁变量核对 | `scripts/verify_deployment_gate_vars.py` | 🟢 | 行号锚定校验 |
| 观测闭环 E2E | `scripts/verify_observability_loop.py`；`.github/workflows/deployed-stack-verification.yml` | 🟢 | 起 Prometheus + Alertmanager + API 做真实事件闭环 |
| 指标落盘验证 | `scripts/verify_metrics_persist.py` | 🟢 | 落盘后重启仍可读 |
| Workflow 静态审计 | — | 🔴 | 无 actionlint / zizmor / act |
| Lint / 类型 / 覆盖率门禁 | `pyproject.toml`（`[tool.ruff]` L215、`[tool.uv]` L221-225 含 ruff/mypy/pytest-cov） | 🟡 | 工具已声明，**CI 中无 ruff/mypy/coverage 执行**（实测工作流无命中） |
| 评测指标库 | `src/evaluation/metrics.py` | 🟢 | 含 BLEU/ROUGE/pass@k/检索指标/安全指标 |
| 基准数据集与基线结构 | `src/evaluation/benchmark_runner.py`（`BenchmarkRunner` L224、`run_suite` L547、`compare_to_baseline` L351） | 🟡 | 全部状态存内存：`_datasets/_suites/_results/_baselines/_runs`（L245-249），`results_dir` 只用来 `mkdir`（L253-254） |
| 结果评估内核 | `src/kernels/evaluation/`；`src/ai/eval_report.py` | 🟢 | 目标完成度/报告聚合 |
| 人工评测 / 红队 | `src/evaluation/human_eval.py`、`red_team.py` | 🟢 | 有实现 |
| **真实模型回归门禁** | — | 🔴 | 无“换模型/改提示词→自动判是否变差”的固化流程 |
| 成本计费（内存） | `src/ai/economy.py`（`BillingEngine` L120，`record_usage` L164） | 🟡 | 聚合在 `self._usage` 字典，重启清零；未知模型返回 0 且 `unknown_model=True` |
| 成本实现四套并存 | `src/ai/economy.py` / `src/model_gateway/cost_optimizer.py` / `src/cost/cost_manager.py` / `src/sre/cost/cost_manager.py` | 🟡 | 口径分散，易漂移 |
| 成本表（ORM） | `src/integrations/orm_models.py`（`CostTracking` L658） | 🟡 | 已建表，**无写入方** |
| 配额 / 预算 | `src/kernels/resource` + `src/ai/agent_factory.py`（L286 起） | 🟢 | 按价目估算，未知价目会显式标注 |
| 文档解析 | `src/knowledge/documents.py`（`KnowledgeDocumentService`） | 🔴 | 只接受**纯文本**；无 PDF/DOCX/XLSX/PPTX/HTML 解析 |
| 分块 | `src/knowledge/chunker.py`（`chunk_text`） | 🟢 | 字符滑窗（约 512/50），非语义分块 |
| 检索 | `src/knowledge/retriever.py`（`Retriever.retrieve` L44） | 🟡 | **仅向量检索**（embedding + vector store），无 BM25/混合/重排 |
| 弱网重试 | `src/ai/providers.py`（`generate_with_retry` L95，退避 L155-156） | 🟢 | 指数退避 + `retry_after`，无抖动、无熔断 |
| 离线降级开关 | — | 🔴 | 无“离线模式 / 降级到本地模型 / 排队重放”产品级开关 |
| 性能基准脚本 | `scripts/performance_benchmark.py` | 🔴有缺陷 | L28 `import resource`、L50/L52 `resource.getrusage` 在 **Windows 不可用**，当前目标平台直接 ImportError |
| 审计写入性能门禁 | `scripts/bench_audit_append.py`；`bench_baseline.json`；`docs/autonomous/PERFORMANCE-BASELINE.md` | 🟢 | 同 profile 才比较，避免机器差异误判 |
| TTFT / 首字延迟 | — | 🔴 | 指标只有请求级 histogram，无首 token 打点 |
| 全量回归 | `tests/`（JUnit XML + `--timeout=60`） | 🟡 | 上轮 4685 passed / 15 failed；本轮未跑 |

---

## 2. 候选清单（按“值得引入程度”排序）

> 字段口径：star 与最近更新读取于 2026-11-05；许可证以仓库 LICENSE 原文为准；安全公告数来自 OSV（含历史/重复记录，未做可利用性分级）。  
> “测试”列只代表**仓库内存在测试目录/用例**，本轮未运行任何第三方测试套件。

| # | 仓库 | star | 最近更新 | 语言/栈 | 许可证（LICENSE 原文） | 维护 | 测试 | 核心源码目录 | OSV 公告 | 引入度 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `confident-ai/deepeval` | 19,293 | 2026-11-04 | Python / pytest | Apache-2.0 | 活跃 | `tests/` 存在 | `deepeval/evaluate/{evaluate,compare,local_store,console_report}.py`、`deepeval/metrics/`、`deepeval/dataset/` | 0 | **高** |
| 2 | `pydantic/genai-prices` | 409 | 2026-11-05 | Python / TS / Go / Rust | MIT | 活跃（Pydantic 官方） | `packages/python/` 内含测试 | `packages/python/genai_prices/{types,_usage,data_snapshot,update_prices}.py` | 0 | **高** |
| 3 | `benfred/py-spy` | 15,623 | 2026-11-04 | Rust + Python | MIT | 活跃 | `tests/` 存在 | `src/{dump,flamegraph,coredump,config}.rs`、`src/python_bindings/v3_*` | 0 | **高** |
| 4 | `microsoft/markitdown` | 193,946 | 2026-11-05 | Python | MIT | 活跃（微软） | `packages/` 内含测试 | `packages/markitdown/src/markitdown/{_markitdown,_base_converter,_stream_info}.py`、`converters/` | 0 | **高** |
| 5 | `xhluca/bm25s` | 1,822 | 2026-11-04 | Python（numpy/numba） | MIT | 活跃 | `tests/` 存在 | `bm25s/{scoring,tokenization,selection,stopwords}.py` | 0 | **高** |
| 6 | `zizmorcore/zizmor` | 6,882 | 2026-11-04 | Rust | MIT | 活跃 | `crates/*/tests` | `crates/`（含 `github-actions-expressions` 词法/语法与审计规则） | 0 | **高（低成本）** |
| 7 | `Preston-Landers/concurrent-log-handler` | 386 | 2026-10-21 | Python | Apache-2.0 | 低频但稳定 | `tests/` 存在 | `src/concurrent_log_handler/`（跨进程文件锁与 rollover） | 0 | **中高** |
| 8 | `UKGovernmentBEIS/inspect_ai` | 3,183 | 2026-11-05 | Python | MIT | 活跃（英国 AISI） | `tests/` 存在 | `src/inspect_ai/{_eval,scorer,solver,model,log,dataset,viewer,approval}` | 0（未查 `inspect-ai` 之外包名） | **中** |
| 9 | `adbar/trafilatura` | 7,078 | 2026-11-05 | Python | Apache-2.0 | 活跃 | `tests/` 存在 | `trafilatura/`（正文抽取与元数据模块；未逐文件核对文件名） | 0 | **中** |
| 10 | `promptfoo/promptfoo` | 26,870 | 2026-11-05 | TypeScript / Node | MIT | 活跃 | `test/` 存在 | `src/{assertions,codeScan,database}`、`examples/` | 0 | **中（Node 工具链）** |
| 11 | `kevin1024/vcrpy` | 3,022 | 2026-11-02 | Python | MIT | 低频 | `tests/` 存在 | `vcr/`（cassette / stubs / matchers） | **1**（任意代码执行，fixed 8.2.1） | **中** |
| 12 | `astral-sh/uv` | 91,546 | 2026-11-04 | Rust | MIT（LICENSE-MIT） | 活跃 | 未确认 | 无源码可借鉴（CLI 工具） | **11**（含 Windows wheel 解压路径穿越，fixed 0.12.18） | **中** |
| 13 | `syrupy-project/syrupy` | 900 | 2026-11-03 | Python / pytest | MIT | 活跃 | `tests/` 存在 | `src/syrupy/`（snapshot 扩展与断言） | 0 | **中** |
| 14 | `docling-project/docling` | 70,140 | 2026-11-05 | Python（布局模型） | MIT | 活跃（IBM） | `tests/` 存在 | `docling/{pipeline,backend,datamodel,chunking}` | **35**（未分级，多为历史/依赖） | **中（重）** |
| 15 | `open-telemetry/opentelemetry-python` | 2,701 | 2026-11-04 | Python | Apache-2.0 | 活跃 | 未确认 | `opentelemetry-{api,sdk,exporter}` | 0（`opentelemetry-sdk`） | **中（已用，补语义约定）** |
| 16 | `traceloop/openllmetry` | 7,523 | 2026-11-05 | Python | Apache-2.0 | 活跃 | 未确认 | `packages/`（各 `opentelemetry-instrumentation-*`） | 0（`instrumentation-openai`） | **低中（与现网重复）** |
| 17 | `jd/tenacity` | 8,818 | 2026-11-04 | Python | Apache-2.0 | 活跃 | 未确认 | `tenacity/`（retry / wait / stop） | 0 | **低（已有自研退避）** |
| 18 | `Delgan/loguru` | 24,156 | 2026-11-05 | Python | MIT | 活跃 | `tests/` 存在 | `loguru/`（file sink 的 rotation/retention/compression 实现） | **2**（历史，fixed 0.5.3） | **低（只借模式）** |

补充已存在依赖（不重复引入）：`langfuse/langfuse`（MIT 核心 + `ee/` 单独商业许可）、`Arize-ai/phoenix`（Elastic License 2.0）、`BerriAI/litellm`（MIT 核心 + 企业目录，85 条 OSV）、`explodinggradients/ragas`（Apache-2.0，本轮补充查看）、`ggml-org/llama.cpp`（MIT）、`microsoft/onnxruntime`（MIT）、`vllm-project/vllm`（Apache-2.0）、`grafana/loki`（AGPL-3.0）、`renovatebot/renovate`（AGPL-3.0）、`asg017/sqlite-vec`（MIT/Apache-2.0 双许可）、`jsvine/pdfplumber`（MIT）、`kevin1024/vcrpy`（见 #11）。

---

## 3. 候选逐条九问

> 每题均以“鎏灏当前代码”为参照，不引用未核实的宣传材料。

### 1) confident-ai/deepeval（Apache-2.0，高）

1. **已有同类？** 有零件：`src/evaluation/metrics.py`（指标）、`src/evaluation/benchmark_runner.py`（数据集/基线结构）、`src/kernels/evaluation/`（结果评估）、`src/ai/eval_report.py`（聚合）。缺“数据集→执行→断言→对比基线→报告”的一体化闭环。
2. **不足**：基线只在内存（`_baselines` L248）；无真实 Provider 执行器；无判定 DSL；无结果归档；无 CI 阈值。
3. **候选解决什么**：`Dataset` + `Metric` + `assert_test` / `evaluate` + `compare` + `local_store`，一条命令回答“换模型后是否变差”。
4. **值得借鉴的目录**：`deepeval/evaluate/{evaluate.py,compare.py,local_store.py,console_report.py,execute/}`、`deepeval/metrics/`、`deepeval/dataset/`。
5. **可独立封装？** 可以。作为 `dev/test` 额外依赖，不进运行期镜像。
6. **破坏接口？** 否。新增 `tests/eval/` 与脚本，不改 `src/` 运行路径。
7. **许可证**：Apache-2.0，允许商用、修改、再分发，需保留版权/NOTICE（若分发二进制）。
8. **风险/成本**：依赖较多；仓库含 `deepeval/telemetry/`（client/api/identity 等）整套遥测模块，接入前必须核实默认上报行为并显式关闭；真实评测需要网络与额度；维护成本中等。
9. **测试怎么写**：`@pytest.mark.eval` 标记并默认 deselect；夜间 job 用真实 Provider（temperature=0、固定 seed）；断言 `pass_rate` 不低于基线且关键用例 100%；配合 vcrpy 录制做离线回放。

### 2) pydantic/genai-prices（MIT，高）

1. **已有同类？** `src/ai/economy.py` 的 `PriceTable`（构造注入，简单 float/dict）；`src/model_gateway/cost_optimizer.py` 有成本计算。
2. **不足**：价格手写、无生效日期、无阶梯计价/条件计价、无离线快照、未知模型只能置 0 并标 `unknown_model`。
3. **候选解决什么**：`ModelPrice` / `TieredPrices` / `ConditionalPrice` / `PriceCalculation`，按日期取值、可自动更新价格快照。
4. **值得借鉴**：`packages/python/genai_prices/{types.py,_usage.py,data_snapshot.py,update_prices.py,units.py}`。
5. **可独立封装？** 可以，封装成 `src/cost/price_catalog.py`，作为 `BillingEngine` 的价格来源。
6. **破坏接口？** 否。`BillingEngine`、`estimate_cost`、`record_usage` 签名不变。
7. **许可证**：MIT。
8. **风险/成本**：价格数据需定期更新（或随包冻结快照）；多语言包但 Python 包很轻；OSV 0。
9. **测试怎么写**：已知价格样例→成本断言；阶梯/条件边界；快照哈希；与真实 Provider 返回 `usage` 对账（误差 0）。

### 3) benfred/py-spy（MIT，高）

1. **已有同类？** 无运行时采样；`scripts/performance_benchmark.py` 是端到端计时（且 Windows 下 `import resource` 直接失败）。
2. **不足**：启动慢、首字延迟、卡死没有栈级证据；崩溃无现场；无法回答“时间花在哪个函数”。
3. **候选解决什么**：对运行中进程采样生成火焰图（`record`）、即时栈转储（`dump`）、实时 top；无需改代码、无需重启。
4. **值得借鉴**：`src/dump.rs`（JSON 栈转储）、`src/flamegraph.rs`、`src/python_bindings/v3_*`（按 Python 版本解析栈，这是跨版本稳定的关键）。
5. **可独立封装？** 是。作为可选运维二进制随包分发，不进入 Python import 面。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：平台差异（Windows 权限/符号与 Linux 不同，本轮未实测）；包体积与平台矩阵；OSV 0。
9. **测试怎么写**：冒烟测试启动子进程 → `py-spy record -d 5` → 断言 SVG 非空且包含预期函数名；失败则跳过并显式标注“未验证”，不伪造通过。

### 4) microsoft/markitdown（MIT，高）

1. **已有同类？** `src/knowledge/documents.py` 只接受 `text`。
2. **不足**：PDF/Word/Excel/PPT/HTML 无法进入知识库；用户只能手工复制文本。
3. **候选解决什么**：统一转 Markdown（保留标题/表格），带 MIME 探测与可插拔 converter。
4. **值得借鉴**：`packages/markitdown/src/markitdown/{_markitdown.py,_base_converter.py,_stream_info.py}` 与 `converters/`（注册表 + 优先级 + `StreamInfo` 抽象非常干净）。
5. **可独立封装？** 是，封装为 `src/knowledge/loaders/markitdown_loader.py`，输出 Markdown 后交给现有 `chunk_text`。
6. **破坏接口？** 否。`KnowledgeDocumentService.ingest(text)` 不变，loader 只做前置转换。
7. **许可证**：MIT。
8. **风险/成本**：解析不可信文件是攻击面（需大小上限、超时、类型白名单、可选子进程隔离）；依赖 `magika` 等；OSV 0。
9. **测试怎么写**：固定样本（PDF/DOCX/XLSX/HTML）→ 断言标题与表格结构保留；超大文件拒绝；压缩炸弹/畸形文件不崩溃；中文编码回归。

### 5) xhluca/bm25s（MIT，高）

1. **已有同类？** 仅向量检索：`src/knowledge/retriever.py`。
2. **不足**：专有名词、错误码、模型名等关键词召回差；无混合排序。
3. **候选解决什么**：纯 numpy 的 BM25（可选 numba 加速），稀疏 top-k，适合本地小语料。
4. **值得借鉴**：`bm25s/{scoring.py,tokenization.py,selection.py,stopwords.py}`。
5. **可独立封装？** 是，`src/knowledge/lexical_index.py`，与向量检索并列。
6. **破坏接口？** 否。`Retriever.retrieve` 内部做 RRF 融合，返回结构不变。
7. **许可证**：MIT。
8. **风险/成本**：索引内存随语料线性增长（桌面需上限 + 懒加载）；中文需分词（仓库自带 tokenization 未必适配中文，未验证）。
9. **测试怎么写**：同一 query 集对比纯向量 vs 混合的 recall@k；空索引、增量更新、删除、持久化重载；内存上限用例。

### 6) zizmorcore/zizmor（MIT，高/低成本）

1. **已有同类？** CI 有 Semgrep 架构门禁、Trivy/Bandit/pip-audit，但**没有 workflow 自身安全审计**。
2. **不足**：表达式注入（`${{ github.event.* }}` 进入 `run`）、过度 `permissions`、未 pin 的 action、`pull_request_target` 风险无人检查。
3. **候选解决什么**：Rust 静态分析 GitHub Actions，覆盖注入/权限/未固定版本等规则。
4. **值得借鉴**：`crates/`（`github-actions-expressions` 词法/语法 + 审计规则组织方式）。
5. **可独立封装？** 是，CI-only 二进制。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：新增 CI 依赖需固定版本+校验和；误报需基线；OSV 0。
9. **测试怎么写**：先 `--persona=regular` 只读跑一遍；再把 findings 固化为快照；门禁与 Semgrep 同口径（零 findings）。

### 7) Preston-Landers/concurrent-log-handler（Apache-2.0，中高）

1. **已有同类？** `src/observability/logging_utils.py` 用 stdlib `RotatingFileHandler`（且未接线）。
2. **不足**：Windows 多进程/多 worker 同时轮转会互相踩踏（rename 失败、丢日志）；启动脚本若多开更明显。
3. **候选解决什么**：跨进程安全的文件轮转（文件锁 + rollover）。
4. **值得借鉴**：`src/concurrent_log_handler/` 的锁与 rollover 流程（可只读参考，不必依赖）。
5. **可独立封装？** 是，替换 dictConfig 中的 handler 类即可。
6. **破坏接口？** 否。
7. **许可证**：Apache-2.0。
8. **风险/成本**：文件锁在 NFS/网络盘不可靠；项目 star 少、维护单点；OSV 0。
9. **测试怎么写**：多进程并发写 → 断言零丢失、轮转成功、备份数正确；Windows 专项 CI（或本地标记）。

### 8) UKGovernmentBEIS/inspect_ai（MIT，中）

1. **已有同类？** 同 #1，零件齐但无标准 benchmark 执行与归档。
2. **不足**：无标准数据集加载、无可复现实验日志、无结果查看器。
3. **候选解决什么**：`Dataset`/`Solver`/`Scorer` + JSONL 日志 + viewer，适合离线批量 benchmark。
4. **值得借鉴**：`src/inspect_ai/{_eval,scorer,solver,model,log,dataset,viewer,approval}`。
5. **可独立封装？** 是，dev-only。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：体量大；默认 Docker 沙箱对桌面不友好；与 DeepEval 功能重叠，**不建议两套同时上**。
9. **测试怎么写**：仅夜间 benchmark job；固定数据集版本；结果 JSONL 入库并与 DeepEval 基线表对齐。

### 9) adbar/trafilatura（Apache-2.0，中）

1. **已有同类？** 无网页正文抽取；知识库只吃纯文本。
2. **不足**：URL→知识库没有正文清洗，导航/广告会污染向量。
3. **候选解决什么**：正文与元数据抽取、去导航、去重。
4. **值得借鉴**：`trafilatura/` 包内抽取与元数据模块（本轮未逐文件核对文件名，故不写具体文件）。
5. **可独立封装？** 是，作为 loader 前置。
6. **破坏接口？** 否。
7. **许可证**：Apache-2.0。
8. **风险/成本**：抓取外部 URL 引入 SSRF 与合规风险，必须域名白名单 + 大小上限 + 超时；OSV 0。
9. **测试怎么写**：固定 HTML fixture 断言正文；私网 URL 必须拒绝；超时/超大响应降级为“仅保存标题”。

### 10) promptfoo/promptfoo（MIT，中）

1. **已有同类？** 同上评测零件；CI 只有 pytest + 脚本门禁。
2. **不足**：无声明式 eval 矩阵、无跨模型并排对比、无内置红队用例集。
3. **候选解决什么**：YAML 声明 model×prompt×assert，CI 里直接跑并给 exit code。
4. **值得借鉴**：`src/assertions/`、`src/codeScan/`、`src/database/`、`examples/`。
5. **可独立封装？** 是，CI-only。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：引入 Node 工具链（仓库已有 Node 前端，可接受）；会调用外部 API；需锁版本；OSV 0。
9. **测试怎么写**：nightly `promptfoo eval --config`，结果入档 + 阈值门禁；与 DeepEval 结果做交叉校验（抽样）。

### 11) kevin1024/vcrpy（MIT，中）

1. **已有同类？** 无 HTTP 录制回放；现有测试以 mock 为主。
2. **不足**：Provider 行为测试依赖真实网络，弱网/断网不可复现。
3. **候选解决什么**：录制 cassette 后离线回放。
4. **值得借鉴**：`vcr/` 的 cassette/stubs/matchers 设计。
5. **可独立封装？** 是，tests-only。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：**GHSA-rpj2-4hq8-938g：cassette 的 YAML 反序列化可致任意代码执行，fixed 8.2.1** → 必须 `>=8.2.1` 且只加载自有 cassette；cassette 可能含密钥，需 `filter_headers`。
9. **测试怎么写**：录一次真实调用 → 断网回放 → 断言一致；CI 默认离线；对 cassette 做 PII/密钥扫描。

### 12) astral-sh/uv（MIT，中）

1. **已有同类？** CI 使用 pip；`pyproject.toml` 已有 `[tool.uv]` 段。
2. **不足**：依赖树大（含 torch/docling 类重量包时）安装慢，CI 时间长。
3. **候选解决什么**：更快的解析/安装与 lockfile 可重现。
4. **值得借鉴**：无源码可借鉴（CLI）。
5. **可独立封装？** 是，仅替换 CI 安装步骤。
6. **破坏接口？** 否（不改进程接口）。
7. **许可证**：MIT（`LICENSE-MIT`）。
8. **风险/成本**：改变构建链，需同步 cache key 与镜像；**OSV 11 条，含 Windows wheel 解压路径穿越 fixed 0.12.18** → 必须 `>=0.12.18`。
9. **测试怎么写**：对比 CI 时长与产物哈希；锁文件可重现安装；在 Windows runner 上验证。

### 13) syrupy-project/syrupy（MIT，中）

1. **已有同类？** 无快照测试。
2. **不足**：报告/配置结构变化只能手写断言，容易漏。
3. **候选解决什么**：pytest 快照断言与更新。
4. **值得借鉴**：`src/syrupy/`（extension/assertion 组织）。
5. **可独立封装？** 是，tests-only。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：`--snapshot-update` 会掩盖真实回归，必须 code review 门禁；OSV 0。
9. **测试怎么写**：对告警 JSON、诊断包清单、评测报告做快照；CI 只校验不更新。

### 14) docling-project/docling（MIT，中/重）

1. **已有同类？** 同 #4。
2. **不足**：复杂 PDF（多栏、表格、扫描件）结构丢失。
3. **候选解决什么**：布局分析 + 表格结构 → `DoclingDocument` → Markdown/JSON，另有 `chunking`。
4. **值得借鉴**：`docling/{pipeline,backend,datamodel,chunking}`。
5. **可独立封装？** 是，但作为可选 extra（重依赖、CPU 慢）。
6. **破坏接口？** 否。
7. **许可证**：MIT。
8. **风险/成本**：体积/内存大，首次加载慢；OSV 35 条（未分级）需 triage；不适合做默认解析器。
9. **测试怎么写**：与 markitdown 同 fixture 对比表格/标题保真度；超时与内存上限；只对“复杂 PDF”启用。

### 15) open-telemetry/opentelemetry-python（Apache-2.0，中/已用）

1. **已有同类？** `src/observability/tracing.py` + adapter 已用。
2. **不足**：span 未遵循 GenAI 语义约定，缺 `gen_ai.*` 属性（模型、token、成本无法从 span 直接读出）。
3. **候选解决什么**：统一 span 属性规范，换后端不改代码。
4. **值得借鉴**：语义约定本身（不在本仓库源码目录内，属规范文档）。
5. **可独立封装？** 是，只改属性拼装层。
6. **破坏接口？** 低风险（新增属性），但需防高基数。
7. **许可证**：Apache-2.0。
8. **风险/成本**：prompt 内容入 span 有 PII 风险，只记长度/哈希；OSV 0（sdk）。
9. **测试怎么写**：断言属性存在且不含原文；采样率与导出器禁用时不产生网络请求。

### 16) traceloop/openllmetry（Apache-2.0，低中）

1. **已有同类？** 已有手写 OTel + provider 埋点。
2. **不足**：每个 Provider 手工埋点，易漏。
3. **候选解决什么**：自动 instrument OpenAI/Anthropic 等并采集 token。
4. **值得借鉴**：`packages/`（各 `opentelemetry-instrumentation-*`）。
5. **可独立封装？** 是，但会与现有手写 tracing **重复**。
6. **破坏接口？** 中：重复 span/重复导出。
7. **许可证**：Apache-2.0。
8. **风险/成本**：版本兼容矩阵大；OSV 0（`instrumentation-openai`）。
9. **测试怎么写**：灰度开关；断言不产生重复 span；关闭后零副作用。

### 17) jd/tenacity（Apache-2.0，低）

1. **已有同类？** `src/ai/providers.py:95-156` 已有指数退避。
2. **不足**：无抖动、无统一 stop 条件、异步封装需自己写。
3. **候选解决什么**：成熟 retry 装饰器（jitter/wait/stop）。
4. **值得借鉴**：`tenacity/`。
5. **可独立封装？** 是。
6. **破坏接口？** 低。
7. **许可证**：Apache-2.0。
8. **风险/成本**：为 30 行自研逻辑新增依赖，收益低；OSV 0。
9. **测试怎么写**：与现有重试行为等价性测试（次数、延迟序列、`retry_after` 优先）。

### 18) Delgan/loguru（MIT，低/只借模式）

1. **已有同类？** structlog 已选型。
2. **不足**：轮转/保留/压缩/异步 enqueue 需手拼。
3. **候选解决什么**：一行配置 rotation/retention/compression/enqueue。
4. **值得借鉴**：`loguru/` 中 file sink 的 rotation 判定与压缩实现。
5. **可独立封装？** 不建议把 loguru 作为日志门面；可借实现。
6. **破坏接口？** 高：整体替换会破坏 structlog 字段、测试与告警关联。
7. **许可证**：MIT。
8. **风险/成本**：两套日志栈并存；OSV 2 条（历史，fixed 0.5.3）。
9. **测试怎么写**：不引入则只做模式对照；若试用，做字段等价性与轮转专项。

---

## 4. 最值得的 5 个深评

### 4.1 DeepEval：把“换模型有没有变差”变成一条可执行命令

**现状证据**

- `src/evaluation/benchmark_runner.py:224-251`：`BenchmarkRunner` 的 datasets/suites/results/baselines/runs 全是内存 `Dict`；`results_dir` 仅在 `__init__` 里 `os.makedirs`（L253-254），**没有任何写盘/读盘代码**。
- `src/evaluation/metrics.py` 已有指标函数，但没有“断言 + 基线 + 退出码”。
- `src/ai/eval_report.py` 只是聚合报告，不触发评测。

**最小可行方案（不引入第二套框架）**

1. **固定黄金集**：30–50 条，覆盖 5 类核心能力——普通对话、Planner 任务分解、工具选择（是否选对 tool+参数 JSON）、RAG 回答（引用是否命中）、拒绝/安全（不该答的不答）。每条含 `input`、`expected`（精确/正则/JSON schema/参考片段）、`critical: bool`。
2. **断言分四档**（由便宜到贵，默认只用前两档，避免 LLM-as-judge 的不稳定与费用）：
   - 结构断言：JSON schema、正则、必含/必不含；
   - 语义断言：用现有 Ollama embedding 算余弦相似度阈值；
   - 工具断言：解析 tool call，断言工具名与参数键；
   - 可选裁判：仅在夜间对 `critical` 用例做 LLM-as-judge，双裁判取一致。
3. **持久化基线**：新增 SQLite 表（或复用 `cost_tracking` 同库）`eval_runs(run_id, model, prompt_version, dataset_hash, metrics_json, created_at)`；`dataset_hash` 用内容哈希，防止“悄悄改题”。
4. **门禁**：`pass_rate >= baseline - 0.05` 且 `critical` 用例 100% 通过；`p95` 延迟不得比基线恶化 20%。不达标 CI 红。
5. **执行位置**：默认 `pytest -m "not eval"` 完全跳过；夜间/发布前 job 用真实 Provider 跑。这样不把外部 API 带进日常 CI。
6. **借 DeepEval 的部分**：`deepeval/evaluate/{evaluate,compare,local_store,console_report}.py` 的组织方式、`Dataset` 与 metric 接口；**不必**把它接进 `src/`。

**为什么不是 inspect_ai / promptfoo**：inspect_ai 更适合“跑公开 benchmark”，体量与 Docker 沙箱对桌面产品偏重；promptfoo 是 Node 工具链，适合做“发布前红队矩阵”，可作为第二层，而不是唯一评测层。

### 4.2 genai-prices + Provider 出口钩子：成本计量不侵入业务

**现状证据**

- `src/ai/economy.py:128-130`：`price_table` 构造注入，`_usage` 是内存字典。
- `src/integrations/orm_models.py:658`：`CostTracking` 表已定义，但全仓无写入方。
- 成本实现四套并存，口径分散。

**设计（业务零改动）**

```
Provider.generate_with_retry()  <-- 唯一出口
        │  返回文本 + usage（prompt_tokens/completion_tokens）
        ├─ genai-prices: 按模型+日期取价，算 input/output 分项成本
        ├─ 无 usage 的 Provider: tiktoken 估算，标记 estimated=True（不得伪装精确）
        └─ 异步批量写 cost_tracking（失败仅记日志，不阻塞主流程）
```

要点：

1. **采集点唯一**：只在 `src/ai/providers.py` 的 `generate_with_retry` 出口；不往 agent/planner/tool 里加计费调用。
2. **价格来源唯一**：`src/cost/price_catalog.py` 包 genai-prices，带本地快照（离线可用）与 `effective_date`；未知模型返回 `unknown`，不返回 0。
3. **持久化**：`cost_tracking` 写入使用“锁内快照 + 唯一临时文件/事务 + 失败 `logger.error`”，禁止静默吞掉。
4. **幂等**：以 `request_id`（已有 trace_id）作唯一键，重试不重复计费。
5. **展示**：控制台显示“今日/本任务 token 与费用”，并标注 `estimated` 与 `unknown` 两类，不伪造精确值。
6. **不引入 LiteLLM**：它有 85 条 OSV 记录且与现有 Provider 抽象重复。

### 4.3 py-spy + 诊断包：首字延迟与崩溃现场

**现状证据**

- `scripts/performance_benchmark.py:28` 顶层 `import resource`，L50/L52 用 `getrusage` → **Windows 直接 ImportError**，该脚本在目标平台不可用。
- 指标只有请求耗时 histogram，没有首 token 打点。
- 无 `faulthandler`、无崩溃转储。

**落地组合（全部轻量、可随产品分发）**

| 目标 | 工具/手段 | 说明 |
|---|---|---|
| 启动慢 | `python -X importtime`（标准库）+ py-spy `record` | 先看 import 冷启动排行，再看 CPU 栈 |
| 首字延迟 | 在流式循环“首个 chunk”处 `perf_counter` 打点 | 产出 `ttft_ms` 指标（provider/label 维度），不进 histogram |
| 卡死 | `py-spy dump --pid` | 输出全线程栈 JSON，直接进诊断包 |
| 崩溃 | `faulthandler.enable(file=...)` | 段错误/死锁等原生崩溃也有栈 |
| 内存 | `memray`（可选，Apache-2.0） | 仅诊断模式下启用，避免常驻开销 |
| 现场 | `psutil` 快照 + 版本/配置（脱敏）+ 最近 200 行日志 + 栈 | 打包 zip，一键导出 |

**注意**：py-spy 在本轮**未实测 Windows 行为**，落地前先做冒烟；`memray` 未在本轮核对 LICENSE 之外的细节（已知 Apache-2.0）。

### 4.4 markitdown：让知识库真正能吃文档

**现状证据**：`src/knowledge/documents.py` 的 `KnowledgeDocumentService` 只接受文本；`src/knowledge/chunker.py` 是字符滑窗。

**方案**

1. 新增 `src/knowledge/loaders/markitdown_loader.py`：输入文件路径/字节流 → Markdown 文本 + 元数据（页数、标题）。
2. 限制：类型白名单（pdf/docx/xlsx/pptx/html/md/txt）、单文件 ≤ 20MB、解析超时 30s、并发 1（桌面）。
3. 安全：解析放子进程（`multiprocessing`）或至少独立线程 + 超时；输出截断；失败返回明确错误，不静默空文档。
4. **默认 markitdown，复杂 PDF 才用 docling**（docling 重依赖、CPU 慢、OSV 35 条待 triage）。
5. 现有 `chunk_text` 保持不变；后续若要语义分块，再单独立项。

### 4.5 zizmor：给 CI 自身做安全审计（最低成本、最高确定性）

**现状证据**：CI 已有 Semgrep（架构）、Trivy/Bandit/pip-audit（依赖与代码），但工作流本身的表达式注入、权限过宽、action 未 pin 无人检查。

**落地**

1. CI 增加一个只读 job：`zizmor --persona=regular .github/workflows`。
2. 首次运行把 findings 固化为基线快照（syrupy 或 JSON），之后要求“零新增”。
3. 固定 zizmor 版本 + 校验和；不与 Semgrep 规则重复。
4. 收益确定、维护成本低，属于“当天可完成”的改进行。

---

## 5. 重点回答（本路核心）

### 5.1 模型评测与回归：从零到一的最小可行方案

**一句话**：30–50 条黄金用例 + 两档便宜断言 + SQLite 基线 + 夜间真实 Provider + `pass_rate`/`critical` 双阈值，**不引入 LLM 裁判也能回答“有没有变差”**。

| 阶段 | 内容 | 产出 |
|---|---|---|
| P0（1 天） | 建黄金集与 4 类断言；用现有 `src/evaluation/metrics.py` 打分 | `tests/eval/golden.yaml` |
| P1 | SQLite 存 `eval_runs` 与 `dataset_hash`；实现 `compare_to_baseline` 的持久化版本 | 基线表 + 对比报告 |
| P2 | 夜间 job 接真实 Provider；`pytest -m eval`；阈值门禁 | CI 门禁 |
| P3（可选） | 引入 DeepEval 做报告/对比；多轮对话与复杂任务再评估裁判模型 | 报告归档 |

**必须避免的伪评测**：

- 用 mock provider 跑“评测”然后宣布通过；
- 只比“总分”不看 `critical` 用例；
- 数据集无哈希，导致“改题提分”；
- temperature > 0 且只跑一次，结果不可复现；
- 把 LLM-as-judge 当唯一标准且不校准。

### 5.2 日志轮转、磁盘上限、崩溃诊断包：可直接借鉴的成熟做法

| 需求 | 成熟做法 | 来源（许可） | 对鎏灏的落地 |
|---|---|---|---|
| 轮转 | `RotatingFileHandler(maxBytes, backupCount)` / `TimedRotatingFileHandler(when, backupCount)` | Python 标准库 | **先调用已写好的 `configure_logging()`**；把 `configs/observability/logging.yml` 的坏 filter 修掉或删除该配置 |
| 多进程安全 | 文件锁式 handler | `concurrent-log-handler`（Apache-2.0） | 仅当确认多 worker/多开时启用；单进程不必引入 |
| 异步不阻塞 | `QueueHandler` + `QueueListener` | Python 标准库 | 日志写入放后台线程，避免请求路径抖动 |
| 压缩/保留 | `rotation + retention + compression` | `loguru`（MIT）实现模式 | 借模式：轮转后 gzip，按日期/总量清理；不替换 structlog |
| 总量上限 | 目录配额 + LRU 清理 | `logrotate`（GPL-2.0，仅借鉴思路，不分发） | 应用内做：`logs/` 总量 > N MB 时按最旧删除 |
| 崩溃现场 | `faulthandler` + 栈转储 | 标准库 / `py-spy`（MIT） | 诊断模式开启；崩溃包含栈、版本、脱敏配置、最近日志 |
| 诊断包 | 一键 zip：系统信息 + 配置（脱敏）+ 日志 + 指标快照 + 栈 | `psutil`（BSD-3）+ 自研 | 控制台“导出诊断”按钮，显式告知内容 |

**关键判断**：D1（日志不轮转）根因不是“缺库”，而是**写好的配置没被调用**。先修接线与磁盘上限，再谈第三方 handler。

### 5.3 性能瓶颈定位：适合随产品分发的轻量工具

| 工具 | 用途 | 体积/侵入 | 许可 | 建议 |
|---|---|---|---|---|
| `python -X importtime` | 启动 import 耗时排行 | 0，标准库 | PSF | **默认开启于诊断模式** |
| `py-spy` | 运行中采样火焰图 / 栈转储 | 单二进制，无侵入 | MIT | **随包可选分发** |
| `faulthandler` | 原生崩溃/死锁栈 | 0，标准库 | PSF | 默认开启（写文件） |
| `psutil` | CPU/内存/句柄快照 | 小 | BSD-3 | 诊断包必带 |
| `memray` | 内存分配追踪 | 中（需重跑） | Apache-2.0 | 仅诊断模式 |
| `llama-bench`（llama.cpp） | 本地模型 tokens/s | 需本地模型 | MIT | 仅在用 Ollama/GGUF 时参考 |
| `hyperfine` | CLI 冷启动计时 | 需外部安装 | MIT/Apache-2.0（未核实） | 开发机使用，不分发 |

**不建议**：把 `vLLM` 指标栈、分布式 tracing（Tempo/Jaeger）搬进桌面产品；单进程应用没有跨服务链路。

### 5.4 成本计量：不侵入业务代码的三条线

1. **采集线**：只在 `Provider.generate_with_retry`（`src/ai/providers.py:95`）出口读取 `usage`；缺 `usage` 时用 `tiktoken` 估算并标记 `estimated`。
2. **计价线**：`src/cost/price_catalog.py` 包装 `genai-prices`，价格带 `effective_date` 与离线快照；未知模型返回 `unknown` 而非 0。
3. **落库线**：异步批量写 `cost_tracking`（`orm_models.py:658` 已建表）；以 trace_id 幂等；失败记 `logger.error` 并保留本地缓冲，不阻塞用户请求。

**禁止**：在各 agent/tool/planner 里散落计费调用；禁止用内存字典充当账单；禁止把未知模型按 0 元计费后当作“免费”。

### 5.5 单机桌面产品里的“过度工程”可观测性

| 能力 | 判定 | 理由 | 建议 |
|---|---|---|---|
| Prometheus + Alertmanager + Grafana | ⚫ 过度（默认） | 三容器 + 配置，单用户桌面无多实例聚合需求 | 保留可选 compose，默认不启动 |
| Loki + Promtail | ⚫ 过度 | 本地文件日志 + 应用内检索足够；AGPL 也增加分发顾虑 | 不打包进发行物 |
| Tempo / Jaeger | ⚫ 过度 | 单进程无跨服务链路；trace_id 已在日志/告警中串联 | 删除或仅开发可选 |
| OTEL Collector | ⚫ 过度 | 进程内 exporter 足够 | 仅在接外部后端时需要 |
| node-exporter / cAdvisor | ⚫ 过度 | 桌面无容器编排监控需求 | 不进默认栈 |
| Phoenix（ELv2） | ⚫ 过度 | 非 OSI 许可 + 重 | 开发调试可选，不进发行物 |
| 告警规则引擎 | 🟢 保留 | 本地阈值告警（磁盘、错误率、Provider 失败）对单机仍有用 | 保留，但 sink 默认本地 |
| 健康/就绪/子系统 | 🟢 保留 | 启动自检与首次运行向导需要 | 保留 |
| trace_id | 🟢 保留 | 排障核心 | 保留 |
| 指标 histogram | 🟢 保留 | 本地诊断页数据源 | 保留，降采样 |

**结论**：默认交付形态应是“**一个进程 + 文件日志 + 本地指标 + 诊断页 + 诊断包**”；容器观测栈作为“高级/企业部署”可选，而不是默认依赖。

---

## 6. 明确不建议引入（及原因）

| 候选 | 许可证/事实 | 不建议原因 |
|---|---|---|
| `grafana/loki` | **AGPL-3.0** | 分发/修改义务与桌面产品不符；默认栈也不需要日志聚合服务 |
| `renovatebot/renovate` | **AGPL-3.0** | 用 GitHub Dependabot 即可，无许可与运维负担 |
| `BerriAI/litellm` | MIT 核心 + 企业目录；**85 条 OSV** | 与现有 Provider 抽象重复；安全面大；本轮查到 2026 年 SSRF（fixed 1.88.6） |
| `Arize-ai/phoenix` | **Elastic License 2.0**（非 OSI） | 已作为依赖存在，但只可内部使用；不可作为托管服务对外；默认栈不启用 |
| `AgentOps-AI/tokencost` | MIT；**依赖 litellm** | 为计价引入 litellm 安全面，不划算；改用 genai-prices |
| `vllm-project/vllm` | Apache-2.0 | GPU 服务器向，桌面 CPU 场景不适配 |
| `microsoft/onnxruntime` / `openvinotoolkit/openvino` | MIT / Apache-2.0 | 当前没有“本地 HF 模型直推理”路径；先解决 Ollama 路径 |
| `boxed/mutmut` | BSD | 变异测试成本高；先把覆盖率与评测门禁补上 |
| `pytest-dev/pytest-xdist` | MIT | 共享 SQLite/单例夹具下有隔离风险；先做套件分层与并行安全改造 |
| `getsentry/sentry-python` | MIT | 远程上报需用户同意与自托管；先用本地诊断包 |
| 默认捆绑 Prometheus/Grafana/Alertmanager/Tempo/node-exporter/cAdvisor | 各自许可不同 | 单机桌面过度工程（见 §5.5） |
| `jd/tenacity`（作为新依赖） | Apache-2.0 | 现有指数退避已够用；只借 jitter 思路，不新增依赖 |
| `Delgan/loguru`（作为日志门面） | MIT | 替换 structlog 破坏字段与测试；只借轮转/压缩实现 |

---

## 7. 未验证项清单

1. **star / 最近更新**：来自 `ungh.cc` 代理（2026-11-05），未再用 GitHub REST API 复核（本轮匿名配额耗尽）。
2. **未 clone、未逐文件读完第三方源码**：每个候选均核对了 **LICENSE 原文 + 仓库顶层/关键目录树**；另对 5 个候选（deepeval、genai-prices、py-spy、markitdown、bm25s）读取了 1 个核心文件片段；未运行其测试套件。
3. **测试覆盖**：仅确认 `tests/` 或包内测试目录存在；“覆盖率百分比”未获取。
4. **OSV 公告**：数量为原始记录数，包含历史版本与重复；仅对 `vcrpy`、`uv`、`loguru`、`sqlite-vec`、`litellm` 查看了修复版本/摘要，其余未做可利用性分级。
5. **D5 全量回归**：`4685 passed / 15 failed（14 既存）`来自上一轮会话记录，**本轮按边界未复跑**，不能据此判定当前状态。
6. **流式输出 / TTFT**：未核实 `src/ai/providers.py` 是否已有流式接口；若无，首 token 打点需新增。
7. **py-spy 在 Windows 的实际可用性**：未实测；仅确认源码含版本化 Python 栈解析。
8. **trafilatura 具体源文件名**：未逐文件核对，故只写包级目录。
9. **`opentelemetry-python` 的测试目录**：未确认是否含独立 `tests/`（star 与更新已复核为 2,701 / 2026-11-04）。
10. **`langfuse` / `phoenix` 主链路是否真的从未调用**：基于 `src/` 检索未见调用点；未做运行期动态验证。
11. **文档索引门禁后果**：`docs/oss-radar/` 下已有 6 份雷达文档（含本文件），`docs/README.md` 中未见 `oss-radar` 或任一文件名条目（本轮检索无命中），因此 `verify_docs_index_coverage.py`（`ci.yml:443`）在该目录出现时**就已处于“未收录”状态**，并非本文件新增导致。按任务边界未改 `docs/README.md`；**需负责人后续统一补登整个 `docs/oss-radar/`**。
12. **本文件未给出的价格/性能数字**：无实测，全部未编造；涉及“预计收益”只做定性排序。

---

## 8. 核实方法与可复现命令（本轮实际执行）

```powershell
# 仓库元数据（star / 最近更新）
Invoke-RestMethod "https://ungh.cc/repos/{owner}/{repo}"

# 仓库文件树（用于确认源码目录与测试目录存在）
Invoke-RestMethod "https://ungh.cc/repos/{owner}/{repo}/files/HEAD"

# LICENSE 原文（逐仓库确认许可类型）
Invoke-RestMethod "https://raw.githubusercontent.com/{owner}/{repo}/HEAD/LICENSE"

# 公开安全公告
Invoke-RestMethod -Method Post -Uri "https://api.osv.dev/v1/query" `
  -ContentType "application/json" -Body '{"package":{"ecosystem":"PyPI","name":"litellm"}}'
```

关键代码证据（本仓库内）：

- 日志未接线：`src/observability/logging_utils.py:80` 与 `src/logging_config.py:170`（两个模块均无调用；后者为裸 `FileHandler`）。
- 死配置：`config/default.yaml:54-69`；实际压缩硬编码 `src/gateway/main.py:679`。
- 指标可信判定：`src/gateway/observability.py:53`（`_is_internal_source`）。
- 限流前缀：`src/gateway/main.py:638-643`；默认路径 `config/default.yaml:56`。
- 评测内存态：`src/evaluation/benchmark_runner.py:245-254`。
- 成本内存态：`src/ai/economy.py:128-130`；成本表未写入：`src/integrations/orm_models.py:658`。
- 知识库仅文本/仅向量：`src/knowledge/documents.py:30`、`src/knowledge/retriever.py:44`。
- 性能脚本 Windows 缺陷：`scripts/performance_benchmark.py:28`、`:50`、`:52`。
