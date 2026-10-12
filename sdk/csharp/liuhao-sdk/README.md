# LiuHao AI OS — C# (.NET) SDK

官方 C# (.NET 6+) SDK，用于访问 LiuHao AI OS 平台。提供强类型、全异步的客户端，覆盖 Agent 管理、记忆系统（Memory）、知识库（Knowledge Base）与任务执行。

## 特性

- **.NET 6+**，启用 nullable reference types 与 implicit usings
- **全异步 API**（`async` / `await`，支持 `CancellationToken`）
- **Bearer Token 认证**，支持自定义请求头、组织 / 项目多租户头
- **指数退避自动重试**（默认重试 429 / 500 / 502 / 503 / 504）
- **结构化异常体系**（`AuthenticationException`、`RateLimitException`、`NotFoundException` 等）
- **`System.Text.Json` 序列化**，snake_case 枚举与显式属性名映射，零第三方依赖
- **现代 C# 特性**：`record` 模型、`required` 成员、switch 表达式
- 完整的 XML 文档注释（生成 `.xml` 文档文件，支持 IDE 智能提示）

## 安装

将项目添加为引用（或后续通过 NuGet 包 `LiuHao.Sdk`）：

```bash
dotnet add reference path/to/liuhao-sdk/LiuHaoSdk.csproj
```

要求：.NET SDK 6.0 或更高版本。

## 快速开始

```csharp
using LiuHao.Sdk;
using LiuHao.Sdk.Models;

// 方式一：显式传入 API Key
using var client = new LiuHaoClient(apiKey: "your-api-key");

// 方式二：通过环境变量（LIUHAO_API_KEY / LIUHAO_BASE_URL ...）
using var client2 = new LiuHaoClient();

// 方式三：完整配置
var config = new LiuHaoConfig
{
    ApiKey = "your-api-key",
    BaseUrl = "https://api.liuhao.ai/v1",
    Timeout = TimeSpan.FromSeconds(30),
    MaxRetries = 3,
};
using var client3 = new LiuHaoClient(config: config);
```

### Agent 管理与执行

```csharp
// 创建 Agent
var agent = await client.Agents.CreateAsync(
    name: "research-assistant",
    description: "深度研究助手",
    model: "default",
    systemPrompt: "你是一位严谨的研究助手。",
    tools: new[] { "web_search", "summarize" });

// 列出 Agent
var page = await client.Agents.ListAsync(limit: 20, offset: 0, status: AgentStatus.Active);
foreach (var a in page.Items)
{
    Console.WriteLine($"{a.Id}: {a.Name} ({a.Status})");
}

// 执行任务
var run = await client.Agents.ExecuteAsync(agent.Id, "总结 LiuHao AI OS 的架构");

// 轮询运行状态
var status = await client.Agents.GetRunAsync(agent.Id, run.Id);

// 取消 / 指标
await client.Agents.CancelRunAsync(agent.Id, run.Id);
var metrics = await client.Agents.GetMetricsAsync(agent.Id);
```

### 记忆系统（Memory）

```csharp
// 存储记忆
var entry = await client.Memory.StoreAsync(
    content: "用户偏好简洁的中文回答",
    category: MemoryCategory.Preference,
    importance: MemoryImportance.High,
    ns: "user-123",
    tags: new[] { "style", "language" });

// 语义搜索
var results = await client.Memory.SearchAsync(
    query: "用户喜欢什么样的回答风格？",
    limit: 5,
    ns: "user-123",
    threshold: 0.7);

foreach (var hit in results.Results)
{
    Console.WriteLine($"[{hit.Score:F2}] {hit.Entry.Content}");
}

// 混合搜索 / 批量操作 / 命名空间
var hybrid = await client.Memory.HybridSearchAsync("回答风格", keywordWeight: 0.3);
await client.Memory.BulkStoreAsync(new[]
{
    new MemoryStoreOptions { Content = "事实一", Category = MemoryCategory.Fact },
    new MemoryStoreOptions { Content = "事实二", Category = MemoryCategory.Fact },
});
var namespaces = await client.Memory.ListNamespacesAsync();
```

### 知识库（Knowledge Base）

