package com.liuhao.sdk

import com.liuhao.sdk.models.ApiError
import com.liuhao.sdk.models.ApiResponse
import com.liuhao.sdk.models.HealthStatus
import com.liuhao.sdk.resources.AgentResource
import com.liuhao.sdk.resources.KnowledgeResource
import com.liuhao.sdk.resources.MemoryResource
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpRequestRetry
import io.ktor.client.plugins.HttpRequestTimeoutException
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.auth.Auth
import io.ktor.client.plugins.auth.providers.BearerTokens
import io.ktor.client.plugins.auth.providers.bearer
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.defaultRequest
import io.ktor.client.plugins.logging.LogLevel
import io.ktor.client.plugins.logging.Logging
import io.ktor.client.request.delete
import io.ktor.client.request.get
import io.ktor.client.request.header
import io.ktor.client.request.parameter
import io.ktor.client.request.patch
import io.ktor.client.request.post
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import java.io.Closeable
import java.io.IOException

/**
 * LiuHao AI OS Kotlin SDK 主客户端。
 *
 * 线程安全，可在多个协程间共享；内部基于 Ktor [HttpClient]，所有 API
 * 均为 `suspend` 函数，天然适配 Kotlin 协程。
 *
 * 使用示例：
 *
 * ```kotlin
 * val client = LiuHaoClient("lh_live_xxx")
 * val agent = client.agents.create(
 *     AgentCreateRequest(name = "客服助手", instructions = "你是乐于助人的客服")
 * )
 * val result = client.agents.execute(agent.id, AgentExecuteRequest(input = "你好"))
 * println(result.output)
 * client.close()
 * ```
 *
 * 实现了 [Closeable]，使用完毕后应调用 [close] 释放连接资源；
 * 也可配合 `use {}` 代码块自动关闭。
 *
 * @property config SDK 配置。
 */
