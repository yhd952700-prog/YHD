# 健康 API 参考

本 API 提供系统健康检查和就绪检查接口，用于监控和运维。

---

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/v1/health` | 健康检查 |
| `GET` | `/v1/ready` | 就绪检查 |
| `GET` | `/v1/health/details` | 详细健康状态 |
| `GET` | `/v1/health/components` | 组件健康状态 |
| `GET` | `/v1/health/metrics` | 健康指标 |

---

## 健康检查

快速检查系统是否在运行。

### 请求

```
GET /v1/health
```

### 响应（200 OK）

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "timestamp": "2026-10-07T12:00:00Z",
  "uptime_seconds": 86400
}
```

### 状态说明

| 状态 | HTTP 状态码 | 说明 |
|------|------------|------|
| `healthy` | 200 | 系统运行正常 |
| `degraded` | 200 | 系统可运行但部分功能降级 |
| `unhealthy` | 503 | 系统不健康，无法正常服务 |

---

## 就绪检查

检查系统是否已准备好接收流量。

### 请求

```
GET /v1/ready
```

### 响应（200 OK）

```json
{
  "ready": true,
  "version": "3.0.0",
  "checks": {
    "database": "ok",
    "model_gateway": "ok",
    "memory_kernel": "ok",
    "identity_kernel": "ok",
    "event_bus": "ok",
    "embedding_service": "ok"
  },
  "timestamp": "2026-10-07T12:00:00Z"
}
```

### 未就绪响应（503 Service Unavailable）

```json
{
  "ready": false,
  "version": "3.0.0",
  "checks": {
    "database": "ok",
    "model_gateway": "degraded",
    "memory_kernel": "ok",
    "identity_kernel": "ok",
    "event_bus": "error",
    "embedding_service": "pending"
  },
  "message": "事件总线连接失败，模型网关降级",
  "timestamp": "2026-10-07T12:00:00Z"
}
```

### 检查状态说明

| 状态 | 说明 |
|------|------|
| `ok` | 正常 |
| `degraded` | 降级运行（功能受限但可用） |
| `error` | 错误（不可用） |
| `pending` | 初始化中 |
| `unknown` | 状态未知 |

---

## 详细健康状态

获取更详细的系统健康信息。

### 请求

```
GET /v1/health/details
```

### 响应（200 OK）

```json
{
  "status": "healthy",
  "version": "3.0.0",
  "build": {
    "commit": "abc123def",
    "date": "2026-10-01T10:00:00Z",
    "branch": "main"
  },
  "uptime": {
    "seconds": 86400,
    "human": "1 day, 0:00:00"
  },
  "system": {
    "cpu_usage": 0.25,
    "memory_usage": 0.45,
    "memory_total_mb": 8192,
    "memory_used_mb": 3686,
    "disk_usage": 0.3,
    "disk_total_gb": 100,
    "disk_used_gb": 30
  },
  "services": {
    "api_gateway": "ok",
    "agent_runtime": "ok",
    "memory_system": "ok",
    "knowledge_base": "ok",
    "plugin_manager": "ok"
  },
  "timestamp": "2026-10-07T12:00:00Z"
}
```

---

## 组件健康状态

获取各内核和组件的详细健康状态。

### 请求

```
GET /v1/health/components
```

### 响应（200 OK）

```json
{
  "status": "healthy",
  "components": {
    "kernels": {
      "identity": {"status": "ok", "latency_ms": 2},
      "memory": {"status": "ok", "latency_ms": 5, "entries": 12345},
      "context": {"status": "ok", "latency_ms": 1},
      "capability": {"status": "ok", "latency_ms": 3, "registered": 87},
      "policy": {"status": "ok", "latency_ms": 2, "policies": 45},
      "execution": {"status": "ok", "latency_ms": 10, "running_tasks": 3},
      "resource": {"status": "ok", "latency_ms": 1},
      "event": {"status": "ok", "latency_ms": 2, "queue_size": 15},
      "network": {"status": "ok", "latency_ms": 5},
      "trust": {"status": "ok", "latency_ms": 1},
      "evaluation": {"status": "ok", "latency_ms": 3},
      "security": {"status": "ok", "latency_ms": 2},
      "audit": {"status": "ok", "latency_ms": 4, "chain_length": 54321},
      "plugin": {"status": "ok", "latency_ms": 3, "loaded": 12}
    },
    "database": {
      "status": "ok",
      "type": "postgresql",
      "connections": {
        "active": 12,
        "idle": 8,
        "total": 20,
        "max": 100
      },
      "latency_ms": 3
    },
    "model_gateway": {
      "status": "ok",
      "providers": {
        "ollama": {"status": "ok", "models": 3, "latency_ms": 45},
        "openai_compatible": {"status": "ok", "models": 5, "latency_ms": 120}
      }
    }
  },
  "timestamp": "2026-10-07T12:00:00Z"
}
```

---

## 健康指标

获取系统运行的关键指标（Prometheus 格式也可用 `/metrics` 端点）。

### 请求

```
GET /v1/health/metrics
```

### 响应（200 OK）

```json
{
  "api": {
    "requests_total": 125000,
    "requests_per_second": 15.2,
    "error_rate": 0.003,
    "avg_latency_ms": 125,
    "p95_latency_ms": 350,
    "p99_latency_ms": 800
  },
  "agents": {
    "total": 156,
    "active": 23,
    "idle": 133,
    "tasks_running": 8
  },
  "memory": {
    "total_entries": 12345,
    "semantic": 8500,
    "episodic": 3500,
    "working": 345
  },
  "tasks": {
    "total_today": 234,
    "completed_today": 220,
    "failed_today": 5,
    "success_rate": 0.978
  },
  "cost": {
    "tokens_today": 5234000,
    "cost_today": 15.23,
    "tokens_this_month": 125000000,
    "cost_this_month": 356.78
  },
  "timestamp": "2026-10-07T12:00:00Z"
}
```

---

## 使用场景

### Kubernetes 健康探针

```yaml
# livenessProbe
livenessProbe:
  httpGet:
    path: /v1/health
    port: 8080
  initialDelaySeconds: 15
  periodSeconds: 20

# readinessProbe
readinessProbe:
  httpGet:
    path: /v1/ready
    port: 8080
  initialDelaySeconds: 5
  periodSeconds: 10
```

### 监控告警

建议监控的关键指标：
- `/v1/health` 返回非 200 状态码
- `/v1/ready` 返回 `ready: false`
- 错误率超过 1%
- P99 延迟超过 2 秒
- 数据库连接池耗尽

---

## 下一步

- [智能体 API](agents_api.md) - 智能体管理 API
- [故障排除](../troubleshooting/common_issues.md) - 常见问题排查
