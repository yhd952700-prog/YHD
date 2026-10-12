package com.liuhao.sdk.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * API 统一响应包装。
 *
 * 服务端所有接口均返回如下结构：
 *
 * ```json
 * {
 *   "success": true,
 *   "data": { ... },
 *   "error": null,
 *   "request_id": "req_abc123"
 * }
 * ```
 *
 * @param T 业务数据类型。
 * @property success   请求是否成功。
 * @property data      业务数据载荷，失败时为 `null`。
 * @property error     错误详情，成功时为 `null`。
 * @property requestId 请求追踪 ID。
 */
@Serializable
public data class ApiResponse<T>(
    val success: Boolean = true,
    val data: T? = null,
    val error: ApiError? = null,
    @SerialName("request_id") val requestId: String? = null,
) {
    /**
     * 取出业务数据，若请求失败或数据为空则返回 `null`。
     */
    public fun dataOrNull(): T? = if (success) data else null
}

/**
 * API 错误详情。
 *
 * @property code       业务错误码，如 `agent_not_found`。
 * @property message    人类可读的错误描述。
 * @property details    字段级校验错误（可选）。
 * @property retryAfter 限流场景下建议的等待秒数（可选）。
 */
@Serializable
public data class ApiError(
    val code: String? = null,
    val message: String = "未知错误",
    val details: Map<String, List<String>>? = null,
    @SerialName("retry_after") val retryAfter: Long? = null,
)

/**
 * 分页请求参数。
 *
 * @property page     页码，从 1 开始。
 * @property pageSize 每页数量，默认 20，最大 100。
 * @property cursor   游标分页的游标（与 page 二选一，优先使用游标）。
 */
@Serializable
public data class PageRequest(
    val page: Int = 1,
    @SerialName("page_size") val pageSize: Int = 20,
    val cursor: String? = null,
) {
    init {
        require(page >= 1) { "page 必须 >= 1" }
        require(pageSize in 1..100) { "pageSize 必须在 1..100 之间" }
    }

    /** 转换为 URL 查询参数 Map。 */
    public fun toQueryParams(): Map<String, String> = buildMap {
        cursor?.let { put("cursor", it) } ?: put("page", page.toString())
        put("page_size", pageSize.toString())
    }
}

/**
 * 分页响应包装。
 *
 * @param T 列表元素类型。
 * @property items      当前页数据列表。
 * @property total      总记录数（游标分页时可能为 `null`）。
 * @property page       当前页码。
 * @property pageSize   每页数量。
 * @property hasMore    是否还有下一页。
 * @property nextCursor 下一页游标，`null` 表示没有更多数据。
 */
@Serializable
public data class PageResponse<T>(
    val items: List<T> = emptyList(),
    val total: Long? = null,
    val page: Int = 1,
    @SerialName("page_size") val pageSize: Int = 20,
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("next_cursor") val nextCursor: String? = null,
) {
    /** 是否为空页。 */
    public fun isEmpty(): Boolean = items.isEmpty()

    /** 当前页元素数量。 */
    public val size: Int get() = items.size

    /**
     * 生成下一页请求参数；无更多数据时返回 `null`。
     */
    public fun nextPageRequest(): PageRequest? = when {
        !hasMore -> null
        nextCursor != null -> PageRequest(pageSize = pageSize, cursor = nextCursor)
        else -> PageRequest(page = page + 1, pageSize = pageSize)
    }
}

/**
 * 删除操作结果。
 *
 * @property deleted  是否删除成功。
 * @property id       被删除资源的 ID。
 */
@Serializable
public data class DeleteResult(
    val deleted: Boolean = true,
    val id: String? = null,
)
