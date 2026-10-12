package com.liuhao.sdk.resources

import com.liuhao.sdk.LiuHaoClient
import com.liuhao.sdk.LiuHaoException
import com.liuhao.sdk.models.DeleteResult
import com.liuhao.sdk.models.DocumentAddTextRequest
import com.liuhao.sdk.models.DocumentStatus
import com.liuhao.sdk.models.KnowledgeBase
import com.liuhao.sdk.models.KnowledgeChunk
import com.liuhao.sdk.models.KnowledgeCreateRequest
import com.liuhao.sdk.models.KnowledgeDocument
import com.liuhao.sdk.models.KnowledgeMultiQueryRequest
import com.liuhao.sdk.models.KnowledgeQueryRequest
import com.liuhao.sdk.models.KnowledgeQueryResponse
import com.liuhao.sdk.models.KnowledgeUpdateRequest
import com.liuhao.sdk.models.PageRequest
import com.liuhao.sdk.models.PageResponse
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Knowledge 资源访问入口。
 *
 * 管理知识库与文档，并提供基于向量的语义检索（RAG）。
 * 通过 [LiuHaoClient.knowledge] 访问：
 *
 * ```kotlin
 * val kb = client.knowledge.create(KnowledgeCreateRequest(name = "产品手册"))
 * val doc = client.knowledge.addText(kb.id, DocumentAddTextRequest(title = "FAQ", content = "..."))
 * client.knowledge.waitDocumentReady(kb.id, doc.id)
 * val chunks = client.knowledge.query(kb.id, KnowledgeQueryRequest(query = "如何退款"))
 * ```
 *
 * @property client 持有 HTTP 管线的 SDK 主客户端。
 */
