# LiuHao AI OS - Java SDK

官方 LiuHao AI OS 平台 Java SDK。

## 特性

- Builder 模式配置客户端
- OkHttp 高性能 HTTP 客户端
- Gson JSON 序列化
- 指数退避重试机制
- 完整的异常层次结构
- 支持 Agents、Memory、Knowledge、Tasks、Health API
- Java 11+ 兼容

## 安装

### Maven

```xml
<dependency>
    <groupId>com.liuhao</groupId>
    <artifactId>liuhao-sdk</artifactId>
    <version>1.0.0</version>
</dependency>
```

### Gradle

```groovy
implementation 'com.liuhao:liuhao-sdk:1.0.0'
```

## 快速开始

```java
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.Agent;
import java.util.List;

// 创建客户端
LiuHaoClient client = LiuHaoClient.builder()
    .apiKey("your-api-key")
    .baseUrl("https://api.liuhao.ai/v1")
    .build();

// 列出所有 Agent
List<Agent> agents = client.agents().list();
for (Agent agent : agents) {
    System.out.println(agent.getName() + " - " + agent.getStatus());
}

// 创建 Agent
Agent agent = client.agents().create("my-agent", "gpt-4");

// 执行 Agent
String result = client.agents().execute(agent.getId(), "Hello, how are you?");
System.out.println(result);
```

## 配置选项

```java
LiuHaoConfig config = LiuHaoConfig.builder()
    .apiKey("your-api-key")
    .baseUrl("https://api.liuhao.ai/v1")
    .timeout(30, TimeUnit.SECONDS)
    .maxRetries(3)
    .initialRetryDelayMs(1000)
    .retryBackoffMultiplier(2.0)
    .userAgent("MyApp/1.0")
    .build();

LiuHaoClient client = LiuHaoClient.builder()
    .config(config)
    .build();
```

## Memory API

```java
import com.liuhao.sdk.models.MemoryItem;
import java.util.List;

// 存储记忆
MemoryItem memory = client.memory().store(
    "User prefers dark mode",
    "preference",
    "ui",
    0.8,
    null
);

// 搜索记忆
List<MemoryItem> results = client.memory().search("dark mode");

// 列出记忆
List<MemoryItem> allMemories = client.memory().list();
```

## Knowledge API

```java
import com.liuhao.sdk.models.KnowledgeBase;
import java.util.List;

// 创建知识库
KnowledgeBase kb = client.knowledge().createBase(
    "my-docs",
    "My documentation knowledge base"
);

// 上传文档
client.knowledge().uploadDocument(kb.getId(), "README.md", "# My Project\n...");

// 搜索知识库
List<Map<String, Object>> results = client.knowledge().search(kb.getId(), "how to install");
```

## Tasks API

```java
import com.liuhao.sdk.models.Task;

// 创建任务
Task task = client.tasks().create("training", Map.of("epochs", 10));

// 等待任务完成
task = client.tasks().waitForCompletion(task.getId());

if (task.isSuccess()) {
    System.out.println("Task completed: " + task.getResultData());
}
```

## 异常处理

SDK 提供完整的异常层次结构：

- `LiuHaoException` - 基类异常
- `AuthenticationException` - 认证失败 (401)
- `NotFoundException` - 资源不存在 (404)
- `ValidationException` - 请求验证失败 (400)
- `RateLimitException` - 速率限制 (429)
- `APIException` - 服务端错误 (5xx)
- `TimeoutException` - 请求超时

```java
try {
    Agent agent = client.agents().get("nonexistent-id");
} catch (NotFoundException e) {
    System.out.println("Agent not found: " + e.getMessage());
} catch (RateLimitException e) {
    System.out.println("Rate limited, retry after " + e.getRetryAfterSeconds() + "s");
} catch (LiuHaoException e) {
    System.out.println("API error: " + e.getMessage() +
        " (status: " + e.getStatusCode() +
        ", requestId: " + e.getRequestId() + ")");
}
```

## 重试策略

SDK 默认对以下情况进行重试（最多 3 次）：

- 网络错误（超时、连接失败）
- 速率限制 (429)
- 服务端错误 (502, 503, 504)

重试采用指数退避策略，初始延迟 1 秒，每次重试延迟翻倍。

## 许可证

详见项目根目录的 LICENSE 文件。
