package com.liuhao.sdk.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * 知识库文档处理状态。
 */
@Serializable
public enum class DocumentStatus {
    /** 已上传，等待处理。 */
    @SerialName("pending") PENDING,

    /** 正在解析与向量化。 */
    @SerialName("processing") PROCESSING,

    /** 处理完成，可被检索。 */
    @SerialName("ready") READY,

    /** 处理失败，可查看 [KnowledgeDocument.errorMessage]。 */
    @SerialName("failed") FAILED,
}

/**
 * 知识库（Knowledge Base）。
 *
 * @property id             知识库唯一标识，形如 `kb_xxx`。
 * @property name           名称。
 * @property description    描述。
 * @property embeddingModel 向量化使用的嵌入模型。
 * @property documentCount  文档总数。
 * @property chunkCount     文本块（chunk）总数。
 * @property metadata       自定义元数据。
 * @property createdAt      创建时间。
 * @property updatedAt      最近更新时间。
 */
@Serializable
public data class KnowledgeBase(
    val id: String,
    val name: String,
    val description: String? = null,
    @SerialName("embedding_model") val embeddingModel: String? = null,
    @SerialName("document_count") val documentCount: Long = 0,
    @SerialName("chunk_count") val chunkCount: Long = 0,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * 创建知识库请求。
 *
 * @property name           名称（必填，1~64 字符）。
 * @property description    描述。
 * @property embeddingModel 嵌入模型，默认使用平台默认模型。
 * @property metadata       自定义元数据。
 */
@Serializable
public data class KnowledgeCreateRequest(
    val name: String,
    val description: String? = null,
    @SerialName("embedding_model") val embeddingModel: String? = null,
    val metadata: Map<String, String> = emptyMap(),
) {
    init {
        require(name.isNotBlank() && name.length <= 64) { "name 长度必须在 1~64 之间" }
    }
}

/**
 * 更新知识库请求。
 */
@Serializable
public data class KnowledgeUpdateRequest(
    val name: String? = null,
    val description: String? = null,
    val metadata: Map<String, String>? = null,
)

/**
 * 知识库中的文档。
 *
 * @property id            文档唯一标识，形如 `doc_xxx`。
 * @property knowledgeId   所属知识库 ID。
 * @property title         文档标题。
 * @property sourceType    来源类型：`text`、`url`、`file`。
 * @property sourceUri     来源 URI（文件路径或 URL）。
 * @property status        处理状态。
 * @property chunkCount    切分出的文本块数量。
 * @property errorMessage  处理失败时的错误信息。
 * @property metadata      自定义元数据。
 * @property createdAt     创建时间。
 * @property updatedAt     最近更新时间。
 */
@Serializable
public data class KnowledgeDocument(
    val id: String,
    @SerialName("knowledge_id") val knowledgeId: String,
    val title: String,
    @SerialName("source_type") val sourceType: String = "text",
    @SerialName("source_uri") val sourceUri: String? = null,
    val status: DocumentStatus = DocumentStatus.PENDING,
    @SerialName("chunk_count") val chunkCount: Long = 0,
    @SerialName("error_message") val errorMessage: String? = null,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
) {
    /** 文档是否可检索。 */
    public fun isReady(): Boolean = status == DocumentStatus.READY
}

/**
 * 向知识库添加文本文档的请求。
 *
 * @property title        文档标题（必填）。
 * @property content      文档正文（必填）。
 * @property metadata     自定义元数据。
 * @property chunkSize    文本块大小（字符数），默认 512。
 * @property chunkOverlap 相邻文本块重叠字符数，默认 64。
 */
@Serializable
public data class DocumentAddTextRequest(
    val title: String,
    val content: String,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("chunk_size") val chunkSize: Int = 512,
    @SerialName("chunk_overlap") val chunkOverlap: Int = 64,
) {
    init {
        require(title.isNotBlank()) { "title 不能为空" }
        require(content.isNotBlank()) { "content 不能为空" }
        require(chunkSize > 0) { "chunkSize 必须为正数" }
        require(chunkOverlap >= 0 && chunkOverlap < chunkSize) { "chunkOverlap 必须小于 chunkSize 且非负" }
    }
}

/**
 * 知识库检索请求。
 *
 * @property query     查询文本（必填）。
 * @property topK      返回的最大文本块数量，默认 5。
 * @property minScore  最低相似度阈值。
 * @property filter    元数据过滤条件（键值精确匹配）。
 * @property rerank    是否启用重排序（rerank）以提升精度。
 */
@Serializable
public data class KnowledgeQueryRequest(
    val query: String,
    @SerialName("top_k") val topK: Int = 5,
    @SerialName("min_score") val minScore: Double = 0.0,
    val filter: Map<String, String>? = null,
    val rerank: Boolean = false,
) {
    init {
        require(query.isNotBlank()) { "query 不能为空" }
        require(topK in 1..50) { "topK 必须在 1..50 之间" }
    }
}

/**
 * 命中的知识文本块。
 *
 * @property chunkId     文本块 ID。
 * @property documentId  所属文档 ID。
 * @property knowledgeId 所属知识库 ID。
 * @property content     文本块内容。
 * @property score       相似度得分。
 * @property metadata    文本块元数据。
 * @property documentTitle 所属文档标题。
 */
@Serializable
public data class KnowledgeChunk(
    @SerialName("chunk_id") val chunkId: String,
    @SerialName("document_id") val documentId: String,
    @SerialName("knowledge_id") val knowledgeId: String,
    val content: String,
    val score: Double,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("document_title") val documentTitle: String? = null,
)

/**
 * 跨知识库检索请求。
 *
 * @property knowledgeIds 参与检索的知识库 ID 列表。
 */
@Serializable
public data class KnowledgeMultiQueryRequest(
    @SerialName("knowledge_ids") val knowledgeIds: List<String>,
    val query: String,
    @SerialName("top_k") val topK: Int = 5,
    @SerialName("min_score") val minScore: Double = 0.0,
    val filter: Map<String, String>? = null,
    val rerank: Boolean = false,
)

/**
 * 知识库检索响应。
 *
 * @property chunks 命中的文本块列表，按相关度降序。
 * @property query  原始查询。
 * @property tookMs 检索耗时（毫秒）。
 */
@Serializable
public data class KnowledgeQueryResponse(
    val chunks: List<KnowledgeChunk> = emptyList(),
    val query: String? = null,
    @SerialName("took_ms") val tookMs: Long? = null,
)
