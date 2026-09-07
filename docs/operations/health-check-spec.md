# LiuHao AI OS 健康检查规范

## 端点规范

### `/health` - 存活探测
- **方法**：GET
- **返回**：`{"status": "ok"}` + HTTP 200
- **目的**：Kubernetes 存活探测，检查容器是否运行正常

### `/ready` - 就绪探测
- **方法**：GET
- **返回**：`{"status": "ok", "checks": [{"name": "<service>", "ok": true/false, ...}]}` + HTTP 200/503
- **目的**：Kubernetes 就绪探测，检查所有依赖服务是否就绪

## 必须检查的服务

| 检查项 | 依赖 | 条件 |
|--------|------|------|
| PostgreSQL | `POSTGRES_URL` | DB 连通，`SELECT 1` |
| Redis | `REDIS_URL` | PING 响应 < 1ms |
| Qdrant | `QDRANT_URL` | `/collections` 返回 200 |
| etcd | `ETCD_URL` | `etcdctl endpoint health` 全绿 |

## 示例响应

```json
{
  "status": "ok",
  "checks": [
    {"name": "postgres", "ok": true, "latency_ms": 1.2},
    {"name": "redis", "ok": true, "latency_ms": 0.5},
    {"name": "qdrant", "ok": true, "latency_ms": 2.1},
    {"name": "etcd", "ok": true, "latency_ms": 3.4}
  ]
}
```

## 就绪探测失败时的行为

- 任意检查 `ok: false` → `/ready` 返回 HTTP 503
- 所有检查通过 → `/ready` 返回 HTTP 200
- K8s Probe: `failureThreshold: 3, initialDelaySeconds: 5, periodSeconds: 10`
