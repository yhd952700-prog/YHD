package com.liuhao.sdk

/**
 * LiuHao SDK 统一异常体系（密封类）。
 *
 * 所有从 SDK 抛出的异常都继承自 [LiuHaoException]，调用方可以使用
 * `when` 表达式穷尽处理：
 *
 * ```kotlin
 * try {
 *     client.agents.get("agent_123")
 * } catch (e: LiuHaoException) {
 *     when (e) {
 *         is LiuHaoException.Authentication -> refreshToken()
 *         is LiuHaoException.RateLimited    -> backoff(e.retryAfterSeconds)
 *         is LiuHaoException.NotFound       -> showEmpty()
 *         else                              -> report(e)
 *     }
 * }
 * ```
 *
 * @property code      服务端返回的业务错误码（可能为 `null`）。
 * @property requestId 服务端返回的请求追踪 ID，便于排查问题。
 */
public sealed class LiuHaoException(
    message: String,
    public val code: String? = null,
    public val requestId: String? = null,
    cause: Throwable? = null,
) : RuntimeException(message, cause) {

    /**
     * 认证失败（HTTP 401）。
     *
     * 通常为 API Key 失效、过期或权限不足。
     */
    public class Authentication(
        message: String = "认证失败：API Key 无效或已过期",
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 权限不足（HTTP 403）。
     *
     * API Key 有效，但没有访问目标资源的权限。
     */
    public class PermissionDenied(
        message: String = "权限不足",
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 资源不存在（HTTP 404）。
     */
    public class NotFound(
        message: String = "资源不存在",
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 请求参数不合法（HTTP 400 / 422）。
     *
     * @property fieldErrors 字段级校验错误，键为字段名，值为错误描述列表。
     */
    public class Validation(
        message: String = "请求参数不合法",
        public val fieldErrors: Map<String, List<String>> = emptyMap(),
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 触发限流（HTTP 429）。
     *
     * @property retryAfterSeconds 服务端建议的重试等待秒数，可能为 `null`。
     */
    public class RateLimited(
        message: String = "请求过于频繁，已被限流",
        public val retryAfterSeconds: Long? = null,
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 资源冲突（HTTP 409）。
     *
     * 例如创建同名 Agent、并发修改同一资源。
     */
    public class Conflict(
        message: String = "资源冲突",
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 服务端内部错误（HTTP 5xx）。
     */
    public class Server(
        message: String = "服务端内部错误",
        public val statusCode: Int = 500,
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    /**
     * 网络层错误（连接失败、超时、DNS 解析失败等）。
     */
    public class Network(
        message: String = "网络请求失败",
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, null, requestId, cause)

    /**
     * 响应体反序列化失败。
     */
    public class Serialization(
        message: String = "响应数据解析失败",
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, null, requestId, cause)

    /**
     * 请求超时。
     */
    public class Timeout(
        message: String = "请求超时",
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, null, requestId, cause)

    /**
     * 未归类的其他错误。
     */
    public class Unknown(
        message: String = "未知错误",
        public val statusCode: Int? = null,
        code: String? = null,
        requestId: String? = null,
        cause: Throwable? = null,
    ) : LiuHaoException(message, code, requestId, cause)

    public companion object {
        /**
         * 根据 HTTP 状态码构造对应的异常实例。
         *
         * @param statusCode HTTP 状态码。
         * @param message    错误消息。
         * @param code       业务错误码。
         * @param requestId  请求追踪 ID。
         * @param retryAfter 限流场景下的重试等待秒数。
         */
        internal fun fromStatusCode(
            statusCode: Int,
            message: String,
            code: String? = null,
            requestId: String? = null,
            retryAfter: Long? = null,
        ): LiuHaoException = when (statusCode) {
            400, 422 -> Validation(message = message, code = code, requestId = requestId)
            401 -> Authentication(message = message, code = code, requestId = requestId)
            403 -> PermissionDenied(message = message, code = code, requestId = requestId)
            404 -> NotFound(message = message, code = code, requestId = requestId)
            409 -> Conflict(message = message, code = code, requestId = requestId)
            429 -> RateLimited(
                message = message,
                retryAfterSeconds = retryAfter,
                code = code,
                requestId = requestId,
            )
            in 500..599 -> Server(
                message = message,
                statusCode = statusCode,
                code = code,
                requestId = requestId,
            )
            else -> Unknown(
                message = message,
                statusCode = statusCode,
                code = code,
                requestId = requestId,
            )
        }
    }
}
