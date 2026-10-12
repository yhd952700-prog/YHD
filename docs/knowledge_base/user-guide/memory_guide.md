# 记忆用户指南

本指南详细介绍如何使用 LiuHao AI OS 的记忆系统，包括各类记忆的使用方法、搜索技巧、优化策略和故障排除。

---

## 工作记忆使用

工作记忆是智能体当前活跃的短期记忆，容量有限但访问速度极快。

### 理解工作记忆

工作记忆就像智能体的"思考桌面"——当前正在处理的信息都在这里。它的特点：

- **容量小**：默认 20 项（7±2 原则的工程实现）
- **速度快**：内存访问，微秒级响应
- **易丢失**：不被关注的内容会快速衰减
- **优先级驱动**：重要的内容保留更久

### 手动管理工作记忆

```python
from liuhao import LiuHaoClient

client = LiuHaoClient()

# 添加工作记忆项
client.memory.working.add(
    agent_id="agent-abc123",
    key="current_task",
    content="完成季度销售报告",
    priority="high",
    item_type="goal"
)

# 批量添加
client.memory.working.add_many(
    agent_id="agent-abc123",
    items=[
        {"key": "deadline", "content": "2026-10-15", "priority": "critical"},
        {"key": "stakeholders", "content": ["张三", "李四"], "priority": "medium"}
    ]
)

# 获取工作记忆
items = client.memory.working.get_all(agent_id="agent-abc123")
for item in items:
    print(f"[{item.priority}] {item.key}: {item.content}")

# 获取最高优先级的 N 项
top_items = client.memory.working.get_top(
    agent_id="agent-abc123",
    n=5
)

# 按类型获取
goals = client.memory.working.get_by_type(
    agent_id="agent-abc123",
    item_type="goal"
)

# 删除指定项
client.memory.working.remove(
    agent_id="agent-abc123",
    key="current_task"
)

# 清空工作记忆
client.memory.working.clear(agent_id="agent-abc123")
```

### 工作记忆优先级

| 优先级 | 使用场景 | 建议数量 |
|--------|---------|---------|
| `critical` | 核心目标、活跃任务ID、关键约束 | 1-3 项 |
| `high` | 重要上下文、相关事实、用户偏好 | 3-5 项 |
| `medium` | 辅助信息、背景资料 | 5-8 项 |
| `low` | 边缘信息、可能用到的数据 | 2-5 项 |
| `fleeting` | 临时计算、中间结果 | 视情况而定 |

### 最佳实践

1. **控制工作记忆数量**：不要塞满低价值信息
2. **明确标记优先级**：重要的事情设为 critical 或 high
3. **定期清理**：任务完成后及时清理相关工作记忆
4. **关键信息持久化**：需要长期保留的内容转移到情景或语义记忆

---

## 语义记忆使用

语义记忆是智能体的长期知识库，存储概念、事实和知识。

### 添加语义记忆

```python
# 添加单条语义记忆
client.memory.store(
    agent_id="agent-abc123",
    content="用户张三是产品经理，负责移动端产品",
    memory_type="semantic",
    category="user_profiles",
    tags=["用户", "张三", "产品经理"],
    importance=0.85,
    metadata={
        "source": "conversation",
        "confidence": 0.9
    }
)

# 批量添加
memories = [
    {
        "content": "公司 Q3 销售额同比增长 25%",
        "category": "business",
        "tags": ["销售", "Q3", "增长"]
    },
    {
        "content": "产品 A 的主要用户群体是 25-35 岁的年轻人",
        "category": "product",
        "tags": ["产品A", "用户画像"]
    }
]

client.memory.store_many(
    agent_id="agent-abc123",
    memory_type="semantic",
    items=memories
)
```

### 从文档导入

```python
# 从文本文件导入
client.memory.import_file(
    agent_id="agent-abc123",
    file_path="/data/product_manual.pdf",
    memory_type="semantic",
    category="product_docs",
    chunk_size=1000,  # 分块大小（字符）
    chunk_overlap=100  # 块之间重叠
)

# 从 URL 导入
client.memory.import_url(
    agent_id="agent-abc123",
    url="https://docs.example.com/guide",
    memory_type="semantic",
    category="external_docs"
)
```

