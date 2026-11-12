package com.liuhao.sdk.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * 记忆类型枚举。
 */
@Serializable
public enum class MemoryType {
    /** 短期记忆：会话级别，过期自动清理。 */
    @SerialName("short_term") SHORT_TERM,

    /** 长期记忆：持久化存储，支持语义检索。 */
    @SerialName("long_term") LONG_TERM,

    /** 情景记忆：记录具体事件与交互。 */
    @SerialName("episodic") EPISODIC,

    /** 语义记忆：结构化的事实与偏好。 */
    @SerialName("semantic") SEMANTIC,
}

/**
 * 一条记忆记录。
 *
 * @property id          记忆唯一标识，形如 `mem_xxx`。
 * @property agentId     所属 Agent ID。
 * @property sessionId   所属会话 ID（短期记忆必填）。
 * @property type        记忆类型。
 * @property content     记忆正文。
 * @property embedding   向量嵌入（仅内部使用，通常不返回）。
 * @property importance  重要性评分 0.0 ~ 1.0，越高越不容易被遗忘。
 * @property metadata    自定义元数据。
 * @property expiresAt   过期时间（ISO-8601），`null` 表示永不过期。
 * @property createdAt   创建时间。
 * @property updatedAt   最近更新时间。
 */
@Serializable
public data class Memory(
    val id: String,
    @SerialName("agent_id") val agentId: String,
    @SerialName("session_id") val sessionId: String? = null,
    val type: MemoryType = MemoryType.LONG_TERM,
    val content: String,
    val embedding: List<Float>? = null,
    val importance: Double = 0.5,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * 写入记忆的请求体。
 *
 * @property agentId    所属 Agent ID（必填）。
 * @property content    记忆内容（必填）。
 * @property type       记忆类型，默认长期记忆。
 * @property sessionId  会话 ID。
 * @property importance 重要性评分，默认 0.5。
 * @property metadata   自定义元数据。
 * @property ttlSeconds 存活时间（秒），`null` 表示永不过期。
 */
@Serializable
public data class MemoryCreateRequest(
    @SerialName("agent_id") val agentId: String,
    val content: String,
    val type: MemoryType = MemoryType.LONG_TERM,
    @SerialName("session_id") val sessionId: String? = null,
    val importance: Double = 0.5,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("ttl_seconds") val ttlSeconds: Long? = null,
) {
    init {
        require(content.isNotBlank()) { "content 不能为空" }
        require(importance in 0.0..1.0) { "importance 必须在 0.0~1.0 之间" }
    }
}

/**
 * 更新记忆的请求体。
 */
@Serializable
public data class MemoryUpdateRequest(
    val content: String? = null,
    val importance: Double? = null,
    val metadata: Map<String, String>? = null,
    @SerialName("ttl_seconds") val ttlSeconds: Long? = null,
)

/**
 * 记忆检索请求（语义搜索）。
 *
 * @property agentId     限定 Agent ID。
 * @property query       查询文本（必填）。
 * @property type        限定记忆类型，`null` 表示全部类型。
 * @property topK        返回的最大结果数，默认 5。
 * @property minScore    最低相似度阈值 0.0 ~ 1.0。
 * @property includeMetadata 是否在结果中包含元数据。
 */
@Serializable
public data class MemorySearchRequest(
    @SerialName("agent_id") val agentId: String? = null,
    val query: String,
    val type: MemoryType? = null,
    @SerialName("top_k") val topK: Int = 5,
    @SerialName("min_score") val minScore: Double = 0.0,
    @SerialName("include_metadata") val includeMetadata: Boolean = true,
) {
    init {
        require(query.isNotBlank()) { "query 不能为空" }
        require(topK in 1..100) { "topK 必须在 1..100 之间" }
    }
}

/**
 * 记忆检索结果项。
 *
 * @property memory 命中的记忆。
 * @property score  相似度得分 0.0 ~ 1.0。
 */
@Serializable
public data class MemorySearchHit(
    val memory: Memory,
    val score: Double,
)

/**
 * 记忆检索响应。
 *
 * @property hits   命中列表，按相似度降序。
 * @property query  原始查询文本。
 * @property tookMs 检索耗时（毫秒）。
 */
@Serializable
public data class MemorySearchResponse(
    val hits: List<MemorySearchHit> = emptyList(),
    val query: String? = null,
    @SerialName("took_ms") val tookMs: Long? = null,
)

/**
 * 批量写入记忆的请求体。
 *
 * @property memories 待写入的记忆列表（最多 100 条）。
 * @property extra    请求级扩展参数。
 */
@Serializable
public data class MemoryBatchCreateRequest(
    val memories: List<MemoryCreateRequest>,
    val extra: JsonObject? = null,
) {
    init {
        require(memories.isNotEmpty() && memories.size <= 100) { "批量写入数量必须在 1~100 之间" }
    }
}

/**
 * 批量写入结果。
 *
 * @property created  成功创建的数量。
 * @property failed   失败的数量。
 * @property ids      成功创建的记忆 ID 列表。
 */
@Serializable
public data class MemoryBatchCreateResponse(
    val created: Int = 0,
    val failed: Int = 0,
    val ids: List<String> = emptyList(),
)
