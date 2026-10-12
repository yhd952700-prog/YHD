# SDK 用户指南

LiuHao AI OS 提供多种编程语言的 SDK，方便开发者在不同环境中集成和使用 LiuHao AI OS 的能力。本指南介绍各 SDK 的快速上手和使用方法。

---

## SDK 概览

### 支持的语言

| 语言 | 包名 | 版本 | 状态 | 适用场景 |
|------|------|------|------|---------|
| Python | `liuhao` | v3.0.0 | 稳定 | 后端开发、数据分析、脚本自动化 |
| JavaScript/TypeScript | `@liuhao/sdk` | v3.0.0 | 稳定 | 前端应用、Node.js 服务 |
| Go | `github.com/liuhao-ai/liuhao-go-sdk` | v3.0.0 | 稳定 | 高性能服务、云原生开发 |
| Java | `ai.liuhao:liuhao-sdk` | v1.0.0 | Beta | 企业级 Java 应用 |
| Rust | `liuhao-sdk` | v0.5.0 | Beta | 高性能系统编程 |

### SDK 能力对比

| 功能 | Python | JS/TS | Go | Java |
|------|--------|-------|----|------|
| 智能体管理 | ✓ | ✓ | ✓ | ✓ |
| 对话交互 | ✓ | ✓ | ✓ | ✓ |
| 记忆管理 | ✓ | ✓ | ✓ | Partial |
| 任务管理 | ✓ | ✓ | ✓ | ✓ |
| 流水线操作 | ✓ | ✓ | Partial | - |
| 插件管理 | ✓ | Partial | - | - |
| 流式输出 | ✓ | ✓ | ✓ | ✓ |
| WebSocket 事件 | ✓ | ✓ | ✓ | - |
| 异步支持 | ✓ (asyncio) | ✓ (Promise) | ✓ (goroutine) | ✓ (Future) |

---

## Python SDK 快速开始

### 安装

```bash
pip install liuhao
```

### 初始化客户端

```python
from liuhao import LiuHaoClient

# 基本初始化
client = LiuHaoClient(
    base_url="http://127.0.0.1:8080",
    api_key="your-api-key"  # 可选，根据安全配置
)

# 从环境变量初始化（推荐）
# 环境变量: LIUHAO_BASE_URL, LIUHAO_API_KEY
client = LiuHaoClient.from_env()
```

### 发送对话消息

```python
# 简单对话
response = client.chat.send("你好，请介绍一下 LiuHao AI OS")
print(response.content)

# 带上下文的对话
response = client.chat.send(
    message="总结一下上面的内容",
    conversation_id="conv-abc123"  # 维持同一个会话
)
```

### 智能体操作

```python
# 创建智能体
agent = client.agents.create(
    name="研究助手",
    role="researcher",
    description="专业研究分析师"
)

# 执行任务
result = client.agents.execute(
    agent_id=agent.id,
    task="研究人工智能的最新发展趋势"
)
print(result.output)

# 流式输出
for chunk in client.agents.stream(
    agent_id=agent.id,
    task="写一首关于秋天的诗"
):
    if chunk.type == "text":
        print(chunk.content, end="", flush=True)
```

### 记忆操作

```python
# 存储记忆
client.memory.store(
    content="用户张三是产品经理",
    memory_type="semantic",
    tags=["用户", "张三", "产品经理"]
)

# 搜索记忆
results = client.memory.search("用户偏好", limit=5)
for r in results:
    print(f"[{r.score:.2f}] {r.content}")
```

### 异步使用

```python
import asyncio
from liuhao import AsyncLiuHaoClient

async def main():
    client = AsyncLiuHaoClient.from_env()
    
    # 并行执行多个任务
    results = await asyncio.gather(
        client.agents.execute("agent-a", "任务A"),
        client.agents.execute("agent-b", "任务B"),
        client.agents.execute("agent-c", "任务C")
    )
    
    for result in results:
        print(result.status)

asyncio.run(main())
```

---

## JavaScript SDK 快速开始

### 安装

```bash
# npm
npm install @liuhao/sdk

# yarn
yarn add @liuhao/sdk

# pnpm
pnpm add @liuhao/sdk
```

### 初始化客户端