public class LiuHaoClient(
    public val config: LiuHaoConfig,
) : Closeable {

    /**
     * 以 API Key 快速构造客户端（使用默认配置）。
     */
    public constructor(apiKey: String) : this(LiuHaoConfig.of(apiKey))

    /** Agent 资源：创建、查询、更新、删除、执行 Agent。 */
    public val agents: AgentResource by lazy { AgentResource(this) }

    /** Memory 资源：读写与语义检索 Agent 记忆。 */
    public val memories: MemoryResource by lazy { MemoryResource(this) }

    /** Knowledge 资源：知识库与文档管理、知识检索。 */
    public val knowledge: KnowledgeResource by lazy { KnowledgeResource(this) }

    // ------------------------------------------------------------------
    // JSON 序列化配置：宽松解析，忽略未知字段，便于服务端平滑升级
    // ------------------------------------------------------------------
    internal val json: Json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        encodeDefaults = true
        explicitNulls = false
        prettyPrint = false
    }

    // ------------------------------------------------------------------
    // 底层 HTTP 客户端
    // ------------------------------------------------------------------
    internal val http: HttpClient = HttpClient(CIO) {
        expectSuccess = false // 错误状态码统一由 handleResponse 处理

        install(ContentNegotiation) {
            json(json)
        }

        install(Auth) {
            bearer {
                loadTokens {
                    BearerTokens(config.apiKey, config.apiKey)
                }
                sendWithoutRequest { request ->
                    request.url.host.isNotEmpty()
                }
            }
        }

        install(HttpTimeout) {
            requestTimeoutMillis = config.timeout.inWholeMilliseconds
            connectTimeoutMillis = config.connectTimeout.inWholeMilliseconds
            socketTimeoutMillis = config.timeout.inWholeMilliseconds
        }

        install(HttpRequestRetry) {
            maxRetries = config.maxRetries
            retryIf { _, response ->
                response.status.value in 500..599 || response.status.value == 429
            }
            retryOnExceptionIf { _, cause ->
                cause is IOException && cause !is CancellationException
            }
            exponentialDelay(base = 2.0, maxDelayMs = 8_000)
            modifyRequest { request ->
                request.headers.append("X-Retry-Attempt", retryCount.toString())
            }
        }

        if (config.enableLogging) {
            install(Logging) {
                level = LogLevel.HEADERS
                sanitizeHeader { header -> header == HttpHeaders.Authorization }
            }
        }

        defaultRequest {
            url(config.baseUrl)
            contentType(ContentType.Application.Json)
            header(HttpHeaders.UserAgent, config.fullUserAgent())
            header("X-SDK-Language", "kotlin")
            header("X-SDK-Version", LiuHaoConfig.SDK_VERSION)
        }
    }

    // ------------------------------------------------------------------
    // 公共 API
    // ------------------------------------------------------------------

    /**
     * 探测服务端健康状态。
     *
     * @return [HealthStatus] 健康检查结果。
     * @throws LiuHaoException.Network 网络不可达时抛出。
     */
    public suspend fun health(): HealthStatus =
        execute(path = "/v1/health") { url -> http.get(url) }

    /**
     * 校验当前 API Key 是否有效。
     *
     * @return 有效返回 `true`；无效抛出 [LiuHaoException.Authentication]。
     */
    public suspend fun verifyCredentials(): Boolean {
        health()
        return true
    }

    // ------------------------------------------------------------------
    // 内部请求执行管线（resources 包内共享）
    // ------------------------------------------------------------------

    /**
     * GET 请求。
     *
     * @param path   以 `/` 开头的 API 路径。
     * @param params 查询参数。
     */
    internal suspend inline fun <reified T> get(
        path: String,
        params: Map<String, String> = emptyMap(),
    ): T = execute(path) { url ->
        http.get(url) { params.forEach { (k, v) -> parameter(k, v) } }
    }

    /** POST 请求。 */
    internal suspend inline fun <reified T> post(path: String, body: Any?): T =
        execute(path) { url -> http.post(url) { body?.let { setBody(it) } } }

    /** PATCH 请求。 */
    internal suspend inline fun <reified T> patch(path: String, body: Any?): T =
        execute(path) { url -> http.patch(url) { body?.let { setBody(it) } } }

    /** DELETE 请求。 */
    internal suspend inline fun <reified T> delete(path: String): T =
        execute(path) { url -> http.delete(url) }

    /**
     * 统一请求执行与错误处理。
     *
     * 职责：
     * 1. 捕获网络层异常并包装为 [LiuHaoException.Network] / [LiuHaoException.Timeout]；
     * 2. 非 2xx 响应解析 [ApiError] 并抛出对应 [LiuHaoException] 子类；
     * 3. 2xx 响应按 [ApiResponse] 包装解析并取出 `data` 字段。
     *
     * @param T     期望的业务数据类型。
     * @param path  API 路径（仅用于错误消息）。
     * @param block 实际发起 HTTP 调用的 lambda。
     */
    internal suspend inline fun <reified T> execute(
        path: String,
        block: suspend (String) -> HttpResponse,
    ): T {
        val response: HttpResponse = try {
            block(path)
        } catch (e: HttpRequestTimeoutException) {
            throw LiuHaoException.Timeout("请求超时：$path", cause = e)
        } catch (e: CancellationException) {
            throw e // 协程取消必须向上传播，不包装
        } catch (e: IOException) {
            throw LiuHaoException.Network("网络请求失败：$path（${e.message}）", cause = e)
        } catch (e: Throwable) {
            throw LiuHaoException.Unknown("未知错误：$path（${e.message}）", cause = e)
        }
        return handleResponse(response, path)
    }

    /**
     * 解析 HTTP 响应。
     */
    internal suspend inline fun <reified T> handleResponse(response: HttpResponse, path: String): T {
        val requestId = response.headers["X-Request-Id"]

        if (!response.status.isSuccess()) {
            val apiError = runCatching { response.body<ApiError>() }.getOrNull()
            throw LiuHaoException.fromStatusCode(
                statusCode = response.status.value,
                message = apiError?.message ?: "HTTP ${response.status.value} 请求失败：$path",
                code = apiError?.code,
                requestId = requestId,
                retryAfter = response.headers["Retry-After"]?.toLongOrNull(),
            )
        }

        return try {
            val envelope = response.body<ApiResponse<T>>()
            if (envelope.success) {
                envelope.data ?: throw LiuHaoException.Serialization(
                    "响应缺少 data 字段：$path",
                    requestId = envelope.requestId ?: requestId,
                )
            } else {
                val err = envelope.error
                throw LiuHaoException.fromStatusCode(
                    statusCode = response.status.value,
                    message = err?.message ?: "业务处理失败",
                    code = err?.code,
                    requestId = envelope.requestId ?: requestId,
                    retryAfter = err?.retryAfter,
                )
            }
        } catch (e: LiuHaoException) {
            throw e
        } catch (e: Throwable) {
            throw LiuHaoException.Serialization(
                "响应解析失败：$path（${e.message}）",
                requestId = requestId,
                cause = e,
            )
        }
    }

    /**
     * 带退避的轮询工具，供资源类等待异步任务完成。
     *
     * @param intervalMs  初始轮询间隔。
     * @param maxAttempts 最大轮询次数。
     * @param fetch       每次轮询执行的查询。
     * @param done        判断任务是否完成的条件。
     */
    internal suspend fun <T> pollUntil(
        intervalMs: Long = 1_000,
        maxAttempts: Int = 60,
        fetch: suspend () -> T,
        done: (T) -> Boolean,
    ): T {
        var attempt = 0
        var backoff = intervalMs
        while (attempt < maxAttempts) {
            val value = fetch()
            if (done(value)) return value
            attempt++
            delay(backoff)
            backoff = (backoff * 3 / 2).coerceAtMost(10_000)
        }
        throw LiuHaoException.Timeout("轮询等待超时（${maxAttempts} 次尝试后仍未完成）")
    }

    /**
     * 关闭客户端并释放底层连接池资源。
     *
     * 关闭后再次调用任何 API 行为未定义。
     */
    override fun close() {
        http.close()
    }

    public companion object {
        /**
         * 使用 [block] 自定义配置后创建客户端。
         *
         * ```kotlin
         * val client = LiuHaoClient.create("lh_live_xxx") {
         *     timeout(60.seconds).maxRetries(5)
         * }
         * ```
         */
        @JvmStatic
        public fun create(
            apiKey: String,
            block: LiuHaoConfig.Builder.() -> LiuHaoConfig.Builder,
        ): LiuHaoClient = LiuHaoClient(LiuHaoConfig.Builder(apiKey).block().build())
    }
}
