# 记忆 API 参考

本 API 提供记忆系统的管理接口，包括记忆的增删改查、搜索、批量操作等功能。

---

## 端点列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/v1/memory` | 存储记忆 |
| `GET` | `/v1/memory` | 列出记忆 |
| `GET` | `/v1/memory/{id}` | 获取记忆详情 |
| `PUT` | `/v1/memory/{id}` | 更新记忆 |
| `DELETE` | `/v1/memory/{id}` | 删除记忆 |
| `POST` | `/v1/memory/batch` | 批量存储 |
| `DELETE` | `/v1/memory/batch` | 批量删除 |
| `POST` | `/v1/memory/search` | 搜索记忆 |
| `GET` | `/v1/memory/categories` | 列出分类 |
| `GET` | `/v1/memory/stats` | 记忆统计 |
| `POST` | `/v1/memory/consolidate` | 记忆巩固 |
| `POST` | `/v1/memory/cleanup` | 清理记忆 |

---

## 存储记忆

创建一条新的记忆。

### 请求

```
POST /v1/memory
Content-Type: application/json
```

```json
{
  "content": "用户张三是产品经理，负责移动端产品线",
  "memory_type": "semantic",
  "category": "user_profiles",
  "tags": ["用户", "张三", "产品经理", "移动端"],
  "importance": 0.85,
  "scope_level": "L2",
  "agent_id": "agent-abc123",
  "metadata": {
    "source": "conversation",
    "confidence": 0.9,
    "conversation_id": "conv-xyz789"
  }
}
```

### 请求体字段

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `content` | string | 是 | 记忆内容 |
| `memory_type` | string | 否 | 记忆类型（episodic/semantic/procedural/working/meta），默认 episodic |
| `category` | string | 否 | 分类，默认 "general" |
| `tags` | array | 否 | 标签列表 |
| `importance` | float | 否 | 重要性评分 0-1，默认 0.5 |
| `scope_level` | string | 否 | 作用域级别（L0-L7），默认 L2 |
| `agent_id` | string | 否 | 所属智能体 ID |
| `metadata` | object | 否 | 自定义元数据 |

### 响应（201 Created）

```json
{
  "id": "mem-abc123def456",
  "content": "用户张三是产品经理，负责移动端产品线",
  "memory_type": "semantic",
  "category": "user_profiles",
  "tags": ["用户", "张三", "产品经理", "移动端"],
  "importance": 0.85,
  "scope_level": "L2",
  "agent_id": "agent-abc123",
  "created_at": "2026-10-07T12:00:00Z",
  "updated_at": "2026-10-07T12:00:00Z",
  "last_accessed": null,
  "access_count": 0,
  "metadata": {
    "source": "conversation",
    "confidence": 0.9
  }
}
```

---

## 列出记忆

分页获取记忆列表。

### 请求

```
GET /v1/memory?memory_type=semantic&category=user_profiles&limit=20&offset=0
```

### 查询参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `memory_type` | string | - | 按类型筛选 |
| `category` | string | - | 按分类筛选 |
| `agent_id` | string | - | 按智能体筛选 |
| `tag` | string | - | 按标签筛选（可多次指定） |
| `min_importance` | float | - | 最低重要性 |
| `limit` | int | 20 | 每页数量 |
| `offset` | int | 0 | 偏移量 |
| `sort_by` | string | created_at | 排序字段 |
| `sort_order` | string | desc | 排序方向 |

### 响应（200 OK）

```json
{
  "total": 156,
  "limit": 20,
  "offset": 0,
  "items": [
    {
      "id": "mem-abc123",
      "content": "用户张三是产品经理...",
      "memory_type": "semantic",
      "category": "user_profiles",
      "tags": ["用户", "张三"],
      "importance": 0.85,
      "created_at": "2026-10-07T12:00:00Z",
      "access_count": 5
    }
  ]
}
```

---

## 获取记忆详情

### 请求