### 语义记忆分类

建议的分类体系：

| 分类 | 说明 | 示例 |
|------|------|------|
| `user_profiles` | 用户画像和偏好 | "用户喜欢简洁回答" |
| `business` | 业务知识 | "Q3 销售额 500 万" |
| `product` | 产品知识 | "产品 A 支持 3 种登录方式" |
| `technical` | 技术知识 | "Python 3.11 支持 TOML 解析" |
| `general` | 通用知识 | "北京是中国首都" |
| `procedural` | 流程知识 | "发布流程需经过测试和审批" |

---

## 情景记忆使用

情景记忆记录智能体的经历和交互历史。

### 情景记忆的结构

情景记忆以 Episode（事件片段）为单位组织：

```
Episode（一次会话/任务）
├── id: "ep-20261007-abc"
├── started_at: "2026-10-07T10:00:00Z"
├── ended_at: "2026-10-07T10:30:00Z"
├── type: "conversation"  # conversation / task / exploration
└── events: [
    Event 1: 用户提问
    Event 2: 智能体思考
    Event 3: 工具调用
    Event 4: 智能体回复
    ...
]
```

### 查看情景记忆

```python
# 列出最近的 episodes
episodes = client.memory.episodic.list(
    agent_id="agent-abc123",
    limit=10,
    type="conversation"
)

for ep in episodes:
    print(f"[{ep.started_at}] {ep.type} - {ep.summary[:50]}")

# 获取单个 episode 详情
episode = client.memory.episodic.get(
    agent_id="agent-abc123",
    episode_id="ep-20261007-abc"
)

# 查看事件序列
for event in episode.events:
    print(f"  [{event.timestamp}] {event.type}: {event.content[:50]}")
```

### 搜索情景记忆

```python
# 按关键词搜索
results = client.memory.episodic.search(
    agent_id="agent-abc123",
    query="产品发布计划",
    limit=5
)

# 按时间范围搜索
results = client.memory.episodic.search(
    agent_id="agent-abc123",
    query="销售数据",
    start_time="2026-09-01",
    end_time="2026-10-01"
)
```

---

## 记忆搜索技巧

### 基础搜索

```python
# 简单搜索
results = client.memory.search(
    agent_id="agent-abc123",
    query="用户的偏好"
)
```

### 高级搜索选项

```python
results = client.memory.search(
    agent_id="agent-abc123",
    query="Q3 销售分析",
    
    # 类型过滤
    memory_types=["semantic", "episodic"],
    
    # 分类过滤
    categories=["business", "reports"],
    
    # 标签过滤
    tags=["销售", "Q3"],
    
    # 结果数量
    limit=10,
    
    # 相似度阈值（0-1）
    threshold=0.7,
    
    # 排序方式
    sort_by="relevance",  # relevance / time / importance
    
    # 权重配置
    weights={
        "semantic_similarity": 0.6,
        "recency": 0.2,
        "importance": 0.2
    }
)
```

### 混合搜索策略

LiuHao AI OS 支持多种搜索模式：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `hybrid` | 关键词 + 向量混合（默认） | 大多数场景 |
| `semantic` | 纯向量语义搜索 | 概念性、语义性查询 |
| `keyword` | 纯关键词匹配 | 精确术语查找 |
| `graph` | 知识图谱遍历 | 关系型查询 |

```python
# 指定搜索模式
results = client.memory.search(
    agent_id="agent-abc123",
    query="什么是 RAG？",
    mode="semantic"
)
```

### 搜索结果解读

```python
for result in results:
    print(f"相似度: {result.score:.3f}")
    print(f"类型: {result.memory_type}")
    print(f"分类: {result.category}")
    print(f"重要性: {result.importance}")
    print(f"内容: {result.content}")
    print(f"标签: {result.tags}")
    print()
```

---

## 记忆优化

### 1. 记忆去重

重复的记忆会浪费存储空间并干扰搜索结果：

```python
# 查找重复记忆
duplicates = client.memory.find_duplicates(
    agent_id="agent-abc123",
    threshold=0.95  # 相似度阈值
)

# 合并重复记忆
client.memory.merge_duplicates(
    agent_id="agent-abc123",
    strategy="keep_most_important"  # keep_latest / keep_most_important / merge
)
```

