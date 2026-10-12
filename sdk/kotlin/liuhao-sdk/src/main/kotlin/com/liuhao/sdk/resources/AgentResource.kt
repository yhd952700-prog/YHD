package com.liuhao.sdk.resources

import com.liuhao.sdk.LiuHaoClient
import com.liuhao.sdk.LiuHaoException
import com.liuhao.sdk.models.Agent
import com.liuhao.sdk.models.AgentCreateRequest
import com.liuhao.sdk.models.AgentExecuteRequest
import com.liuhao.sdk.models.AgentExecuteResult
import com.liuhao.sdk.models.AgentStatus
import com.liuhao.sdk.models.AgentUpdateRequest
import com.liuhao.sdk.models.DeleteResult
import com.liuhao.sdk.models.PageRequest
import com.liuhao.sdk.models.PageResponse
import com.liuhao.sdk.models.Session
import com.liuhao.sdk.models.SessionCreateRequest
import com.liuhao.sdk.models.Task
import com.liuhao.sdk.models.TaskListFilter
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

/**
 * Agent 资源访问入口。
 *
 * 提供 Agent 的完整生命周期管理（CRUD）与执行能力，
 * 通过 [LiuHaoClient.agents] 访问：
 *
 * ```kotlin
 * val agent = client.agents.create(AgentCreateRequest(name = "翻译助手"))
 * val result = client.agents.execute(agent.id, AgentExecuteRequest(input = "Hello"))
 * ```
 *
 * @property client 持有 HTTP 管线的 SDK 主客户端。
 */
