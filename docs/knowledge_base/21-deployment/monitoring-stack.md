# Prometheus + Grafana 监控栈

可观测性是智能体系统的生命线：LLM 调用的成本、延迟、成功率，智能体的任务吞吐，内核间的消息流动，都需要实时可见。本文档提供 LiuHao AI OS 推荐的 Prometheus + Grafana 监控栈完整配置，包括指标采集、告警规则、仪表盘设计与智能体特有的监控维度。

---

## 目录

- [监控架构](#监控架构)
- [Prometheus 配置](#prometheus-配置)
- [应用埋点规范](#应用埋点规范)
- [核心指标清单](#核心指标清单)
- [告警规则](#告警规则)
- [Grafana 仪表盘](#grafana-仪表盘)
- [日志与追踪](#日志与追踪)
- [最佳实践](#最佳实践)
- [常见问题](#常见问题)

---

## 监控架构

```
应用 (api-gateway / agent-runtime / kernels)
   │  /metrics (Prometheus 格式)
   ▼
Prometheus ──▶ Alertmanager ──▶ Slack/PagerDuty/飞书
   │
   ▼
Grafana（仪表盘 + 探索）

日志: 应用 stdout ──▶ 节点采集 (fluentbit) ──▶ Loki
追踪: OTel SDK ──▶ OTel Collector ──▶ Tempo/Jaeger
```

三大支柱分工：

| 支柱 | 回答的问题 | 工具 |
|------|-----------|------|
| Metrics | 系统整体健康吗？趋势如何？ | Prometheus + Grafana |
| Logs | 这个具体请求发生了什么？ | Loki + Grafana |
| Traces | 请求在哪个环节变慢/失败？ | Tempo + OTel |

---

## Prometheus 配置

### prometheus.yml（K8s 环境用 ServiceMonitor 更佳，此处为通用配置）

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s
  external_labels:
    cluster: liuhao-prod
    env: production

rule_files:
  - /etc/prometheus/rules/*.yaml

alerting:
  alertmanagers:
    - static_configs:
        - targets: [alertmanager:9093]

scrape_configs:
  - job_name: liuhao-pods
    kubernetes_sd_configs:
      - role: pod
        namespaces: { names: [liuhao] }
    relabel_configs:
      # 只抓带注解的 Pod
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_scrape]
        action: keep
        regex: "true"
      - source_labels: [__meta_kubernetes_pod_annotation_prometheus_io_port]
        action: replace
        target_label: __address__
        regex: (.+)
        replacement: $1
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: replace
        target_label: app

  - job_name: postgres-exporter
    static_configs:
      - targets: [postgres-exporter:9187]

  - job_name: redis-exporter
    static_configs:
      - targets: [redis-exporter:9121]

storage:
  tsdb:
    retention.time: 30d           # 本地保留 30 天
    # 长期存储用 Thanos/Mimir 远端写
```

### 资源估算

```
Prometheus 内存 ≈ 活跃序列数 × 3~5 KB
10 万序列 ≈ 500MB；100 万序列 ≈ 5GB
```

控制基数是第一要务（见最佳实践）。

---

## 应用埋点规范

### Python（prometheus-client）

```python
from prometheus_client import Counter, Histogram, Gauge, start_http_server

# 计数器：只增不减，算速率
LLM_CALLS = Counter(
    "liuhao_llm_calls_total",
    "LLM API 调用总数",
    ["provider", "model", "result"],       # result: ok|error|timeout
)

# 直方图：延迟分布（P50/P95/P99）
LLM_LATENCY = Histogram(
    "liuhao_llm_call_duration_seconds",
    "LLM 调用延迟",
    ["provider", "model"],
    buckets=(0.5, 1, 2, 5, 10, 30, 60, 120),
)

# 成本追踪（智能体系统特有）
LLM_TOKENS = Counter(
    "liuhao_llm_tokens_total",
    "LLM token 消耗",
    ["provider", "model", "kind"],         # kind: input|output
)

# 仪表盘瞬时值
AGENTS_ACTIVE = Gauge(
    "liuhao_agents_active",
    "当前活跃智能体数",
    ["runtime_pod"],
)

def call_llm(provider: str, model: str, prompt: str):
    start = time.monotonic()
    try:
        resp = gateway.complete(model=model, prompt=prompt)
        LLM_CALLS.labels(provider, model, "ok").inc()
        LLM_TOKENS.labels(provider, model, "input").inc(resp.usage.input)
        LLM_TOKENS.labels(provider, model, "output").inc(resp.usage.output)
        return resp
    except TimeoutError:
        LLM_CALLS.labels(provider, model, "timeout").inc()
        raise
    except Exception:
        LLM_CALLS.labels(provider, model, "error").inc()
        raise
    finally:
        LLM_LATENCY.labels(provider, model).observe(time.monotonic() - start)
```

### 标签规范（防基数爆炸）

```
✅ 低基数标签：provider（~5个）、model（~20个）、result（3个）、status_code 分组
❌ 高基数标签：user_id、agent_id、request_id、prompt 内容 —— 这些放日志和追踪
```

红线：任何标签的取值数 > 100 都要评审。

---

## 核心指标清单

### 黄金信号（每个服务必备）

| 信号 | 指标 | 查询示例 |
|------|------|---------|
| 流量 | `http_requests_total` | `rate(http_requests_total[5m])` |
| 错误 | 同上按 status 过滤 | 5xx 速率 / 总速率 |
| 延迟 | `http_request_duration_seconds` | `histogram_quantile(0.99, ...)` |
| 饱和度 | CPU/内存/连接池占用率 | `process_resident_memory_bytes / limit` |

### 智能体系统特有指标

| 指标 | 类型 | 说明 |
|------|------|------|
| `liuhao_agents_active` | Gauge | 活跃智能体数（按 runtime） |
| `liuhao_tasks_completed_total{result}` | Counter | 任务完成/失败/超时 |
| `liuhao_task_duration_seconds` | Histogram | 任务端到端耗时 |
| `liuhao_llm_tokens_total{kind}` | Counter | token 消耗（成本） |
| `liuhao_llm_calls_total{result}` | Counter | LLM 调用成功率 |
| `liuhao_memory_operations_total{layer,op}` | Counter | 四层记忆读写 |
| `liuhao_saga_active` / `liuhao_saga_compensated_total` | Gauge/Counter | Saga 健康 |
| `liuhao_circuit_breaker_state{circuit}` | Gauge | 熔断器状态 0/1/2 |
| `liuhao_policy_denials_total{policy}` | Counter | 策略拒绝（安全信号） |
| `liuhao_bus_lag_seconds{topic}` | Gauge | 消息总线消费延迟 |

### 成本看板核心 PromQL

```promql
# 每小时 LLM 成本（按 token 单价表）
sum by (model) (
  increase(liuhao_llm_tokens_total{kind="input"}[1h])
) * on(model) group_left liuhao_model_input_price
+
sum by (model) (
  increase(liuhao_llm_tokens_total{kind="output"}[1h])
) * on(model) group_left liuhao_model_output_price
```

---

## 告警规则

### /etc/prometheus/rules/liuhao.yaml

```yaml
groups:
  - name: availability
    rules:
      - alert: HighErrorRate
        expr: |
          sum(rate(http_requests_total{status=~"5.."}[5m]))
            /
          sum(rate(http_requests_total[5m])) > 0.01
        for: 5m
        labels: { severity: critical, team: platform }
        annotations:
          summary: "错误率超过 1%（5 分钟窗口）"
          runbook: https://wiki.example.com/runbooks/high-error-rate

      - alert: APIGatewayDown
        expr: up{job="liuhao-pods", app="api-gateway"} == 0
        for: 1m
        labels: { severity: critical }
        annotations:
          summary: "api-gateway 实例全部失联"

  - name: llm
    rules:
      - alert: LLMProviderFailureStorm
        expr: |
          sum by (provider) (
            rate(liuhao_llm_calls_total{result!="ok"}[5m])
          ) > 5
        for: 5m
        labels: { severity: warning }
        annotations:
          summary: "{{ $labels.provider }} 每分钟失败调用 > 5"

      - alert: LLMCostBudgetBurn
        expr: |
          sum(increase(liuhao_llm_tokens_total[1h]))
            > on() group_left() (liuhao_daily_token_budget / 24 * 3)
        for: 1h
        labels: { severity: warning }
        annotations:
          summary: "LLM 消耗速度超过日预算 3 倍均速"

  - name: agent_runtime
    rules:
      - alert: SagaCompensationStorm
        expr: rate(liuhao_saga_compensated_total[10m]) > 0.1
        for: 10m
        labels: { severity: critical }
        annotations:
          summary: "Saga 补偿频率异常，可能存在系统性故障"

      - alert: BusConsumerLag
        expr: liuhao_bus_lag_seconds > 60
        for: 10m
        labels: { severity: warning }
        annotations:
          summary: "消息总线 {{ $labels.topic }} 消费延迟超 60s"

  - name: resources
    rules:
      - alert: PostgresConnectionsExhausted
        expr: |
          pg_stat_activity_count / pg_settings_max_connections > 0.85
        for: 5m
        labels: { severity: warning }

      - alert: PodRestartStorm
        expr: |
          increase(kube_pod_container_status_restarts_total{namespace="liuhao"}[1h]) > 5
        for: 15m
        labels: { severity: warning }
```

### Alertmanager 路由

```yaml
route:
  receiver: slack-default
  group_by: [alertname, severity]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - match: { severity: critical }
      receiver: pagerduty
      continue: true               # 同时发 slack 留痕
    - match: { team: platform }
      receiver: slack-platform

receivers:
  - name: slack-default
    slack_configs:
      - channel: "#liuhao-alerts"
        api_url: ${SLACK_WEBHOOK}
  - name: pagerduty
    pagerduty_configs:
      - service_key: ${PD_SERVICE_KEY}
```

---

## Grafana 仪表盘

### 推荐仪表盘布局

**1. 总览（Overview）** —— 一屏看全局：

- 上行：流量、错误率、P99、活跃智能体（大数字 SingleStat）
- 中行：LLM 调用成功率 × 提供商（堆叠图）、任务吞吐
- 下行：成本燃烧速率、预算水位、熔断器状态矩阵

**2. LLM 深度** —— 成本与性能：

- 按 model 的 QPS / 延迟热图 / token 消耗
- 失败原因分布（timeout vs 429 vs 5xx）
- 降级链触发次数（fallback 到第几级）

**3. 智能体运行时**：

- 每 Pod 承载智能体数、任务队列深度
- Saga 进行中/补偿中（超过 10 分钟的高亮）
- 记忆操作速率按层（working/episodic/semantic/procedural）

### Dashboard JSON 片段（变量与面板）

```json
{
  "templating": {
    "list": [
      {
        "name": "namespace",
        "type": "query",
        "query": "label_values(liuhao_llm_calls_total, namespace)"
      },
      {
        "name": "model",
        "type": "query",
        "query": "label_values(liuhao_llm_calls_total{namespace=\"$namespace\"}, model)",
        "multi": true,
        "includeAll": true
      }
    ]
  },
  "panels": [
    {
      "title": "LLM P99 Latency by Model",
      "type": "timeseries",
      "targets": [
        {
          "expr": "histogram_quantile(0.99, sum by (model, le) (rate(liuhao_llm_call_duration_seconds_bucket{model=~\"$model\"}[5m])))"
        }
      ]
    }
  ]
}
```

> 仪表盘用 provisioning 以代码管理（`deploy/grafana/dashboards/*.json`），禁止手工改线上。

---

## 日志与追踪

### 结构化日志（JSON）

```python
import structlog

log = structlog.get_logger()
log.info("llm_call_completed",
         provider="provider-a", model="gpt-5",
         latency_ms=1830, input_tokens=512, output_tokens=128,
         trace_id=current_trace_id(),      # 与追踪关联的关键
         agent_id="agent_42", task_id="task_99")
```

### Loki 查询（LogQL）

```logql
{app="agent-runtime"} |= "llm_call_completed"
  | json | latency_ms > 10000
  | line_format "{{.trace_id}} {{.model}} {{.latency_ms}}ms"
```

### OpenTelemetry 追踪

```python
from opentelemetry import trace
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

tracer = trace.get_tracer("liuhao.agent")
HTTPXClientInstrumentor().instrument()

async def execute_task(task):
    with tracer.start_as_current_span("task.execute") as span:
        span.set_attribute("task.id", task.id)
        span.set_attribute("agent.id", task.agent_id)
        with tracer.start_as_current_span("llm.plan"):
            plan = await planner.plan(task)
        with tracer.start_as_current_span("memory.retrieve"):
            context = await memory.retrieve(plan.query)
        ...
```

trace_id 贯穿日志，Grafana 中可从日志一键跳到 Trace 视图，形成排障闭环。

---

## 最佳实践

1. **先指标后告警**：每个告警都对应一个可看趋势的指标面板，否则告警来了没有上下文。
2. **告警必须可行动**：每条告警写 runbook 链接；"信息性"告警降级到周报，不打扰值班。
3. **控制指标基数**：上线前 `count({__name__=~".+"}) by (__name__)` 评审，标签取值 >100 需审批。
4. **成本指标一等公民**：LLM 成本燃烧速率与错误率同等重要，预算超支要告警。
5. **仪表盘即代码**：provisioning 管理，PR 评审，禁止线上手改。
6. **trace_id 贯穿三支柱**：指标 exemplar → 日志 → 追踪，排障路径最短化。
7. **保留期分层**：Prometheus 本地 30d 高精度，Thanos 远端 1 年降采样，成本与查询需求平衡。

---

## 常见问题

### Q1: Prometheus 内存爆了怎么排查？

`http://prometheus:9090/tsdb-status` 看 Head Stats。Top 高基数指标逐一评审：删掉高基数标签、加 recording rule 预聚合、或迁移该指标到日志。

### Q2: 智能体粒度的指标（per-agent）怎么做？

不要做成 Prometheus 标签（基数爆炸）。方案：per-agent 状态写 Redis/PG，仪表盘用 business 数据源查询；或只在日志/追踪中带 agent_id。

### Q3: 告警太多大家麻木了（alert fatigue）？

审计现有告警：过去 30 天触发 >3 次且无需行动的告警——修根因或删除。目标：值班每周被叫醒 ≤ 2 次，且每次都是真问题。

### Q4: 多集群监控怎么统一？

Thanos sidecar + 全局 Query 层，或 VictoriaMetrics 集群版。Grafana 加 `cluster` 变量切换/聚合。

### Q5: 本地开发也要起整套吗？

不必。Docker Compose 的 `observability` profile（见 [Docker Compose 配置](docker-compose-setup.md)）提供轻量 Prometheus + Grafana；追踪可用 Jaeger all-in-one 单容器。

---

**相关文档**：[Kubernetes 部署](kubernetes-deployment.md) · [CI/CD 流水线](ci-cd-pipeline.md) · [性能调优](../23-performance/performance-tuning.md) · [审计日志](../24-security/audit-logging.md)
