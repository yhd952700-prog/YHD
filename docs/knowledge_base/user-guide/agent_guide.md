# 智能体用户指南

本指南详细介绍如何创建、配置、运行和管理 LiuHao AI OS 中的智能体。

---

## 创建智能体

### 通过 API 创建

使用 REST API 创建智能体是最灵活的方式：

```bash
curl -X POST http://127.0.0.1:8080/v1/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "数据分析助手",
    "role": "analyst",
    "description": "专业的数据分析师，擅长数据可视化和洞察提取",
    "config": {
      "model": "qwen2.5:7b",
      "temperature": 0.3,
      "max_tokens": 4096,
      "system_prompt": "你是一名专业的数据分析师..."
    }
  }'
```

### 通过 Python SDK 创建

```python
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

agent = client.agents.create(
    name="代码审查助手",
    role="critic",
    description="擅长代码质量审查和最佳实践建议",
    config={
        "model": "qwen2.5:7b",
        "temperature": 0.2,
        "tools": ["code_analyzer", "file_reader", "git_diff"]
    }
)

print(f"智能体创建成功: {agent.id}")
```

### 通过 CLI 创建

```bash
# 交互式创建
liuhao agent create

# 带参数创建
liuhao agent create --name "我的助手" --role general --description "个人助理"
```

### 通过驾驶舱创建

1. 打开驾驶舱 `http://127.0.0.1:5173`
2. 进入「智能体管理」
3. 点击「新建智能体」
4. 填写表单，选择角色和配置
5. 点击「创建」

---

## 配置智能体

### 基础配置

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `name` | string | 必填 | 智能体名称，1-100 字符 |
| `role` | string | 必填 | 角色类型，见角色列表 |
| `description` | string | "" | 描述，最多 500 字符 |
| `model` | string | "qwen2.5:3b" | 使用的 LLM 模型 |
| `temperature` | float | 0.7 | 采样温度，0-2 |
| `max_tokens` | int | 2048 | 最大生成长度 |

### 系统提示词配置

系统提示词定义了智能体的基础行为和身份：

```python
agent_config = {
    "system_prompt": """你是一名专业的研究分析师。
你的职责是：
1. 收集和整理相关信息
2. 进行深入分析
3. 提供客观、有依据的结论

请始终：
- 基于事实，不臆测
- 引用来源，标注出处
- 结构清晰，逻辑严谨""",
}
```

### 工具配置

配置智能体可以使用的工具：

```python
agent_config = {
    "tools": [
        {
            "name": "web_search",
            "enabled": True,
            "config": {
                "max_results": 10,
                "language": "zh-CN"
            }
        },
        {
            "name": "file_reader",
            "enabled": True,
            "config": {
                "allowed_paths": ["/data/reports/"]
            }
        },
        {
            "name": "code_executor",
            "enabled": True,
            "config": {
                "timeout": 30,
                "memory_limit": "512m"
            }
        }
    ]
}
```

### 记忆配置

```python
agent_config = {
    "memory": {
        "enabled": True,
        "working_memory_size": 20,
        "episodic_memory_ttl": "7d",
        "semantic_memory_enabled": True,
        "consolidation_threshold": 0.7
    }
}
```

### 安全配置

```python
agent_config = {
    "security": {
        "scope_level": "L3",              # 作用域级别
        "allowed_tools": ["web_search"],  # 白名单工具
        "denied_tools": ["shell_exec"],   # 黑名单工具
        "max_tool_calls_per_task": 50,    # 单任务最大工具调用
        "human_approval_required": [      # 需要人类审批的操作
            "file_write",
            "api_call_external"
        ]
    }
}
```

### 更新配置

```python
# 更新智能体配置
client.agents.update(
    agent_id="agent-abc123",
    config={
        "temperature": 0.5,
        "max_tokens": 8192
    }
)
```

---

## 运行智能体

### 执行单次任务

```python
# 同步执行，等待结果
result = client.agents.execute(
    agent_id="agent-abc123",
    task="分析这份销售数据，找出增长最快的产品",
    context={
        "data_file": "/data/sales_q3.csv",
        "time_range": "2026 Q3"
    }
)

print(f"状态: {result.status}")
print(f"结果:\n{result.output}")
```

### 异步执行

```python
import asyncio

async def run_task():
    # 启动任务
    task = await client.agents.execute_async(
        agent_id="agent-abc123",
        task="生成一份完整的市场研究报告"
    )
    
    print(f"任务已启动: {task.id}")
    
    # 轮询状态
    while True:
        status = await client.tasks.get(task.id)
        print(f"进度: {status.progress}%")
        
        if status.status in ("completed", "failed"):
            break
        
        await asyncio.sleep(5)
    
    return status

result = asyncio.run(run_task())
```

### 流式输出

```python
# 流式获取智能体回复
stream = client.agents.stream(
    agent_id="agent-abc123",
    task="写一个关于人工智能的故事"
)

for chunk in stream:
    if chunk.type == "text":
        print(chunk.content, end="", flush=True)
    elif chunk.type == "tool_call":
        print(f"\n[调用工具: {chunk.tool_name}]")
    elif chunk.type == "thought":
        print(f"\n[思考: {chunk.content[:50]}...]")
```

### 对话模式

智能体也支持多轮对话：

```python
# 开始对话会话
session = client.agents.start_session(agent_id="agent-abc123")

# 第一轮
response1 = session.send("你好，请帮我分析一下这个问题")
print(response1.content)

# 第二轮（携带上下文）
response2 = session.send("能再详细说说第二点吗？")
print(response2.content)

# 获取对话历史
history = session.get_history()
for msg in history:
    print(f"[{msg.role}]: {msg.content[:50]}...")
```

---

## 监控智能体

### 查看运行状态