```
GET /v1/memory/{memory_id}
```

### 响应（200 OK）

返回完整的记忆对象，格式同「存储记忆」响应。

---

## 更新记忆

### 请求

```
PUT /v1/memory/{memory_id}
Content-Type: application/json
```

```json
{
  "content": "更新后的内容",
  "category": "new_category",
  "tags": ["标签1", "标签2"],
  "importance": 0.9,
  "metadata": {
    "key": "value"
  }
}
```

### 响应（200 OK）

返回更新后的记忆对象。

---

## 删除记忆

### 请求

```
DELETE /v1/memory/{memory_id}
```

### 响应（204 No Content）

---

## 批量存储

### 请求

```
POST /v1/memory/batch
Content-Type: application/json
```

```json
{
  "items": [
    {
      "content": "记忆内容 1",
      "memory_type": "semantic",
      "category": "general"
    },
    {
      "content": "记忆内容 2",
      "memory_type": "semantic",
      "category": "general"
    }
  ]
}
```

### 响应（201 Created）

```json
{
  "created": 2,
  "failed": 0,
  "items": [
    {"id": "mem-aaa", "status": "created"},
    {"id": "mem-bbb", "status": "created"}
  ]
}
```

---

## 搜索记忆

语义搜索记忆内容。

### 请求

```
POST /v1/memory/search
Content-Type: application/json
```

```json
{
  "query": "用户的职责是什么",
  "agent_id": "agent-abc123",
  "memory_types": ["semantic", "episodic"],
  "categories": ["user_profiles"],
  "limit": 10,
  "threshold": 0.7,
  "mode": "hybrid",
  "weights": {
    "semantic_similarity": 0.6,
    "recency": 0.2,
    "importance": 0.2
  }
}
```

### 请求体字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | string | 必填 | 搜索查询 |
| `agent_id` | string | - | 按智能体筛选 |
| `memory_types` | array | 全部 | 记忆类型过滤 |
| `categories` | array | 全部 | 分类过滤 |
| `limit` | int | 10 | 返回数量 |
| `threshold` | float | 0.7 | 相似度阈值 0-1 |
| `mode` | string | hybrid | 搜索模式（hybrid/semantic/keyword/graph） |

### 响应（200 OK）

```json
{
  "query": "用户的职责是什么",
  "total": 23,
  "returned": 10,
  "results": [
    {
      "id": "mem-abc123",
      "content": "用户张三是产品经理，负责移动端产品线",
      "memory_type": "semantic",
      "category": "user_profiles",
      "score": 0.92,
      "importance": 0.85,
      "created_at": "2026-10-07T12:00:00Z"
    }
  ]
}
```

---

## 记忆统计

### 请求

```
GET /v1/memory/stats?agent_id=agent-abc123
```

### 响应（200 OK）

```json
{
  "total_count": 1234,
  "by_type": {
    "working": 15,
    "episodic": 456,
    "semantic": 720,
    "meta": 43
  },
  "by_category": {
    "user_profiles": 89,
    "business": 234,
    "general": 911
  },
  "total_size_bytes": 5242880,
  "last_24h_added": 42,
  "last_24h_accessed": 156
}
```

---

## 错误码

| HTTP 状态码 | 错误码 | 说明 |
|-------------|--------|------|
| 400 | `INVALID_REQUEST` | 请求参数无效 |
| 401 | `UNAUTHORIZED` | 未认证 |
| 403 | `FORBIDDEN` | 无权限访问该记忆 |
| 404 | `MEMORY_NOT_FOUND` | 记忆不存在 |
| 429 | `RATE_LIMITED` | 触发速率限制 |
| 500 | `INTERNAL_ERROR` | 服务器内部错误 |

---

## 下一步

- [智能体 API](agents_api.md) - 智能体管理 API
- [知识 API](knowledge_api.md) - 知识库 API 参考
- [记忆用户指南](../user-guide/memory_guide.md) - 记忆系统使用指南
