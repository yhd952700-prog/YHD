package com.liuhao.sdk.resources

import com.liuhao.sdk.LiuHaoClient
import com.liuhao.sdk.LiuHaoException
import com.liuhao.sdk.models.DeleteResult
import com.liuhao.sdk.models.Memory
import com.liuhao.sdk.models.MemoryBatchCreateRequest
import com.liuhao.sdk.models.MemoryBatchCreateResponse
import com.liuhao.sdk.models.MemoryCreateRequest
import com.liuhao.sdk.models.MemorySearchRequest
import com.liuhao.sdk.models.MemorySearchResponse
import com.liuhao.sdk.models.MemoryType
import com.liuhao.sdk.models.MemoryUpdateRequest
import com.liuhao.sdk.models.PageRequest
import com.liuhao.sdk.models.PageResponse
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope

/**
 * Memory 资源访问入口。
 *
 * 管理 Agent 的记忆：写入、更新、删除、语义检索与批量操作。
 * 通过 [LiuHaoClient.memories] 访问：
 *
 * ```kotlin
 * val memory = client.memories.create(
 *     MemoryCreateRequest(agentId = agent.id, content = "用户偏好中文回复")
 * )
 * val hits = client.memories.search(MemorySearchRequest(agentId = agent.id, query = "用户偏好"))
 * ```
 *
 * @property client 持有 HTTP 管线的 SDK 主客户端。
 */
public class MemoryResource internal constructor(
    private val client: LiuHaoClient,
) {
    private companion object {
        const val BASE = "/v1/memories"
    }

    // ------------------------------------------------------------------
    // 写入与更新
    // ------------------------------------------------------------------

    /**
     * 写入一条记忆。
     *
     * @param request 写入参数，见 [MemoryCreateRequest]。
     * @return 创建成功的 [Memory]。
     * @throws LiuHaoException.Validation 参数校验失败。
     */
    public suspend fun create(request: MemoryCreateRequest): Memory =
        client.post(BASE, request)

    /**
     * 便捷方法：为指定 Agent 写入长期记忆。
     *
     * @param agentId    Agent ID。
     * @param content    记忆内容。
     * @param importance 重要性评分 0.0 ~ 1.0。
     */
    public suspend fun remember(
        agentId: String,
        content: String,
        importance: Double = 0.5,
    ): Memory = create(
        MemoryCreateRequest(
            agentId = agentId,
            content = content,
            type = MemoryType.LONG_TERM,
            importance = importance,
        ),
    )

    /**
     * 批量写入记忆（单次最多 100 条）。
     *
     * @param requests 写入请求列表。
     * @return [MemoryBatchCreateResponse] 包含成功与失败数量。
     * @throws LiuHaoException.Validation 列表为空或超过 100 条。
     */
    public suspend fun createBatch(requests: List<MemoryCreateRequest>): MemoryBatchCreateResponse =
        client.post("$BASE/batch", MemoryBatchCreateRequest(requests))

    /**
     * 并行批量写入：将大列表按 100 条分片后并发提交。
     *
     * @param requests 任意大小的写入请求列表。
     * @return 全部分片的汇总结果。
     */
    public suspend fun createBatchParallel(
        requests: List<MemoryCreateRequest>,
    ): MemoryBatchCreateResponse = coroutineScope {
        val shards = requests.chunked(100)
        val results = shards.map { shard ->
            async { createBatch(shard) }
        }.map { it.await() }

        MemoryBatchCreateResponse(
            created = results.sumOf { it.created },
            failed = results.sumOf { it.failed },
            ids = results.flatMap { it.ids },
        )
    }

    /**
     * 获取单条记忆。
     *
     * @throws LiuHaoException.NotFound 记忆不存在或已过期被清理。
     */
    public suspend fun get(memoryId: String): Memory {
        require(memoryId.isNotBlank()) { "memoryId 不能为空" }
        return client.get("$BASE/$memoryId")
    }

    /**
     * 更新记忆（仅非 `null` 字段生效）。
     */
    public suspend fun update(memoryId: String, request: MemoryUpdateRequest): Memory {
        require(memoryId.isNotBlank()) { "memoryId 不能为空" }
        return client.patch("$BASE/$memoryId", request)
    }

    /**
     * 删除记忆。
     */
    public suspend fun delete(memoryId: String): DeleteResult {
        require(memoryId.isNotBlank()) { "memoryId 不能为空" }
        return client.delete("$BASE/$memoryId")
    }

    // ------------------------------------------------------------------
    // 列表与检索
    // ------------------------------------------------------------------

    /**
     * 分页列出记忆。
     *
     * @param agentId   按 Agent 过滤，建议传入以缩小范围。
     * @param sessionId 按会话过滤。
     * @param type      按类型过滤。
     * @param page      分页参数。
     */
    public suspend fun list(
        agentId: String? = null,
        sessionId: String? = null,
        type: MemoryType? = null,
        page: PageRequest = PageRequest(),
    ): PageResponse<Memory> = client.get(
        BASE,
        buildMap {
            putAll(page.toQueryParams())
            agentId?.let { put("agent_id", it) }
            sessionId?.let { put("session_id", it) }
            type?.let { put("type", it.name.lowercase()) }
        },
    )

    /**
     * 语义检索记忆。
     *
     * 基于向量相似度匹配与 [MemorySearchRequest.query] 最相关的记忆，
     * 结果按相关度降序返回。
     *
     * @param request 检索参数。
     * @return [MemorySearchResponse]，`hits` 为命中列表。
     */
    public suspend fun search(request: MemorySearchRequest): MemorySearchResponse =
        client.post("$BASE/search", request)

    /**
     * 便捷方法：检索指定 Agent 的相关记忆，仅返回记忆列表。
     *
     * @param agentId Agent ID。
     * @param query   查询文本。
     * @param topK    最大返回数量。
     */
    public suspend fun recall(
        agentId: String,
        query: String,
        topK: Int = 5,
    ): List<Memory> = search(
        MemorySearchRequest(agentId = agentId, query = query, topK = topK),
    ).hits.map { it.memory }

    // ------------------------------------------------------------------
    // 维护操作
    // ------------------------------------------------------------------

    /**
     * 清理指定 Agent 的全部短期记忆。
     *
     * 通常在会话结束后调用，释放存储并保持上下文窗口整洁。
     *
     * @param agentId   Agent ID。
     * @param sessionId 可选，仅清理指定会话。
     * @return 删除结果，`deleted` 表示是否有数据被清除。
     */
    public suspend fun clearShortTerm(
        agentId: String,
        sessionId: String? = null,
    ): DeleteResult {
        require(agentId.isNotBlank()) { "agentId 不能为空" }
        val suffix = sessionId?.let { "?session_id=$it" } ?: ""
        return client.delete("$BASE/agent/$agentId/short-term$suffix")
    }

    /**
     * 压缩（consolidate）Agent 的长期记忆。
     *
     * 服务端会将相似记忆合并、淘汰低重要性条目，
     * 用于控制记忆规模、提升检索质量。
     *
     * @param agentId Agent ID。
     * @return 压缩任务 ID，可用 [LiuHaoClient] 的任务接口跟踪进度。
     */
    public suspend fun consolidate(agentId: String): String {
        require(agentId.isNotBlank()) { "agentId 不能为空" }
        val result: Map<String, String> = client.post("$BASE/agent/$agentId/consolidate", null)
        return result["task_id"]
            ?: throw LiuHaoException.Serialization("consolidate 响应缺少 task_id 字段")
    }
}