```bash
# 获取智能体状态
curl http://127.0.0.1:8080/v1/agents/agent-abc123/status
```

响应示例：

```json
{
  "id": "agent-abc123",
  "status": "running",
  "current_task": "分析销售数据",
  "task_progress": 65,
  "uptime_seconds": 3600,
  "tasks_completed": 42,
  "tasks_failed": 2,
  "memory_usage": {
    "working": 15,
    "episodic": 1024,
    "semantic": 5000
  }
}
```

### 查看指标

```python
metrics = client.agents.get_metrics(agent_id="agent-abc123")

print(f"总任务数: {metrics.total_tasks}")
print(f"成功率: {metrics.success_rate:.1%}")
print(f"平均耗时: {metrics.avg_duration_seconds:.1f}s")
print(f"Token 消耗: {metrics.total_tokens}")
print(f"成本: ${metrics.total_cost:.4f}")
```

### 查看日志

```python
# 获取智能体运行日志
logs = client.agents.get_logs(
    agent_id="agent-abc123",
    level="INFO",       # DEBUG, INFO, WARNING, ERROR
    limit=100,
    since="1h"          # 最近1小时
)

for log in logs:
    print(f"[{log.timestamp}] [{log.level}] {log.message}")
```

### 实时监控（WebSocket）

```javascript
// 连接实时事件流
const ws = new WebSocket("ws://localhost:8080/v1/events/ws");

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: "subscribe",
    agent_id: "agent-abc123",
    events: ["task_started", "task_progress", "task_completed", "tool_call"]
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log(`[${data.event_type}]`, data.payload);
};
```

---

## 智能体模板

LiuHao AI OS 提供了多种预配置的智能体模板，可以快速创建常用类型的智能体。

### 使用模板

```python
# 查看可用模板
templates = client.agents.list_templates()
for t in templates:
    print(f"{t.id}: {t.name} - {t.description}")

# 从模板创建
agent = client.agents.create_from_template(
    template_id="research_assistant",
    name="我的研究助手",
    overrides={
        "config": {
            "model": "qwen2.5:7b"
        }
    }
)
```

### 内置模板列表

| 模板 ID | 名称 | 角色 | 适用场景 |
|---------|------|------|---------|
| `research_assistant` | 研究助手 | researcher | 资料收集、文献调研、市场分析 |
| `code_reviewer` | 代码审查员 | critic | 代码质量检查、最佳实践建议 |
| `data_analyst` | 数据分析师 | analyst | 数据分析、可视化、报告生成 |
| `content_writer` | 内容创作者 | creator | 文章写作、文案创作、内容生成 |
| `personal_assistant` | 个人助理 | general | 日程管理、信息查询、日常事务 |
| `project_manager` | 项目经理 | manager | 任务分解、进度跟踪、团队协调 |
| `qa_engineer` | 测试工程师 | qa | 测试用例设计、缺陷发现、质量保证 |

### 自定义模板

```python
# 创建自定义模板
template = client.agents.create_template(
    id="financial_analyst",
    name="财务分析师",
    description="专业的财务数据分析助手",
    role="analyst",
    config={
        "model": "qwen2.5:7b",
        "temperature": 0.2,
        "system_prompt": "你是一名资深财务分析师...",
        "tools": ["spreadsheet_reader", "calculator", "chart_generator"]
    }
)
```

---

## 智能体管理

### 列出智能体

```python
# 分页列出
agents = client.agents.list(
    limit=20,
    offset=0,
    status="active",
    role="analyst"
)

print(f"共 {agents.total} 个智能体")
for agent in agents.items:
    print(f"- {agent.name} ({agent.role}) [{agent.status}]")
```

### 暂停和恢复

```python
# 暂停智能体
client.agents.pause(agent_id="agent-abc123")

# 恢复运行
client.agents.resume(agent_id="agent-abc123")
```

### 停止运行中的任务

```python
# 停止当前任务
client.agents.stop(agent_id="agent-abc123")

# 强制停止（立即终止）
client.agents.stop(agent_id="agent-abc123", force=True)
```

### 删除智能体

```python
# 删除智能体（同时删除其记忆）
client.agents.delete(agent_id="agent-abc123")

# 删除但保留记忆
client.agents.delete(agent_id="agent-abc123", keep_memory=True)
```

### 克隆智能体

```python
# 复制一个智能体（包含配置，不包含记忆）
new_agent = client.agents.clone(
    agent_id="agent-abc123",
    name="新的智能体副本"
)
```

---

## 最佳实践

### 1. 明确的角色定义

- 每个智能体应该有清晰的职责边界
- 避免"万能"智能体，专注才能高效
- 使用系统提示词明确角色和约束

### 2. 合理的工具配置

- 只启用真正需要的工具（最小权限原则）
- 为工具设置适当的安全限制
- 危险操作需要人类审批

### 3. 记忆管理

- 定期清理无用记忆
- 重要信息明确标记高重要性
- 敏感信息使用高作用域级别

### 4. 性能优化

- 选择合适的模型，小模型能完成的不用大模型
- 合理设置 max_tokens，避免浪费
- 复杂任务用多智能体协作，而非单个大智能体

### 5. 错误处理

- 设置合理的超时和重试机制
- 监控失败率，及时排查问题
- 为关键任务设计 fallback 方案

### 6. 安全第一

- 遵循最小权限原则
- 敏感操作需要人类审批
- 定期审计智能体行为
- 保护用户隐私数据

---

## 下一步

- [智能体核心概念](../core-concepts/agents.md) - 深入理解智能体原理
- [记忆用户指南](memory_guide.md) - 管理智能体的记忆
- [插件用户指南](plugins_guide.md) - 通过插件扩展智能体能力
- [多智能体系统教程](../../tutorials/build_multi_agent_system.md) - 构建智能体团队
