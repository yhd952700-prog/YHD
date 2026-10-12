# 智能体 API 参考

本 API 提供智能体的管理和操作接口，包括创建、配置、执行任务、监控等功能。

---

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/v1/agents` | 列出智能体 |
| `POST` | `/v1/agents` | 创建智能体 |
| `GET` | `/v1/agents/{id}` | 获取智能体详情 |
| `PUT` | `/v1/agents/{id}` | 更新智能体 |
| `DELETE` | `/v1/agents/{id}` | 删除智能体 |
| `POST` | `/v1/agents/{id}/execute` | 执行任务 |
| `GET` | `/v1/agents/{id}/status` | 获取运行状态 |
| `POST` | `/v1/agents/{id}/stop` | 停止运行 |
| `POST` | `/v1/agents/{id}/pause` | 暂停 |
| `POST` | `/v1/agents/{id}/resume` | 恢复 |
| `GET` | `/v1/agents/{id}/metrics` | 获取指标 |
| `GET` | `/v1/agents/{id}/logs` | 获取日志 |
| `GET` | `/v1/agents/{id}/tools` | 列出可用工具 |
| `POST` | `/v1/agents/{id}/clone` | 克隆智能体 |
| `GET` | `/v1/agent-templates` | 列出模板 |

---

## 列出智能体

获取智能体列表，支持分页、筛选和排序。

### 请求

```
GET /v1/agents?limit=20&offset=0&status=idle&role=analyst&sort_by=created_at&sort_order=desc
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `limit` | int | 20 | 每页数量，最大 100 |
| `offset` | int | 0 | 偏移量 |
| `status` | string | - | 按状态筛选（idle/running/paused/error） |
| `role` | string | - | 按角色筛选 |
| `search` | string | - | 名称/描述搜索 |
| `sort_by` | string | created_at | 排序字段 |
| `sort_order` | string | desc | 排序方向（asc/desc） |

### 响应

```json
{
  "total": 42,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "id": "agent-abc123",
      "name": "数据分析助手",
      "role": "analyst",
      "status": "idle",
      "description": "专业的数据分析师",
      "created_at": "2026-10-01T10:00:00Z",
      "updated_at": "2026-10-05T14:30:00Z",
      "tasks_completed": 156
    }
  ]
}
```

---

## 创建智能体

创建一个新的智能体。

### 请求

```
POST /v1/agents
Content-Type: application/json
```

```json
{
  "name": "研究助手",
  "role": "researcher",
  "description": "专业研究分析师，擅长信息收集和分析",
  "config": {
    "model": "qwen2.5:7b",
    "temperature": 0.7,
    "max_tokens": 4096,
    "system_prompt": "你是一名专业的研究分析师...",
    "tools": ["web_search", "file_reader"],
    "memory": {
      "enabled": true,
      "semantic_memory_enabled": true
    }
  }
}
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 智能体名称，1-100 字符 |
| `role` | string | 是 | 角色类型 |
| `description` | string | 否 | 描述，最多 500 字符 |
| `config` | object | 否 | 配置对象 |
| `config.model` | string | 否 | LLM 模型，默认 `qwen2.5:3b` |
| `config.temperature` | float | 否 | 采样温度 0-2，默认 0.7 |
| `config.max_tokens` | int | 否 | 最大生成长度，默认 2048 |
| `config.system_prompt` | string | 否 | 系统提示词 |
| `config.tools` | array | 否 | 可用工具列表 |

### 响应（201 Created）

```json
{
  "id": "agent-def456",
  "name": "研究助手",
  "role": "researcher",
  "status": "idle",
  "description": "专业研究分析师，擅长信息收集和分析",
  "created_at": "2026-10-07T12:00:00Z",
  "updated_at": "2026-10-07T12:00:00Z",
  "config": {
    "model": "qwen2.5:7b",
    "temperature": 0.7,
    "max_tokens": 4096
  }
}
```

---

## 获取智能体详情

获取指定智能体的详细信息。

### 请求

```
GET /v1/agents/{agent_id}
```

### 响应（200 OK）

```json
{
  "id": "agent-abc123",
  "name": "数据分析助手",
  "role": "analyst",
  "status": "running",
  "description": "专业的数据分析师",
  "created_at": "2026-10-01T10:00:00Z",
  "updated_at": "2026-10-07T12:00:00Z",
  "config": {
    "model": "qwen2.5:7b",
    "temperature": 0.3,
    "max_tokens": 4096
  },
  "stats": {
    "tasks_completed": 156,
    "tasks_failed": 3,
    "total_tokens_used": 523400,
    "total_runtime_seconds": 7200
  },
  "current_task": {
    "id": "task-xyz789",
    "name": "分析 Q3 销售数据",
    "progress": 65,
    "started_at": "2026-10-07T11:30:00Z"
  }
}
```

---

## 更新智能体

更新智能体的配置和信息。

### 请求

```
PUT /v1/agents/{agent_id}
Content-Type: application/json
```

```json
{
  "name": "高级数据分析助手",
  "description": "更新后的描述",
  "config": {
    "temperature": 0.5,
    "max_tokens": 8192
  },
  "status": "idle"
}
```

### 响应（200 OK）

返回更新后的智能体详情，格式同「获取智能体详情」。

---

## 删除智能体

删除指定的智能体。

### 请求

```
DELETE /v1/agents/{agent_id}?keep_memory=false
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `keep_memory` | bool | false | 是否保留智能体的记忆数据 |

### 响应（204 No Content）

无响应体。

---

## 执行任务

让智能体执行一个任务。

### 请求

```
POST /v1/agents/{agent_id}/execute
Content-Type: application/json
```