### 2. 记忆压缩

长期记忆会越来越多，定期压缩可以提升性能：

```python
# 压缩旧的情景记忆（提取要点转为语义记忆）
client.memory.episodic.compress(
    agent_id="agent-abc123",
    older_than="30d",
    strategy="extract_key_points"
)

# 清理低价值记忆
client.memory.cleanup(
    agent_id="agent-abc123",
    min_importance=0.3,
    min_access_count=0,
    older_than="90d"
)
```

### 3. 记忆分层优化

合理利用各层记忆的特点：

- **工作记忆**：只放当前任务真正需要的信息
- **情景记忆**：记录完整过程，但定期压缩归档
- **语义记忆**：存储提炼后的知识，用于长期检索
- **元记忆**：记录策略，优化记忆本身的使用

### 4. 性能调优

```python
# 配置记忆系统
memory_config = {
    "working_memory": {
        "capacity": 20,
        "decay_interval": 60  # 秒
    },
    "semantic_memory": {
        "vector_index_size": 10000,
        "search_effort": 100  # 搜索精度 vs 速度的权衡
    },
    "episodic_memory": {
        "max_events_per_episode": 500,
        "retention_days": 90
    }
}
```

---

## 记忆故障排除

### 问题：搜索不到相关记忆

**可能原因和解决方案**：

1. **记忆不存在**
   - 确认记忆已正确存储
   - 检查存储时是否有错误

2. **查询方式不对**
   - 尝试不同的查询词
   - 使用更通用的查询
   - 降低相似度阈值

3. **记忆类型不匹配**
   - 确认搜索的类型是否正确
   - 尝试不指定类型搜索

```python
# 排查搜索问题
results = client.memory.search(
    agent_id="agent-abc123",
    query="你的查询",
    threshold=0.3,  # 降低阈值
    limit=20,       # 增加返回数量
    memory_types=None  # 不限制类型
)
```

### 问题：记忆太多导致混乱

**解决方案**：

1. 清理低价值记忆
2. 合并重复记忆
3. 压缩旧的情景记忆
4. 使用更具体的搜索查询

### 问题：记忆遗忘太快

**解决方案**：

1. 提高重要性评分
2. 主动"复习"重要记忆
3. 将重要记忆从工作记忆转移到语义记忆

```python
# 主动巩固重要记忆
client.memory.consolidate(
    agent_id="agent-abc123",
    memory_ids=["mem-abc", "mem-def"],
    target="semantic"
)
```

### 问题：记忆包含错误信息

**解决方案**：

1. 更新或删除错误记忆
2. 添加纠正信息（带更高置信度）
3. 设置记忆的置信度字段

```python
# 更新记忆
client.memory.update(
    memory_id="mem-abc",
    content="更新后的正确内容",
    metadata={"confidence": 0.95}
)

# 删除错误记忆
client.memory.delete(memory_id="mem-abc")
```

### 问题：记忆占用空间过大

**解决方案**：

1. 运行清理任务，删除过期记忆
2. 压缩情景记忆
3. 调整保留策略
4. 考虑使用外部存储后端

---

## 记忆保留策略

### 默认保留策略

| 记忆类型 | 默认保留时间 | 清理方式 |
|---------|-------------|---------|
| 工作记忆 | 会话结束即清理 | 自动衰减 |
| 情景记忆 | 90 天 | 定期压缩归档 |
| 语义记忆 | 永久（除非手动删除） | 手动管理 |
| 元记忆 | 永久 | 手动管理 |

### 自定义保留策略

```python
policy = {
    "episodic": {
        "retention_days": 30,
        "compression_after_days": 7,
        "archive_after_days": 30
    },
    "semantic": {
        "max_items": 100000,
        "cleanup_strategy": "least_accessed"
    }
}

client.memory.set_retention_policy(
    agent_id="agent-abc123",
    policy=policy
)
```

---

## 下一步

- [记忆系统核心概念](../core-concepts/memory.md) - 深入理解记忆原理
- [智能体用户指南](agent_guide.md) - 智能体与记忆的配合使用
- [API 参考](../api-reference/memory_api.md) - 完整的记忆 API 文档
