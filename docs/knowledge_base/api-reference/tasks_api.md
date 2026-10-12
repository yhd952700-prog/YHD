# 任务 API 参考

本 API 提供任务管理接口，包括任务创建、查询、取消、结果获取等功能。

---

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/v1/tasks` | 列出任务 |
| `POST` | `/v1/tasks` | 创建任务 |
| `GET` | `/v1/tasks/{id}` | 获取任务详情 |
| `DELETE` | `/v1/tasks/{id}` | 取消任务 |
| `GET` | `/v1/tasks/{id}/result` | 获取任务结果 |
| `GET` | `/v1/tasks/{id}/steps` | 获取任务步骤 |
| `GET` | `/v1/tasks/{id}/logs` | 获取任务日志 |
| `POST` | `/v1/tasks/{id}/retry` | 重试任务 |
| `GET` | `/v1/tasks/{id}/children` | 获取子任务 |

---

## 任务状态

| 状态 | 说明 |
|------|------|
| `pending` | 等待执行 |
| `queued` | 已入队，等待调度 |
| `running` | 正在执行 |
| `paused` | 已暂停 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `cancelled` | 已取消 |
| `timeout` | 执行超时 |

---

## 列出任务

### 请求

```
GET /v1/tasks?status=running&agent_id=agent-abc123&limit=20&offset=0
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `status` | string | - | 按状态筛选 |
| `agent_id` | string | - | 按智能体筛选 |
| `type` | string | - | 按任务类型筛选 |
| `created_from` | string | - | 创建时间起始（ISO 8601） |
| `created_to` | string | - | 创建时间截止（ISO 8601） |
| `limit` | int | 20 | 每页数量 |
| `offset` | int | 0 | 偏移量 |
| `sort_by` | string | created_at | 排序字段 |
| `sort_order` | string | desc | 排序方向 |

### 响应（200 OK）

```json
{
  "total": 89,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "id": "task-abc123",
      "name": "分析 Q3 销售数据",
      "type": "analysis",
      "agent_id": "agent-xyz789",
      "status": "running",
      "progress": 65,
      "priority": "high",
      "created_at": "2026-10-07T11:30:00Z",
      "started_at": "2026-10-07T11:30:05Z",
      "estimated_completion": "2026-10-07T11:45:00Z"
    }
  ]
}
```

---

## 创建任务

### 请求

```
POST /v1/tasks
Content-Type: application/json
```

```json
{
  "name": "生成季度报告",
  "description": "根据 Q3 数据生成完整的季度业务报告",
  "agent_id": "agent-abc123",
  "type": "report_generation",
  "priority": "high",
  "input": {
    "quarter": "Q3",
    "year": 2026,
    "format": "markdown"
  },
  "options": {
    "timeout_seconds": 1800,
    "max_retries": 2,
    "callback_url": "https://example.com/webhook/task-done"
  }
}
```

### 响应（201 Created）

```json
{
  "id": "task-def456",
  "name": "生成季度报告",
  "status": "pending",
  "agent_id": "agent-abc123",
  "type": "report_generation",
  "priority": "high",
  "progress": 0,
  "created_at": "2026-10-07T12:00:00Z"
}
```

---

## 获取任务详情

### 请求

```
GET /v1/tasks/{task_id}
```

### 响应（200 OK）

```json
{
  "id": "task-abc123",
  "name": "分析 Q3 销售数据",
  "description": "分析 Q3 销售数据，找出增长趋势",
  "agent_id": "agent-xyz789",
  "status": "running",
  "type": "analysis",
  "priority": "high",
  "progress": 65,
  "input": {
    "quarter": "Q3",
    "data_source": "sales_db"
  },
  "result": null,
  "error": null,
  "created_at": "2026-10-07T11:30:00Z",
  "started_at": "2026-10-07T11:30:05Z",
  "completed_at": null,
  "duration_seconds": 1200,
  "current_step": "数据可视化",
  "steps_completed": 3,
  "total_steps": 5,
  "metrics": {
    "tokens_used": 15600,
    "tool_calls": 8,
    "cost": 0.045
  },
  "parent_task_id": null,
  "child_task_count": 2
}
```

---

## 获取任务结果

### 请求

```
GET /v1/tasks/{task_id}/result
```

### 响应（200 OK）

```json
{
  "task_id": "task-abc123",
  "status": "completed",
  "output": {
    "summary": "Q3 销售总额同比增长 25%...",
    "highlights": [
      "产品 A 增长最快，达到 45%",
      "华东地区贡献最大份额"
    ],
    "charts": [
      {"type": "bar", "data_url": "/charts/task-abc123/sales-by-product.png"}
    ],
    "full_report": "..."
  },
  "metrics": {
    "total_tokens": 32000,
    "total_duration_seconds": 450,
    "tool_calls": 12
  },
  "completed_at": "2026-10-07T11:45:00Z"
}
```

---

## 获取任务步骤

### 请求

```
GET /v1/tasks/{task_id}/steps
```

### 响应（200 OK）

```json
{
  "task_id": "task-abc123",
  "total_steps": 5,
  "steps": [
    {
      "step": 1,
      "name": "数据获取",
      "status": "completed",
      "started_at": "2026-10-07T11:30:05Z",
      "completed_at": "2026-10-07T11:32:00Z",
      "duration_seconds": 115
    },
    {
      "step": 2,
      "name": "数据清洗",
      "status": "completed",
      "started_at": "2026-10-07T11:32:00Z",
      "completed_at": "2026-10-07T11:35:00Z",
      "duration_seconds": 180
    },
    {
      "step": 3,
      "name": "数据分析",
      "status": "completed",
      "started_at": "2026-10-07T11:35:00Z",
      "completed_at": "2026-10-07T11:42:00Z",
      "duration_seconds": 420
    },
    {
      "step": 4,
      "name": "数据可视化",
      "status": "running",
      "started_at": "2026-10-07T11:42:00Z",
      "completed_at": null,
      "duration_seconds": 180
    },
    {
      "step": 5,
      "name": "报告生成",
      "status": "pending",
      "started_at": null,
      "completed_at": null,
      "duration_seconds": 0
    }
  ]
}
```

---

## 取消任务

### 请求

```
DELETE /v1/tasks/{task_id}
```

### 响应（200 OK）

```json
{
  "task_id": "task-abc123",
  "status": "cancelled",
  "message": "任务已取消"
}
```

---

## 重试任务

### 请求

```
POST /v1/tasks/{task_id}/retry
```

### 响应（200 OK）

```json
{
  "task_id": "task-abc123",
  "status": "pending",
  "retry_count": 1,
  "message": "任务已重新排队"
}
```

---

## 错误码

| HTTP 状态码 | 错误码 | 说明 |
|-------------|--------|------|
| 400 | `INVALID_REQUEST` | 请求参数无效 |
| 401 | `UNAUTHORIZED` | 未认证 |
| 403 | `FORBIDDEN` | 无权限 |
| 404 | `TASK_NOT_FOUND` | 任务不存在 |
| 409 | `INVALID_STATUS` | 任务状态不允许该操作 |
| 429 | `RATE_LIMITED` | 触发速率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |

---

## 下一步

- [智能体 API](agents_api.md) - 智能体管理 API
- [健康 API](health_api.md) - 健康检查 API
