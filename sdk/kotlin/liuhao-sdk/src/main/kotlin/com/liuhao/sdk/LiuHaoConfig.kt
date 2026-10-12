package com.liuhao.sdk

import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

/**
 * LiuHao AI OS SDK 客户端配置。
 *
 * 使用 [Builder] 模式构造，示例：
 *
 * ```kotlin
 * val config = LiuHaoConfig.Builder("lh_live_xxx")
 *     .baseUrl("https://api.liuhao.ai")
 *     .timeout(30.seconds)
 *     .maxRetries(3)
 *     .build()
 * ```
 *
 * @property apiKey       API 密钥，用于 Bearer Token 鉴权（必填）。
 * @property baseUrl      API 基础地址，默认指向生产环境。
 * @property timeout      单次 HTTP 请求总超时时间。
 * @property connectTimeout 建立 TCP 连接的超时时间。
 * @property maxRetries   针对可重试错误（5xx、429、网络抖动）的最大重试次数。
 * @property userAgent    自定义 User-Agent 前缀，会附加 SDK 版本信息。
 * @property enableLogging 是否启用 HTTP 请求日志（生产环境建议关闭）。
 * @property defaultAgentId 默认 Agent ID，调用部分接口时可省略显式传参。
 */
public data class LiuHaoConfig internal constructor(
    val apiKey: String,
    val baseUrl: String = DEFAULT_BASE_URL,
    val timeout: Duration = 30.seconds,
    val connectTimeout: Duration = 10.seconds,
    val maxRetries: Int = 3,
    val userAgent: String = "liuhao-sdk-kotlin",
    val enableLogging: Boolean = false,
    val defaultAgentId: String? = null,
) {

    init {
        require(apiKey.isNotBlank()) { "apiKey 不能为空" }
        require(baseUrl.isNotBlank()) { "baseUrl 不能为空" }
        require(!timeout.isNegative() && timeout != Duration.ZERO) { "timeout 必须为正数" }
        require(maxRetries >= 0) { "maxRetries 不能为负数" }
    }

    /** SDK 版本号，随发布流程更新。 */
    public val sdkVersion: String get() = SDK_VERSION

    /** 拼接完整 User-Agent 字符串。 */
    internal fun fullUserAgent(): String = "$userAgent/$SDK_VERSION"

    public companion object {
        public const val DEFAULT_BASE_URL: String = "https://api.liuhao.ai"
        public const val SDK_VERSION: String = "0.1.0"

        /**
         * 使用默认配置创建 [LiuHaoConfig]，仅需提供 API Key。
         */
        @JvmStatic
        public fun of(apiKey: String): LiuHaoConfig = Builder(apiKey).build()
    }

    /**
     * [LiuHaoConfig] 的构建器。
     *
     * @param apiKey API 密钥，必填。
     */
    public class Builder(private val apiKey: String) {
        private var baseUrl: String = DEFAULT_BASE_URL
        private var timeout: Duration = 30.seconds
        private var connectTimeout: Duration = 10.seconds
        private var maxRetries: Int = 3
        private var userAgent: String = "liuhao-sdk-kotlin"
        private var enableLogging: Boolean = false
        private var defaultAgentId: String? = null

        public fun baseUrl(value: String): Builder = apply { baseUrl = value.trimEnd('/') }
        public fun timeout(value: Duration): Builder = apply { timeout = value }
        public fun connectTimeout(value: Duration): Builder = apply { connectTimeout = value }
        public fun maxRetries(value: Int): Builder = apply { maxRetries = value }
        public fun userAgent(value: String): Builder = apply { userAgent = value }
        public fun enableLogging(value: Boolean): Builder = apply { enableLogging = value }
        public fun defaultAgentId(value: String?): Builder = apply { defaultAgentId = value }

        public fun build(): LiuHaoConfig = LiuHaoConfig(
            apiKey = apiKey,
            baseUrl = baseUrl,
            timeout = timeout,
            connectTimeout = connectTimeout,
            maxRetries = maxRetries,
            userAgent = userAgent,
            enableLogging = enableLogging,
            defaultAgentId = defaultAgentId,
        )
    }
}
