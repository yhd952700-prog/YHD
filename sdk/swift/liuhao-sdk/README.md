# LiuHao SDK for Swift

The official Swift SDK for **LiuHao AI OS** — a resource-oriented, fully
`async`/`await` client for agents, memory, and knowledge bases.

- 零第三方依赖（仅 Foundation）
- 全量 Swift Concurrency（`async`/`await`、`Sendable`）
- Bearer Token 认证、自动重试与指数退避
- 强类型 `Codable` 模型，含 DocC 文档注释
- 支持 iOS 15+ / macOS 12+ / tvOS 15+ / watchOS 8+ / visionOS 1+ / Linux

## 安装 (Swift Package Manager)

在 `Package.swift` 中添加：

```swift
dependencies: [
    .package(path: "../sdk/swift/liuhao-sdk")
]
```

或在 Xcode 中：`File > Add Package Dependencies…` 并选择本地目录。

然后在目标中声明依赖：

```swift
.target(
    name: "YourApp",
    dependencies: [
        .product(name: "LiuHaoSDK", package: "liuhao-sdk")
    ]
)
```

## 快速开始

```swift
import LiuHaoSDK

// 方式一：显式 API Key
let client = LiuHaoClient(apiKey: "lh_your_api_key")

// 方式二：完整配置
let config = LiuHaoConfig(
    apiKey: "lh_your_api_key",
    baseURL: URL(string: "https://api.liuhao.ai/v1")!,
    timeout: 30,
    maxRetries: 3,
    debug: true
)
let client = LiuHaoClient(config: config)

// 方式三：环境变量（LIUHAO_API_KEY / LIUHAO_BASE_URL / ...）
let client = try LiuHaoClient.fromEnvironment()
```

## Agents

```swift
// 创建 Agent
let agent = try await client.agents.create(
    name: "Research Assistant",
    description: "Answers research questions",
    model: "liuhao-large",
    systemPrompt: "You are a helpful research assistant.",
    tools: ["web-search"]
)

// 列表 / 获取 / 更新 / 删除
let page = try await client.agents.list(limit: 20, status: .active)
let fetched = try await client.agents.get(agentID: agent.id)
let updated = try await client.agents.update(agentID: agent.id, name: "New Name")
let deleted = try await client.agents.delete(agentID: agent.id)

// 执行并等待结果
let run = try await client.agents.run(agentID: agent.id, input: "Summarize transformers")
let final = try await client.agents.waitForRun(agentID: agent.id, runID: run.id)
print(final.output ?? "")

// 指标
let metrics = try await client.agents.metrics(agentID: agent.id)
print("Success rate: \(metrics.successRate)")
```

## Memory

```swift
// 存储记忆
let entry = try await client.memory.store(
    content: "User prefers dark mode",
    category: .preference,
    importance: .high,
    tags: ["ui"],
    entityID: "user-42"
)

// 语义搜索
let results = try await client.memory.search(query: "UI preferences", limit: 5)
for hit in results {
    print("\(hit.score): \(hit.entry.content)")
}

// 更新 / 删除 / 统计
_ = try await client.memory.update(memoryID: entry.id, importance: .critical)
_ = try await client.memory.delete(memoryID: entry.id)
let stats = try await client.memory.stats()
```

## Knowledge

```swift
// 创建知识库并上传文档
let base = try await client.knowledge.createBase(name: "Product Docs")
let doc = try await client.knowledge.addDocument(
    baseID: base.id,
    filename: "guide.md",
    content: markdown,
    format: .markdown
)

// 混合检索
let hits = try await client.knowledge.search(
    baseID: base.id,
    query: "How do I install the CLI?",
    mode: .hybrid,
    limit: 10
)
```

## 错误处理

所有失败均以 `LiuHaoError` 抛出：

```swift
do {
    let agent = try await client.agents.get(agentID: "missing")
} catch LiuHaoError.notFound(let message, let requestID) {
    print("Not found: \(message) (request: \(requestID ?? "-"))")
} catch LiuHaoError.rateLimited(let message, let retryAfter, _) {
    print("Slow down: \(message), retry after \(retryAfter ?? 0)s")
} catch {
    print(error.localizedDescription)
}
```

| Case | 触发条件 |
| --- | --- |
| `missingAPIKey` | 未提供 API Key |
| `authenticationFailed` | HTTP 401/403 |
| `notFound` | HTTP 404 |
| `validation` | HTTP 400/422 |
| `rateLimited` | HTTP 429（含 `retryAfter`） |
| `api` | 其他 HTTP 错误 |
| `timeout` / `network` | 传输层故障 |
| `decoding` / `invalidResponse` | 响应解析失败 |

瞬时错误（429、5xx、网络问题）会按
`retryBackoffFactor * 2^n` 的指数退避自动重试，最多 `maxRetries` 次。

## 项目结构

```
liuhao-sdk/
├── Package.swift
├── Sources/LiuHaoSDK/
│   ├── LiuHaoClient.swift        # 主客户端（URLSession + async/await）
│   ├── LiuHaoConfig.swift        # 配置与环境变量加载
│   ├── LiuHaoError.swift         # 统一错误类型
│   ├── Models/
│   │   ├── Agent.swift           # Agent / AgentRun / AgentMetrics
│   │   ├── Memory.swift          # MemoryEntry / 搜索结果 / 统计
│   │   ├── Knowledge.swift       # KnowledgeBase / 文档 / 检索
│   │   ├── Task.swift            # 平台任务模型
│   │   ├── ApiResponse.swift     # 分页与通用信封
│   │   └── JSONValue.swift       # 任意 JSON 值的 Codable 封装
│   └── Resources/
│       ├── AgentResource.swift   # Agent CRUD + 执行 + 指标
│       ├── MemoryResource.swift  # 记忆存取 / 搜索 / 维护
│       └── KnowledgeResource.swift # 知识库 / 文档 / 检索
└── Tests/LiuHaoSDKTests/
```

## 运行测试

```bash
cd sdk/swift/liuhao-sdk
swift test
```

## License

MIT