public class KnowledgeResource internal constructor(
    private val client: LiuHaoClient,
) {
    private companion object {
        const val BASE = "/v1/knowledge"
    }

    // ------------------------------------------------------------------
    // 知识库 CRUD
    // ------------------------------------------------------------------

    /**
     * 创建知识库。
     *
     * @param request 创建参数，见 [KnowledgeCreateRequest]。
     * @return 创建成功的 [KnowledgeBase]。
     * @throws LiuHaoException.Conflict 同名知识库已存在。
     */
    public suspend fun create(request: KnowledgeCreateRequest): KnowledgeBase =
        client.post(BASE, request)

    /**
     * 根据 ID 获取知识库。
     *
     * @throws LiuHaoException.NotFound 知识库不存在。
     */
    public suspend fun get(knowledgeId: String): KnowledgeBase {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        return client.get("$BASE/$knowledgeId")
    }

    /**
     * 分页列出知识库。
     */
    public suspend fun list(page: PageRequest = PageRequest()): PageResponse<KnowledgeBase> =
        client.get(BASE, page.toQueryParams())

    /**
     * 以 [Flow] 形式遍历全部知识库，自动处理分页。
     */
    public fun listAll(): Flow<KnowledgeBase> = flow {
        var page: PageRequest? = PageRequest(pageSize = 100)
        while (page != null) {
            val resp = list(page)
            resp.items.forEach { emit(it) }
            page = resp.nextPageRequest()
        }
    }

    /**
     * 更新知识库（仅非 `null` 字段生效）。
     */
    public suspend fun update(
        knowledgeId: String,
        request: KnowledgeUpdateRequest,
    ): KnowledgeBase {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        return client.patch("$BASE/$knowledgeId", request)
    }

    /**
     * 删除知识库。
     *
     * @param cascade 为 `true` 时连同库内全部文档与向量索引一并删除。
     */
    public suspend fun delete(knowledgeId: String, cascade: Boolean = false): DeleteResult {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        return client.delete("$BASE/$knowledgeId?cascade=$cascade")
    }

    // ------------------------------------------------------------------
    // 文档管理
    // ------------------------------------------------------------------

    /**
     * 向知识库添加纯文本文档。
     *
     * 文档上传后进入异步处理队列（切分、向量化），
     * 可调用 [waitDocumentReady] 等待其可检索。
     *
     * @param knowledgeId 知识库 ID。
     * @param request     文档内容与切分配置。
     * @return 处于 PENDING/PROCESSING 状态的 [KnowledgeDocument]。
     */
    public suspend fun addText(
        knowledgeId: String,
        request: DocumentAddTextRequest,
    ): KnowledgeDocument {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        return client.post("$BASE/$knowledgeId/documents", request)
    }

    /**
     * 通过 URL 抓取并添加文档（网页、公开 PDF 等）。
     *
     * @param knowledgeId 知识库 ID。
     * @param url         文档地址。
     * @param title       可选标题，省略时由服务端从内容推断。
     */
    public suspend fun addFromUrl(
        knowledgeId: String,
        url: String,
        title: String? = null,
    ): KnowledgeDocument {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        require(url.isNotBlank()) { "url 不能为空" }
        return client.post(
            "$BASE/$knowledgeId/documents/url",
            mapOf("url" to url, "title" to title),
        )
    }

    /**
     * 获取文档详情（含处理状态）。
     */
    public suspend fun getDocument(knowledgeId: String, documentId: String): KnowledgeDocument {
        require(documentId.isNotBlank()) { "documentId 不能为空" }
        return client.get("$BASE/$knowledgeId/documents/$documentId")
    }

    /**
     * 分页列出知识库中的文档。
     *
     * @param status 可选状态过滤。
     */
    public suspend fun listDocuments(
        knowledgeId: String,
        status: DocumentStatus? = null,
        page: PageRequest = PageRequest(),
    ): PageResponse<KnowledgeDocument> = client.get(
        "$BASE/$knowledgeId/documents",
        buildMap {
            putAll(page.toQueryParams())
            status?.let { put("status", it.name.lowercase()) }
        },
    )

    /**
     * 删除文档及其向量索引。
     */
    public suspend fun deleteDocument(knowledgeId: String, documentId: String): DeleteResult {
        require(documentId.isNotBlank()) { "documentId 不能为空" }
        return client.delete("$BASE/$knowledgeId/documents/$documentId")
    }

    /**
     * 重新处理文档（切分与向量化）。
     *
     * 适用于处理失败后的重试，或切分策略调整后的重建。
     */
    public suspend fun reprocessDocument(knowledgeId: String, documentId: String): KnowledgeDocument {
        require(documentId.isNotBlank()) { "documentId 不能为空" }
        return client.post("$BASE/$knowledgeId/documents/$documentId/reprocess", null)
    }

    /**
     * 等待文档处理完成（内部按指数退避轮询）。
     *
     * @param knowledgeId 知识库 ID。
     * @param documentId  文档 ID。
     * @param intervalMs  初始轮询间隔（毫秒）。
     * @param maxAttempts 最大轮询次数。
     * @return 状态为 READY 或 FAILED 的 [KnowledgeDocument]。
     * @throws LiuHaoException.Timeout 超过最大轮询次数仍未完成。
     */
    public suspend fun waitDocumentReady(
        knowledgeId: String,
        documentId: String,
        intervalMs: Long = 1_000,
        maxAttempts: Int = 120,
    ): KnowledgeDocument = client.pollUntil(
        intervalMs,
        maxAttempts,
        fetch = { getDocument(knowledgeId, documentId) },
    ) {
        it.status == DocumentStatus.READY || it.status == DocumentStatus.FAILED
    }

    // ------------------------------------------------------------------
    // 语义检索
    // ------------------------------------------------------------------

    /**
     * 在单个知识库中执行语义检索。
     *
     * @param knowledgeId 知识库 ID。
     * @param request     检索参数。
     * @return [KnowledgeQueryResponse]，`chunks` 按相关度降序。
     */
    public suspend fun query(
        knowledgeId: String,
        request: KnowledgeQueryRequest,
    ): KnowledgeQueryResponse {
        require(knowledgeId.isNotBlank()) { "knowledgeId 不能为空" }
        return client.post("$BASE/$knowledgeId/query", request)
    }

    /**
     * 跨多个知识库执行语义检索，结果合并后按相关度降序。
     *
     * @param knowledgeIds 知识库 ID 列表。
     * @param request      检索参数。
     * @return 合并后的文本块列表（最多返回 [KnowledgeQueryRequest.topK] 条）。
     */
    public suspend fun queryMultiple(
        knowledgeIds: List<String>,
        request: KnowledgeQueryRequest,
    ): List<KnowledgeChunk> {
        require(knowledgeIds.isNotEmpty()) { "knowledgeIds 不能为空" }
        if (knowledgeIds.size == 1) return query(knowledgeIds.single(), request).chunks

        val response: KnowledgeQueryResponse = client.post(
            "$BASE/query",
            KnowledgeMultiQueryRequest(
                knowledgeIds = knowledgeIds,
                query = request.query,
                topK = request.topK,
                minScore = request.minScore,
                filter = request.filter,
                rerank = request.rerank,
            ),
        )
        return response.chunks
    }

    /**
     * 便捷方法：检索并拼接为上下文文本，直接供提示词使用。
     *
     * ```kotlin
     * val context = client.knowledge.buildContext(kb.id, "退款政策", topK = 3)
     * val prompt = "基于以下资料回答：\n$context\n\n问题：退款政策"
     * ```
     *
     * @param knowledgeId 知识库 ID。
     * @param query       查询文本。
     * @param topK        使用的文本块数量。
     * @param separator   文本块之间的分隔符。
     * @return 拼接后的上下文字符串；无命中时为空字符串。
     */
    public suspend fun buildContext(
        knowledgeId: String,
        query: String,
        topK: Int = 3,
        separator: String = "\n---\n",
    ): String {
        val resp = query(knowledgeId, KnowledgeQueryRequest(query = query, topK = topK))
        return resp.chunks.joinToString(separator) { it.content }
    }
}