```javascript
import { LiuHaoClient } from '@liuhao/sdk';

const client = new LiuHaoClient({
  baseUrl: 'http://127.0.0.1:8080',
  apiKey: 'your-api-key'
});
```

### 发送对话消息

```javascript
// 简单对话
const response = await client.chat.send('你好，LiuHao!');
console.log(response.content);

// 流式输出
const stream = client.chat.stream('讲一个故事');
for await (const chunk of stream) {
  if (chunk.type === 'text') {
    process.stdout.write(chunk.content);
  }
}
```

### 智能体操作

```javascript
// 创建智能体
const agent = await client.agents.create({
  name: '代码助手',
  role: 'creator',
  description: '专业的编程助手'
});

console.log(`智能体 ID: ${agent.id}`);

// 执行任务
const result = await client.agents.execute(agent.id, {
  task: '用 Python 写一个快速排序算法'
});

console.log(result.output);
```

### 在 React 中使用

```jsx
import { useLiuHao } from '@liuhao/sdk/react';

function ChatComponent() {
  const { chat, isLoading } = useLiuHao({
    baseUrl: 'http://127.0.0.1:8080'
  });

  const [messages, setMessages] = useState([]);

  const sendMessage = async (text) => {
    const response = await chat.send(text);
    setMessages(prev => [...prev, 
      { role: 'user', content: text },
      { role: 'assistant', content: response.content }
    ]);
  };

  return (
    <div>
      {messages.map((msg, i) => (
        <div key={i} className={msg.role}>
          {msg.content}
        </div>
      ))}
      <button onClick={() => sendMessage('你好')} disabled={isLoading}>
        发送
      </button>
    </div>
  );
}
```

---

## Go SDK 快速开始

### 安装

```bash
go get github.com/liuhao-ai/liuhao-go-sdk
```

### 初始化客户端

```go
package main

import (
    "context"
    "fmt"
    liuhao "github.com/liuhao-ai/liuhao-go-sdk"
)

func main() {
    // 创建客户端
    client := liuhao.NewClient(
        liuhao.WithBaseURL("http://127.0.0.1:8080"),
        liuhao.WithAPIKey("your-api-key"),
    )

    // 或从环境变量初始化
    client, _ := liuhao.NewClientFromEnv()
}
```

### 发送对话消息

```go
func main() {
    client, _ := liuhao.NewClientFromEnv()
    ctx := context.Background()

    // 简单对话
    response, err := client.Chat.Send(ctx, "你好，请介绍一下自己")
    if err != nil {
        panic(err)
    }
    fmt.Println(response.Content)
}
```

### 智能体操作

```go
func main() {
    client, _ := liuhao.NewClientFromEnv()
    ctx := context.Background()

    // 创建智能体
    agent, err := client.Agents.Create(ctx, &liuhao.CreateAgentRequest{
        Name:        "数据分析助手",
        Role:        "analyst",
        Description: "专业数据分析师",
    })
    if err != nil {
        panic(err)
    }

    // 执行任务
    result, err := client.Agents.Execute(ctx, agent.ID, 
        "分析 Q3 销售数据，找出增长趋势")
    if err != nil {
        panic(err)
    }

    fmt.Printf("状态: %s\n", result.Status)
    fmt.Printf("结果: %s\n", result.Output)
}
```

### 流式输出

```go
func main() {
    client, _ := liuhao.NewClientFromEnv()
    ctx := context.Background()

    stream, err := client.Agents.Stream(ctx, "agent-abc123", 
        "写一首关于春天的诗")
    if err != nil {
        panic(err)
    }

    for chunk := range stream.Channels() {
        if chunk.Type == "text" {
            fmt.Print(chunk.Content)
        }
    }
}
```

---

## SDK 对比与选择指南

### 选择建议

| 场景 | 推荐 SDK | 原因 |
|------|---------|------|
| 数据科学/分析 | Python | 生态丰富，与 pandas/numpy 集成好 |
| 后端自动化脚本 | Python | 开发效率高，库丰富 |
| Web 前端 | JavaScript/TypeScript | 原生支持，React/Vue 集成 |
| Node.js 后端 | JavaScript/TypeScript | 异步性能好，生态丰富 |
| 微服务/云原生 | Go | 高性能，低延迟，部署简单 |
| 企业级 Java 应用 | Java | 与 Spring Boot 等框架集成 |
| 系统级编程 | Rust | 内存安全，极致性能 |