```json
{
  "task": "分析 Q3 销售数据，找出增长最快的产品和地区",
  "context": {
    "data_source": "sales_db",
    "time_range": "2026-Q3"
  },
  "options": {
    "stream": false,
    "max_steps": 20,
    "timeout_seconds": 300
  }
}
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `task` | string | 是 | 任务描述 |
| `context` | object | 否 | 任务上下文信息 |
| `options.stream` | bool | 否 | 是否流式输出，默认 false |
| `options.max_steps` | int | 否 | 最大执行步数，默认 50 |
| `options.timeout_seconds` | int | 否 | 超时时间（秒），默认 600 |

### 响应（200 OK）

```json
{
  "task_id": "task-abc123",
  "status": "completed",
  "output": "根据 Q3 销售数据分析...（完整的分析结果）",
  "steps": [
    {
      "step": 1,
      "type": "thought",
      "content": "我需要先获取销售数据..."
    },
    {
      "step": 2,
      "type": "tool_call",
      "tool": "database_query",
      "input": "SELECT * FROM sales WHERE ..."
    }
  ],
  "metrics": {
    "total_tokens": 3200,
    "duration_seconds": 45.2,
    "tool_calls": 3
  }
}
```

### 流式响应

当 `stream: true` 时，返回 Server-Sent Events (SSE) 流：

```
data: {"type": "thought", "content": "让我先理解任务要求...", "step": 1}

data: {"type": "tool_call", "tool": "web_search", "input": "...", "step": 2}

data: {"type": "text", "content": "根据", "step": 3}

data: {"type": "text", "content": "分析", "step": 3}

data: {"type": "complete", "task_id": "task-abc123", "status": "completed"}
```

---

## 停止运行中的任务

强制停止智能体当前正在执行的任务。

### 请求

```
POST /v1/agents/{agent_id}/stop
Content-Type: application/json
```

```json
{
  "force": false
}
```

### 响应（200 OK）

```json
{
  "stopped": true,
  "task_id": "task-abc123",
  "message": "任务已停止"
}
```

---

## 获取智能体指标

获取智能体的运行指标统计。

### 请求

```
GET /v1/agents/{agent_id}/metrics?period=7d
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `period` | string | 7d | 统计周期（1d/7d/30d/90d/all） |

### 响应（200 OK）

```json
{
  "period": "7d",
  "total_tasks": 42,
  "completed_tasks": 38,
  "failed_tasks": 4,
  "success_rate": 0.905,
  "avg_duration_seconds": 120.5,
  "total_tokens_used": 523400,
  "total_cost": 1.25,
  "tool_calls": 156,
  "daily_stats": [
    {
      "date": "2026-10-01",
      "tasks": 8,
      "tokens": 45000
    }
  ]
}
```

---

## 获取智能体日志

获取智能体的运行日志。

### 请求

```
GET /v1/agents/{agent_id}/logs?level=INFO&limit=100&since=1h
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `level` | string | INFO | 日志级别（DEBUG/INFO/WARNING/ERROR） |
| `limit` | int | 100 | 返回数量，最大 1000 |
| `since` | string | 1h | 时间范围（1h/24h/7d） |
| `task_id` | string | - | 按任务过滤 |

### 响应（200 OK）

```json
{
  "total": 256,
  "limit": 100,
  "items": [
    {
      "timestamp": "2026-10-07T12:00:00Z",
      "level": "INFO",
      "task_id": "task-abc123",
      "message": "任务开始执行",
      "details": {}
    }
  ]
}
```

---

## 错误码

| HTTP 状态码 | 错误码 | 说明 |
|-------------|--------|------|
| 400 | `INVALID_REQUEST` | 请求参数无效 |
| 401 | `UNAUTHORIZED` | 未认证 |
| 403 | `FORBIDDEN` | 无权限 |
| 404 | `AGENT_NOT_FOUND` | 智能体不存在 |
| 409 | `AGENT_BUSY` | 智能体正在执行任务 |
| 429 | `RATE_LIMITED` | 触发速率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |
| 503 | `SERVICE_UNAVAILABLE` | 服务不可用 |

### 错误响应格式

```json
{
  "error": {
    "code": "AGENT_NOT_FOUND",
    "message": "智能体不存在",
    "details": {
      "agent_id": "agent-invalid"
    },
    "request_id": "req-abc123"
  }
}
```

---

## 分页

所有列表接口都使用一致的分页格式。

### 请求参数

- `limit`：每页数量，默认 20，最大 100
- `offset`：偏移量，默认 0

### 响应格式

```json
{
  "total": 100,
  "limit": 20,
  "offset": 0,
  "items": [...]
}
```

---

## 速率限制

### 默认限制

| 端点类型 | 限制 | 说明 |
|---------|------|------|
| 读操作（GET） | 100 次/分钟 | 列表、查询等 |
| 写操作（POST/PUT/DELETE） | 30 次/分钟 | 创建、更新、删除 |
| 任务执行 | 10 次/分钟 | execute 端点 |
| 流式请求 | 5 并发 | 同时进行的流式请求数 |

### 响应头

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1699000000
```

### 超限响应

```
HTTP/1.1 429 Too Many Requests
Retry-After: 30
```

```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "请求过于频繁，请稍后重试",
    "details": {
      "limit": 100,
      "remaining": 0,
      "reset_in_seconds": 30
    }
  }
}
```

---

## 下一步

- [记忆 API](memory_api.md) - 记忆系统 API 参考
- [任务 API](tasks_api.md) - 任务管理 API 参考
- [健康 API](health_api.md) - 健康检查 API 参考