```csharp
// 创建知识库
var kb = await client.Knowledge.CreateBaseAsync(
    name: "产品文档库",
    description: "所有产品手册与 FAQ");

// 摄入文档（格式自动从文件名推断）
var doc = await client.Knowledge.IngestAsync(
    baseId: kb.Id,
    filename: "getting-started.md",
    content: "# 快速开始\n...");

// 列出文档
var docs = await client.Knowledge.ListDocumentsAsync(kb.Id, status: DocumentStatus.Ready);

// 语义搜索
var hits = await client.Knowledge.SearchAsync(
    baseId: kb.Id,
    query: "如何配置 API Key？",
    mode: SearchMode.Hybrid,
    limit: 5);

// 跨库搜索
var all = await client.Knowledge.MultiBaseSearchAsync(
    new[] { kb.Id, "other-base-id" },
    "部署流程");

// 集合（Collection）管理
var collection = await client.Knowledge.CreateCollectionAsync(kb.Id, "入门指南");
await client.Knowledge.AddDocumentToCollectionAsync(kb.Id, collection.Id, doc.Id);
```

### 健康检查与平台信息

```csharp
var health = await client.HealthCheckAsync();
Console.WriteLine($"平台状态: {health.Status}, 版本: {health.Version}");

var info = await client.GetApiInfoAsync();
```

## 错误处理

SDK 将 HTTP 错误映射为结构化异常，均继承自 `LiuHaoException`：

| HTTP 状态码 | 异常类型 |
|---|---|
| 400 / 422 | `ValidationException`（含 `FieldErrors`） |
| 401 / 403 | `AuthenticationException` |
| 404 | `NotFoundException`（含 `ResourceType` / `ResourceId`） |
| 429 | `RateLimitException`（含 `RetryAfter`） |
| 5xx / 其他 | `ApiException` |
| 请求超时 | `LiuHaoTimeoutException` |

```csharp
try
{
    var agent = await client.Agents.GetAsync("missing-id");
}
catch (NotFoundException ex)
{
    Console.WriteLine($"资源不存在: {ex.ResourceType}/{ex.ResourceId} [{ex.RequestId}]");
}
catch (RateLimitException ex)
{
    await Task.Delay(ex.RetryAfter ?? TimeSpan.FromSeconds(5));
}
catch (LiuHaoException ex)
{
    Console.WriteLine($"SDK 错误: {ex}");
}
```

## 配置项

`LiuHaoConfig` 支持以下设置，均可通过环境变量（前缀 `LIUHAO_`）配置：

| 属性 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `ApiKey` | `LIUHAO_API_KEY` | — | Bearer 认证密钥 |
| `BaseUrl` | `LIUHAO_BASE_URL` | `https://api.liuhao.ai/v1` | API 基础地址 |
| `Timeout` | `LIUHAO_TIMEOUT` | 30s | 单次请求超时 |
| `MaxRetries` | `LIUHAO_MAX_RETRIES` | 3 | 最大重试次数 |
| `RetryBackoffFactor` | `LIUHAO_RETRY_BACKOFF_FACTOR` | 0.5s | 退避基数（`factor * 2^n`） |
| `RetryStatusCodes` | `LIUHAO_RETRY_STATUS_CODES` | 429,500,502,503,504 | 触发重试的状态码 |
| `Debug` | `LIUHAO_DEBUG` | `false` | 调试模式 |
| `VerifySsl` | `LIUHAO_VERIFY_SSL` | `true` | SSL 证书校验 |
| `Proxy` | `LIUHAO_PROXY` | — | HTTP 代理地址 |
| `OrganizationId` | `LIUHAO_ORGANIZATION_ID` | — | 多租户组织头 |
| `ProjectId` | `LIUHAO_PROJECT_ID` | — | 项目 / 租户头 |

## 项目结构

```
liuhao-sdk/
├── LiuHaoSdk.csproj          # .NET 6 项目文件
├── src/
│   ├── LiuHaoClient.cs       # 主客户端（HttpClient、认证、重试、错误映射）
│   ├── LiuHaoConfig.cs       # 配置（环境变量加载、验证、克隆）
│   ├── Exceptions.cs         # 异常体系与状态码映射
│   ├── Models/
│   │   ├── Agent.cs          # Agent / AgentRun / AgentMetrics 及选项
│   │   ├── Memory.cs         # MemoryEntry / 搜索结果 / 命名空间
│   │   ├── Knowledge.cs      # 知识库 / 文档 / 集合 / 搜索结果
│   │   ├── Task.cs           # 异步任务模型
│   │   ├── ApiResponse.cs    # 分页 / 搜索 / 错误 / 健康检查响应
│   │   └── JsonConverters.cs # snake_case 枚举转换器与 JSON 配置
│   └── Resources/
│       ├── AgentResource.cs      # Agent CRUD、执行、指标
│       ├── MemoryResource.cs     # 记忆存取、搜索、批量、命名空间
│       └── KnowledgeResource.cs  # 知识库、文档摄入、搜索、集合
└── README.md
```

## 构建

```bash
dotnet build liuhao-sdk/LiuHaoSdk.csproj
```

## 许可证

MIT
