# Phase 7a — 生产化就绪层设计（指标暴露 / 子系统探针 / 配置预检）

> 状态：设计已定，实现中。范围是 Phase 7（工程生产化）的**第一个可验证切片**。
> 原则沿用全项目纪律：**先实测再判断**、**附加式改动**、**默认零行为变化**、**诚实失败不静默降级**。

## 1. 实测发现（决定"接线"而非"重造"）

| # | 事实 | 证据 | 结论 |
|---|------|------|------|
| 1 | `src/observability/metrics.py` 已有一整套 Prometheus 指标（HTTP / provider / agent / goal / task / process，40+ 个 Counter/Gauge/Histogram） | `metrics.py:12` `from prometheus_client import ...`；`metrics.py:17` `REGISTRY = CollectorRegistry()` | 指标基建**已存在** |
| 2 | 该注册表的导出函数 `generate_metrics()` **在全仓库没有任何调用点** | `metrics.py:405-408`；`grep -rn generate_latest src/ tests/` 只命中定义处本身 | **孤儿函数** —— 与 Phase 3 的 `Evaluator`、Phase 5 的 `model_gateway`、Phase 6 的 `Evaluator.stats()` 同一种病 |
| 3 | 线上 `/v1/metrics` 返回的是**自定义 JSON**（只含 auth 组件统计），**不是** Prometheus 文本格式 | `src/gateway/health.py:169-192` | Prometheus/Grafana **刮不到**真实指标 |
| 4 | `/v1/ready` 只检查 5 个 **auth** 组件（api_key / jwt / rbac / encryption / rate_limiter），**完全不检查任何内核子系统** | `src/gateway/health.py:65-109`；`tests/test_gateway_health_wiring.py:32-38` `REQUIRED_CHECKS` | 14 内核是否可加载、可响应，**无人验证** |
| 5 | 14 个内核**统一**暴露 `stats()` | memory:461 / evaluation:532 / event:280 / policy:742 … | 存在**统一探针面**，无需为每个内核单独写探针 |
| 6 | 占位密钥检测逻辑已存在且已收口 | `src/security/jwt_handler.py:618` `_PLACEHOLDER_SECRETS`；`:624` `_configured_secret()` | 生产配置预检应**复用**它，不另造一套 |

**核心判断**：Phase 7a 不是"新建一整套监控"，而是 **① 把孤儿导出函数接上一个真实端点；② 把 14 内核纳入就绪探针；③ 把已经存在的占位密钥语义提升为「启动前预检」**。

## 2. 交付物

### 2.1 新增 `src/observability/production.py`（纯逻辑，无 HTTP、无副作用）

| 符号 | 职责 |
|------|------|
| `ProductionError(RuntimeError)` | 生产能力无法诚实提供时抛出 |
| `ProductionConfigError(ProductionError)` | 严格模式下配置预检失败 |
| `ConfigFinding` (dataclass) | `key` / `severity`(critical\|warning) / `code` / `message` |
| `PreflightReport` (dataclass) | `findings` + `ok`（无 critical）+ `to_dict()` |
| `validate_production_config(env=None, strict=False)` | 生产配置预检，**只返回键名与代码，永不返回值本身** |
| `SubsystemStatus` / `SubsystemReport` (dataclass) | 逐内核状态 + 汇总 |
| `check_subsystems()` | 逐内核探针，**永不抛异常** |
| `prometheus_available()` / `prometheus_response()` | 包装孤儿 `generate_metrics()`，不可用时抛 `ProductionError` |

**探针语义（关键设计）** —— 三态，不混淆：

- `healthy`：getter 可导入、可实例化，且 `stats()` 可调用
- `degraded`：可实例化但 `stats()` 失败（核心仍可服务，暴露问题但不阻断）
- `unavailable`：导入或实例化失败（OS 真跑不起来）

`ready = 不存在 unavailable`；HTTP 层只在存在 `unavailable` 时返回 503。**这是刻意的**：把"内核构造不出来"（致命）与"统计数据取不到"（可降级）分开，避免探针变成"一有噪音就全场 503"从而被运维关掉。

### 2.2 新增 `src/gateway/observability.py`（路由）

| 端点 | 鉴权 | 说明 |
|------|------|------|
| `GET /v1/metrics/prometheus` | **公开** | Prometheus 文本格式，接孤儿 `generate_metrics()`；`LIUHAO_PROMETHEUS_METRICS=0` 时 404 关闭 |
| `GET /v1/ready/subsystems` | **公开** | 14 内核逐条就绪；有 `unavailable` → 503。与既有 `/v1/ready` **并存不替换** |
| `GET /v1/production/preflight` | **挂闸门** | 配置预检。**必须鉴权** —— "SECRET_KEY 是占位符"这类信息对攻击者价值极高 |

**为什么不改 `/v1/ready` 与 `/v1/metrics`**：`tests/test_gateway_health_wiring.py` 对它们有精确断言（含 `/v1/metrics` 字段真实性）。新增独立端点 = **零回归风险**，且语义更清晰（liveness / readiness / metrics / config-audit 各司其职）。聚合它们的工作留给上层（Prometheus 或 k8s 探针配置）。

## 3. 复用的既有资产（不重造）

- `src/observability/metrics.py` 的 `REGISTRY` / `generate_metrics()` —— 直接用。
- `src/kernels/*/__init__.py` 的 `get_*()` —— 12 个内核 getter，按名动态导入。
- `src/security/jwt_handler.py` 的 `_PLACEHOLDER_SECRETS` —— 复用；本地保留一份镜像常量并在测试中断言**两份必须相等**（防漂移）。
- `src/gateway/policy.py` 的 `require_human_principal` —— 预检端点的闸门（已确认 `policy.py` 不反向依赖 main/observability，无环）。

## 4. 默认零行为变化

- 不改 `health.py`、不改 `main.py` 既有路由、不改任何内核。
- 新增 router 以 `include_router` 追加，与既有路由无命名冲突（`/v1/metrics/prometheus` ⊃ `/v1/metrics` 是不同路径）。
- 新增模块在无 prometheus_client 环境下**诚实报错**（503 + 原因），不静默返回空串。

## 5. 风险与回滚

- 回滚 = 删 3 个新文件 + 撤销 `main.py` 中 1 处 `include_router`（2 行）。
- 唯一触碰既有文件处：`src/gateway/main.py` 追加 2 行 include。其余全新增。

## 6. 诚实边界（明确不做）

- **不做** Postgres 真连接迁移（`SqliteBackend` 之外的 Postgres 后端仍是 fail-closed 占位，见 Phase 4）。
- **不做** OpenTelemetry 导出器接线。
- Redis / 异步队列的**真接线已于 Phase 7b 完成**，见 `docs/MESSAGE-BUS-BACKEND-DESIGN.md`
  （`src/distribution/msg_bus.py` 的 redis 分支不再是占位）。
- 预检的 `sqlite_in_production` 是**静态启发式**（看 `APP_ENV` + `DATABASE_URL` 前缀），不能替代真实连接测试。
- 预检只查**已知**的四类风险项，不是完整的安全审计。
