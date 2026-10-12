# 快速上手指南

本指南将带你在 10 分钟内创建第一个 LiuHao AI OS 智能体并运行第一个任务。

---

## Hello World 示例

### 前提条件

- 已完成 [安装指南](installation.md) 中的安装步骤
- LiuHao AI OS 服务正在运行（端口 8080）
- 已安装并运行 Ollama

### 第一个 API 调用

使用 curl 测试基础 API：

```bash
# 健康检查
curl http://127.0.0.1:8080/v1/health

# 列出可用模型
curl http://127.0.0.1:8080/v1/providers/models
```

### 使用 Python SDK 发送对话

```python
from liuhao import LiuHaoClient

# 初始化客户端
client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 发送消息
response = client.chat.send("你好，请介绍一下你自己")
print(response.content)
```

### 使用 CLI

```bash
# 进入交互式 shell
liuhao shell

# 或者直接发送消息
liuhao chat "你好，LiuHao!"
```

---

## 核心概念速览

在深入之前，先了解几个核心概念：

### 智能体（Agent）

智能体是 LiuHao AI OS 中的基本执行单元。每个智能体：
- 有独立的身份和角色
- 拥有自己的记忆空间
- 可以使用工具和能力
- 能够执行任务并产生结果

### 记忆（Memory）

LiuHao AI OS 采用分层记忆体系：

| 记忆类型 | 层级 | 作用 | 生命周期 |
|---------|------|------|---------|
| 工作记忆 | 短期 | 当前任务的活跃上下文 | 分钟级 |
| 情景记忆 | 中期 | 经历过的事件和对话 | 小时~天 |
| 语义记忆 | 长期 | 知识、事实、概念 | 持久 |
| 元记忆 | 元层 | 关于记忆的记忆 | 持久 |

### 任务（Task）

任务是智能体需要完成的具体工作。任务可以：
- 由用户直接分配
- 由规划引擎分解生成
- 包含子任务形成任务树
- 有状态追踪（待办/进行中/已完成/失败）

---

## 创建你的第一个智能体

### 方式一：通过 API 创建

```bash
curl -X POST http://127.0.0.1:8080/v1/agents \
  -H "Content-Type: application/json" \
  -d '{
    "name": "我的助手",
    "role": "general",
    "description": "一个通用型个人助理智能体",
    "config": {
      "model": "qwen2.5:3b",
      "temperature": 0.7
    }
  }'
```

响应示例：

```json
{
  "id": "agent-abc123",
  "name": "我的助手",
  "role": "general",
  "status": "idle",
  "description": "一个通用型个人助理智能体",
  "created_at": "2026-10-07T12:00:00Z"
}
```

### 方式二：通过 Python SDK 创建

```python
from liuhao import LiuHaoClient

client = LiuHaoClient(base_url="http://127.0.0.1:8080")

# 创建智能体
agent = client.agents.create(
    name="研究助手",
    role="researcher",
    description="专门负责资料收集和研究分析"
)

print(f"智能体已创建: {agent.id}")
```

### 方式三：通过驾驶舱创建

1. 打开 `http://127.0.0.1:5173`
2. 进入「智能体管理」页面
3. 点击「新建智能体」
4. 填写表单并提交

---

## 运行你的第一个任务

### 给智能体分配任务

```bash
curl -X POST http://127.0.0.1:8080/v1/agents/agent-abc123/execute \
  -H "Content-Type: application/json" \
  -d '{
    "task": "帮我总结一下人工智能的发展历史，用中文回答，控制在300字以内"
  }'
```

### 查询任务状态

```bash
curl http://127.0.0.1:8080/v1/tasks/task-xyz789
```

任务状态说明：

| 状态 | 说明 |
|------|------|
| `pending` | 等待执行 |
| `running` | 正在执行 |
| `completed` | 已完成 |
| `failed` | 执行失败 |
| `paused` | 已暂停 |

### 使用 SDK 异步执行

```python
import asyncio
from liuhao import LiuHaoClient

async def main():
    client = LiuHaoClient(base_url="http://127.0.0.1:8080")
    
    # 执行任务并等待结果
    result = await client.agents.execute(
        agent_id="agent-abc123",
        task="写一首关于秋天的五言绝句"
    )
    
    print(f"任务状态: {result.status}")
    print(f"结果:\n{result.output}")

asyncio.run(main())
```

---

## 构建你的第一个流水线

流水线（Pipeline）用于处理数据流，支持从数据源读取、转换、验证到输出的完整流程。

### 示例：构建一个简单的文本处理流水线

```python
from liuhao.pipelines import PipelineBuilder

# 构建流水线
pipeline = (
    PipelineBuilder()
    .source("file", path="input.txt")          # 数据源：读取文件
    .transform("split", by="paragraph")        # 转换：按段落分割
    .transform("summarize", ratio=0.3)         # 转换：摘要提取
    .validate("length", min=10, max=500)       # 验证：长度检查
    .sink("file", path="output_summary.txt")   # 输出：写入文件
    .build()
)

# 运行流水线
result = pipeline.run()
print(f"处理了 {result.processed_count} 条数据")
```

### 流水线核心组件

| 组件 | 作用 | 示例 |
|------|------|------|
| Source | 数据源，从各种来源读取数据 | file, database, api, mqtt |
| Transform | 转换器，处理和转换数据 | filter, map, aggregate, summarize |
| Validate | 验证器，检查数据质量 | schema, length, format, custom |
| Sink | 输出槽，写入目标位置 | file, database, api, stdout |
| Aggregator | 聚合器，合并多路数据 | union, join, merge |

---

## 下一步

恭喜你完成了 LiuHao AI OS 的快速上手！接下来可以：

### 深入学习

- [智能体核心概念](../core-concepts/agents.md) - 深入理解智能体的工作原理
- [记忆系统详解](../core-concepts/memory.md) - 了解分层记忆体系
- [多智能体协作](../core-concepts/multi_agent.md) - 探索多智能体系统

### 动手实践

- [构建你的第一个智能体](../../tutorials/build_your_first_agent.md) - 完整教程
- [构建研究机器人](../../tutorials/build_a_research_bot.md) - 实战项目
- [构建数据流水线](../../tutorials/build_a_data_pipeline.md) - 数据处理教程

### 参考资源

- [API 参考](../api-reference/agents_api.md) - 完整的 API 文档
- [SDK 指南](../user-guide/sdk_guide.md) - 各语言 SDK 使用指南
- [常见问题](../faq/faq.md) - 常见问题解答