public class AgentResource internal constructor(
    private val client: LiuHaoClient,
) {
    private companion object {
        const val BASE = "/v1/agents"
    }

    // ------------------------------------------------------------------
    // CRUD
    // ------------------------------------------------------------------

    /**
     * 创建 Agent。
     *
     * @param request 创建参数，见 [AgentCreateRequest]。
     * @return 创建成功的 [Agent]。
     * @throws LiuHaoException.Validation 参数校验失败。
     * @throws LiuHaoException.Conflict   同名 Agent 已存在。
     */
    public suspend fun create(request: AgentCreateRequest): Agent =
        client.post(BASE, request)

    /**
     * 根据 ID 获取 Agent。
     *
     * @param agentId Agent ID，形如 `agent_xxx`。
     * @throws LiuHaoException.NotFound Agent 不存在。
     */
    public suspend fun get(agentId: String): Agent {
        require(agentId.isNotBlank()) { "agentId 不能为空" }
        return client.get("$BASE/$agentId")
    }

    /**
     * 分页列出 Agent。
     *
     * @param page   分页参数。
     * @param status 可选状态过滤。
     */
    public suspend fun list(
        page: PageRequest = PageRequest(),
        status: AgentStatus? = null,
    ): PageResponse<Agent> = client.get(
        BASE,
        buildMap {
            putAll(page.toQueryParams())
            status?.let { put("status", it.name.lowercase()) }
        },
    )

    /**
     * 以 [Flow] 形式遍历全部 Agent，自动处理分页。
     *
     * ```kotlin
     * client.agents.listAll().collect { println(it.name) }
     * ```
     */
    public fun listAll(status: AgentStatus? = null): Flow<Agent> = flow {
        var page: PageRequest? = PageRequest(pageSize = 100)
        while (page != null) {
            val resp = list(page, status)
            resp.items.forEach { emit(it) }
            page = resp.nextPageRequest()
        }
    }

    /**
     * 部分更新 Agent（仅非 `null` 字段生效）。
     *
     * @param agentId Agent ID。
     * @param request 更新参数。
     */
    public suspend fun update(agentId: String, request: AgentUpdateRequest): Agent {
        require(agentId.isNotBlank()) { "agentId 不能为空" }
        return client.patch("$BASE/$agentId", request)
    }

    /**
     * 删除 Agent。
     *
     * @param agentId Agent ID。
     * @param force   为 `true` 时连同其任务与短期记忆一并删除。
     */
    public suspend fun delete(agentId: String, force: Boolean = false): DeleteResult {
        require(agentId.isNotBlank()) { "agentId 不能为空" }
        return client.delete("$BASE/$agentId?force=$force")
    }

    /**
     * 激活 Agent（状态置为 [AgentStatus.ACTIVE]）。
     */
    public suspend fun activate(agentId: String): Agent =
        update(agentId, AgentUpdateRequest(status = AgentStatus.ACTIVE))

    /**
     * 暂停 Agent（状态置为 [AgentStatus.PAUSED]）。
     */
    public suspend fun pause(agentId: String): Agent =
        update(agentId, AgentUpdateRequest(status = AgentStatus.PAUSED))

    /**
     * 归档 Agent（状态置为 [AgentStatus.ARCHIVED]，只读）。
     */
    public suspend fun archive(agentId: String): Agent =
        update(agentId, AgentUpdateRequest(status = AgentStatus.ARCHIVED))

    // ------------------------------------------------------------------
    // 执行
    // ------------------------------------------------------------------

    /**
     * 同步执行 Agent，阻塞直至任务完成并返回最终结果。
     *
     * @param agentId Agent ID，省略时使用 [com.liuhao.sdk.LiuHaoConfig.defaultAgentId]。
     * @param request 执行参数。
     * @return [AgentExecuteResult]，包含最终输出与中间步骤。
     * @throws LiuHaoException.Validation Agent 未激活或输入非法。
     */
    public suspend fun execute(
        agentId: String? = null,
        request: AgentExecuteRequest,
    ): AgentExecuteResult {
        val id = resolveAgentId(agentId)
        return client.post("$BASE/$id/execute", request)
    }

    /**
     * 提交异步执行任务后立即返回任务句柄，由调用方自行轮询。
     *
     * 适合长耗时任务，避免 HTTP 长连接占用。
     *
     * @return 处于 QUEUED/RUNNING 状态的 [Task]。
     */
    public suspend fun submit(
        agentId: String? = null,
        request: AgentExecuteRequest,
    ): Task {
        val id = resolveAgentId(agentId)
        return client.post("$BASE/$id/tasks", request)
    }

    /**
     * 查询任务当前状态。
     */
    public suspend fun getTask(taskId: String): Task {
        require(taskId.isNotBlank()) { "taskId 不能为空" }
        return client.get("/v1/tasks/$taskId")
    }

    /**
     * 取消执行中的任务。
     *
     * @return 取消后的任务快照。
     */
    public suspend fun cancelTask(taskId: String): Task {
        require(taskId.isNotBlank()) { "taskId 不能为空" }
        return client.post("/v1/tasks/$taskId/cancel", null)
    }

    /**
     * 分页列出任务。
     *
     * @param filter 过滤条件。
     */
    public suspend fun listTasks(filter: TaskListFilter = TaskListFilter()): PageResponse<Task> =
        client.get("/v1/tasks", filter.toQueryParams())

    /**
     * 等待任务到达终态（内部按指数退避轮询）。
     *
     * @param taskId      任务 ID。
     * @param intervalMs  初始轮询间隔（毫秒）。
     * @param maxAttempts 最大轮询次数。
     * @return 终态 [Task]。
     * @throws LiuHaoException.Timeout 超过最大轮询次数仍未完成。
     */
    public suspend fun waitTask(
        taskId: String,
        intervalMs: Long = 1_000,
        maxAttempts: Int = 120,
    ): Task = client.pollUntil(intervalMs, maxAttempts, fetch = { getTask(taskId) }) {
        it.isTerminal()
    }

    /**
     * 提交任务并等待完成，一步完成异步执行。
     *
     * ```kotlin
     * val task = client.agents.submitAndWait(agent.id, AgentExecuteRequest(input = "总结文档"))
     * println(task.output)
     * ```
     */
    public suspend fun submitAndWait(
        agentId: String? = null,
        request: AgentExecuteRequest,
        intervalMs: Long = 1_000,
        maxAttempts: Int = 120,
    ): Task {
        val task = submit(agentId, request)
        return waitTask(task.id, intervalMs, maxAttempts)
    }

    // ------------------------------------------------------------------
    // 会话
    // ------------------------------------------------------------------

    /**
     * 创建会话。
     *
     * @param agentId  Agent ID。
     * @param title    会话标题，省略时由服务端自动生成。
     * @param metadata 自定义元数据。
     */
    public suspend fun createSession(
        agentId: String? = null,
        title: String? = null,
        metadata: Map<String, String> = emptyMap(),
    ): Session {
        val id = resolveAgentId(agentId)
        return client.post(
            "$BASE/$id/sessions",
            SessionCreateRequest(title = title, metadata = metadata),
        )
    }

    /**
     * 获取会话详情。
     */
    public suspend fun getSession(sessionId: String): Session {
        require(sessionId.isNotBlank()) { "sessionId 不能为空" }
        return client.get("/v1/sessions/$sessionId")
    }

    /**
     * 列出某 Agent 的全部会话。
     */
    public suspend fun listSessions(
        agentId: String? = null,
        page: PageRequest = PageRequest(),
    ): PageResponse<Session> {
        val id = resolveAgentId(agentId)
        return client.get("$BASE/$id/sessions", page.toQueryParams())
    }

    // ------------------------------------------------------------------
    // 内部工具
    // ------------------------------------------------------------------

    /**
     * 解析 Agent ID：显式传参优先，否则回退到配置默认值。
     *
     * @throws IllegalArgumentException 两者都缺失时抛出。
     */
    private fun resolveAgentId(agentId: String?): String {
        val id = agentId ?: client.config.defaultAgentId
        require(!id.isNullOrBlank()) {
            "agentId 未提供，且 LiuHaoConfig.defaultAgentId 未设置"
        }
        return id
    }
}