### 功能矩阵

| 功能模块 | Python | JS/TS | Go |
|---------|--------|-------|----|
| **聊天接口** | ✓ | ✓ | ✓ |
| 同步对话 | ✓ | ✓ | ✓ |
| 流式对话 | ✓ | ✓ | ✓ |
| 会话管理 | ✓ | ✓ | ✓ |
| **智能体管理** | ✓ | ✓ | ✓ |
| 创建/删除/更新 | ✓ | ✓ | ✓ |
| 执行任务（同步） | ✓ | ✓ | ✓ |
| 执行任务（流式） | ✓ | ✓ | ✓ |
| 暂停/恢复/停止 | ✓ | ✓ | ✓ |
| 指标/日志 | ✓ | ✓ | Partial |
| **记忆管理** | ✓ | ✓ | ✓ |
| 存储/更新/删除 | ✓ | ✓ | ✓ |
| 搜索 | ✓ | ✓ | ✓ |
| 工作记忆操作 | ✓ | Partial | - |
| **任务管理** | ✓ | ✓ | ✓ |
| 创建/查询/取消 | ✓ | ✓ | ✓ |
| 任务列表 | ✓ | ✓ | ✓ |
| **流水线** | ✓ | ✓ | Partial |
| 创建/运行 | ✓ | ✓ | - |
| 结果查询 | ✓ | ✓ | - |
| **插件管理** | ✓ | Partial | - |
| 列出/安装/卸载 | ✓ | - | - |
| 配置 | ✓ | - | - |
| **事件（WebSocket）** | ✓ | ✓ | ✓ |
| 订阅事件 | ✓ | ✓ | ✓ |
| 实时监控 | ✓ | ✓ | ✓ |

---

## SDK 最佳实践

### 1. 客户端生命周期管理

```python
# 推荐：使用单例模式，全局复用客户端
# 不要每次请求都创建新客户端

# Python
from liuhao import LiuHaoClient

# 在模块级别初始化一次
_client = None

def get_client() -> LiuHaoClient:
    global _client
    if _client is None:
        _client = LiuHaoClient.from_env()
    return _client
```

### 2. 错误处理

```python
from liuhao import LiuHaoClient, errors

client = LiuHaoClient.from_env()

try:
    result = client.agents.execute("agent-id", "任务")
except errors.AgentNotFoundError:
    print("智能体不存在")
except errors.RateLimitError:
    print("触发速率限制，请稍后重试")
except errors.APIError as e:
    print(f"API 错误: {e.code} - {e.message}")
except Exception as e:
    print(f"未知错误: {e}")
```

### 3. 重试机制

```python
from liuhao import LiuHaoClient
from liuhao.utils import RetryConfig

client = LiuHaoClient(
    base_url="http://127.0.0.1:8080",
    retry_config=RetryConfig(
        max_retries=3,
        base_delay=1.0,       # 初始延迟（秒）
        max_delay=10.0,       # 最大延迟
        backoff_factor=2.0,   # 指数退避因子
        retry_on=[500, 502, 503, 504, 429]  # 重试的状态码
    )
)
```

### 4. 超时设置

```python
client = LiuHaoClient(
    base_url="http://127.0.0.1:8080",
    timeout=30,           # 全局超时（秒）
    connect_timeout=5,    # 连接超时
    read_timeout=30,      # 读取超时
    write_timeout=10      # 写入超时
)
```

### 5. 日志与调试

```python
import logging
from liuhao import LiuHaoClient

# 启用调试日志
logging.basicConfig(level=logging.DEBUG)

client = LiuHaoClient(
    base_url="http://127.0.0.1:8080",
    debug=True  # 启用详细请求/响应日志
)
```

### 6. 安全最佳实践

- 不要在代码中硬编码 API Key
- 使用环境变量或密钥管理服务
- 遵循最小权限原则
- 定期轮换 API Key
- 使用 HTTPS 连接生产环境

---

## 下一步

- [API 参考](../api-reference/agents_api.md) - 完整的 API 文档
- [智能体用户指南](agent_guide.md) - 智能体操作详解
- [插件开发](#) - 使用 SDK 开发插件
