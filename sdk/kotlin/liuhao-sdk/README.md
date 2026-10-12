# LiuHao AI OS Kotlin SDK

LiuHao AI OS 官方 Kotlin 客户端，基于 **Ktor Client** 与 **kotlinx.coroutines**，
提供类型安全、协程原生的 API 访问能力，覆盖 Agent、Memory、Knowledge 三大核心资源。

## 环境要求

- JDK 17+
- Kotlin 1.9+
- Gradle 8+（使用项目内 Gradle Wrapper 亦可）

## 安装

### Gradle (Kotlin DSL)

```kotlin
repositories {
    mavenCentral()
}

dependencies {
    implementation("com.liuhao.sdk:liuhao-sdk:0.1.0")
    // 运行时日志实现（任选其一）
    runtimeOnly("ch.qos.logback:logback-classic:1.5.6")
}
```

### 本地源码集成

```kotlin
// settings.gradle.kts
includeBuild("sdk/kotlin/liuhao-sdk")
```

```kotlin
// build.gradle.kts
dependencies {
    implementation("com.liuhao.sdk:liuhao-sdk")
}
```

### 构建

```bash
./gradlew build        # 编译 + 测试
./gradlew publishToMavenLocal  # 发布到本地 Maven 仓库
```

## 快速开始

```kotlin
import com.liuhao.sdk.LiuHaoClient
import com.liuhao.sdk.models.*

suspend fun main() {
    // 1. 创建客户端（仅需 API Key）
    LiuHaoClient("lh_live_xxx").use { client ->

        // 2. 创建 Agent
        val agent = client.agents.create(
            AgentCreateRequest(
                name = "知识库助手",
                instructions = "基于提供的资料回答问题，不确定就说不知道",
                modelConfig = AgentModelConfig(model = "gpt-4o", temperature = 0.3),
            )
        )

        // 3. 同步执行
        val result = client.agents.execute(
            agent.id,
            AgentExecuteRequest(input = "介绍一下你自己"),
        )
        println(result.output)

        // 4. 异步执行 + 轮询等待
        val task = client.agents.submitAndWait(
            agent.id,
            AgentExecuteRequest(input = "写一份周报大纲"),
        )
        println("任务 ${task.id} 状态：${task.status}")
    }
}
```

## 自定义配置

```kotlin
import kotlin.time.Duration.Companion.seconds

val client = LiuHaoClient.create("lh_live_xxx") {
    baseUrl("https://api.liuhao.ai")
        .timeout(60.seconds)
        .maxRetries(5)
        .enableLogging(true)
        .defaultAgentId("agent_abc123")
}
```

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `baseUrl` | `https://api.liuhao.ai` | API 基础地址 |
| `timeout` | 30s | 请求总超时 |
| `connectTimeout` | 10s | TCP 连接超时 |
| `maxRetries` | 3 | 5xx/429/网络错误自动重试（指数退避） |
| `enableLogging` | false | HTTP 日志（自动脱敏 Authorization 头） |
| `defaultAgentId` | null | 默认 Agent，调用时可省略 agentId |

## 资源一览

### Agent（`client.agents`）

| 方法 | 说明 |
| --- | --- |
| `create / get / update / delete` | 生命周期管理 |
| `list / listAll` | 分页查询 / Flow 全量遍历 |
| `activate / pause / archive` | 状态流转 |
| `execute` | 同步执行，直接返回结果 |
| `submit / submitAndWait` | 异步任务 |
| `getTask / cancelTask / waitTask / listTasks` | 任务管理 |
| `createSession / getSession / listSessions` | 多轮会话 |

### Memory（`client.memories`）

| 方法 | 说明 |
| --- | --- |
| `create / remember` | 写入记忆 |
| `createBatch / createBatchParallel` | 批量写入（自动分片并发） |
| `search / recall` | 向量语义检索 |
| `clearShortTerm` | 清理短期记忆 |
| `consolidate` | 压缩合并长期记忆 |

### Knowledge（`client.knowledge`）

| 方法 | 说明 |
| --- | --- |
| `create / get / update / delete` | 知识库 CRUD |
| `addText / addFromUrl` | 添加文档 |
| `waitDocumentReady` | 轮询等待向量化完成 |
| `query / queryMultiple` | 单库 / 跨库语义检索 |
| `buildContext` | 检索并拼接为提示词上下文 |

## 错误处理

所有异常均为 `LiuHaoException` 密封类子类，支持穷尽式 `when`：

```kotlin
try {
    client.agents.get("agent_not_exist")
} catch (e: LiuHaoException) {
    when (e) {
        is LiuHaoException.Authentication  -> println("API Key 失效：${e.requestId}")
        is LiuHaoException.RateLimited     -> println("限流，${e.retryAfterSeconds}s 后重试")
        is LiuHaoException.NotFound        -> println("资源不存在")
        is LiuHaoException.Validation      -> println("参数错误：${e.fieldErrors}")
        is LiuHaoException.Network         -> println("网络异常：${e.message}")
        else                               -> println("其他错误：${e.message}")
    }
}
```

SDK 内置重试策略：对 `5xx`、`429` 与 `IOException` 自动按指数退避重试
（可通过 `maxRetries` 配置）；`4xx` 业务错误与协程取消不会重试。

## 项目结构

```
liuhao-sdk/
├── build.gradle.kts                    # Gradle 构建配置
├── settings.gradle.kts
└── src/main/kotlin/com/liuhao/sdk/
    ├── LiuHaoClient.kt                 # 主客户端（Ktor、鉴权、重试、错误管线）
    ├── LiuHaoConfig.kt                 # 配置 data class + Builder
    ├── LiuHaoException.kt              # 密封异常体系
    ├── models/                         # @Serializable 数据模型
    │   ├── ApiResponse.kt              #   响应包装 / 分页
    │   ├── Agent.kt                    #   Agent 与执行模型
    │   ├── Memory.kt                   #   记忆模型
    │   ├── Knowledge.kt                #   知识库模型
    │   └── Task.kt                     #   任务 / 会话 / 健康检查
    └── resources/                      # API 资源层
        ├── AgentResource.kt
        ├── MemoryResource.kt
        └── KnowledgeResource.kt
```

## License

Apache License 2.0
