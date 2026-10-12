# LiuHao AI OS - Rust SDK

官方 LiuHao AI OS 平台 Rust SDK。

## 特性

- 完全异步 (tokio + reqwest)
- Builder 模式配置客户端
- Serde 类型安全的 JSON 序列化
- 指数退避重试机制
- thiserror 定义的完整错误类型
- 支持 Agents、Memory、Knowledge API
- Rust Edition 2021

## 安装

将以下内容添加到 `Cargo.toml`:

```toml
[dependencies]
liuhao-sdk = "0.1.0"
tokio = { version = "1.0", features = ["full"] }
```

## 快速开始

```rust
use liuhao_sdk::LiuHaoClient;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let client = LiuHaoClient::builder()
        .api_key("your-api-key")
        .build()?;

    // 列出所有 Agent
    let agents = client.agents().list().await?;
    println!("Found {} agents", agents.len());

    // 创建 Agent
    let agent = client.agents().create_simple("my-agent", "gpt-4").await?;
    println!("Created agent: {}", agent.name);

    // 执行 Agent
    let result = client.agents().execute(&agent.id, "Hello, how are you?").await?;
    println!("{}", result);

    Ok(())
}
```

## 配置选项

```rust
use std::time::Duration;
use liuhao_sdk::{LiuHaoConfig, LiuHaoClient};

let config = LiuHaoConfig::builder()
    .api_key("your-api-key")
    .base_url("https://api.liuhao.ai/v1")
    .timeout(Duration::from_secs(30))
    .max_retries(3)
    .initial_retry_delay(Duration::from_millis(1000))
    .retry_backoff_multiplier(2.0)
    .user_agent("MyApp/1.0")
    .build()?;

let client = LiuHaoClient::new(config)?;
```

## Memory API

```rust
use liuhao_sdk::types::StoreMemoryRequest;

// 存储记忆
let memory = client.memory().store(StoreMemoryRequest {
    content: "User prefers dark mode".to_string(),
    memory_type: "preference".to_string(),
    category: Some("ui".to_string()),
    importance: 0.8,
    ..Default::default()
}).await?;

// 搜索记忆
let results = client.memory().search("dark mode").await?;
println!("Found {} memories", results.len());

// 列出记忆
let all_memories = client.memory().list().await?;
```

## Knowledge API

```rust
use liuhao_sdk::types::CreateKnowledgeBaseRequest;

// 创建知识库
let kb = client.knowledge().create_base(CreateKnowledgeBaseRequest {
    name: "my-docs".to_string(),
    description: Some("My documentation knowledge base".to_string()),
    ..Default::default()
}).await?;

// 上传文档
client.knowledge().upload_document(
    &kb.id,
    "README.md",
    "# My Project\n..."
).await?;

// 搜索知识库
let results = client.knowledge().search(&kb.id, "how to install").await?;
for result in results {
    println!("Score: {:.3}, Content: {}", result.score, result.content);
}
```

## 错误处理

SDK 使用 `thiserror` 定义完整的错误枚举：

```rust
use liuhao_sdk::LiuHaoError;

match client.agents().get("nonexistent-id").await {
    Ok(agent) => println!("Agent: {}", agent.name),
    Err(LiuHaoError::NotFound(msg)) => println!("Agent not found: {}", msg),
    Err(LiuHaoError::RateLimit { message, retry_after_seconds }) => {
        println!("Rate limited, retry after {}s: {}", retry_after_seconds, message);
    }
    Err(LiuHaoError::API { status, message, request_id }) => {
        println!("API error {}: {} (request: {:?})", status, message, request_id);
    }
    Err(e) => println!("Error: {}", e),
}
```

## 重试策略

SDK 默认对以下情况进行重试（最多 3 次）：

- 网络超时
- 连接错误
- 速率限制 (429)
- 服务端错误 (502, 503, 504)

重试采用指数退避策略，初始延迟 1 秒，每次重试延迟翻倍。

可以使用 `is_retryable()` 方法检查错误是否可重试：

```rust
if let Err(e) = client.agents().list().await {
    if e.is_retryable() {
        // 自定义重试逻辑
    }
}
```

## 许可证

详见项目根目录的 LICENSE 文件。
