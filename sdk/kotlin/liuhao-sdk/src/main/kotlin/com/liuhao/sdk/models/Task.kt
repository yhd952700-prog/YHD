package com.liuhao.sdk.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * 任务运行状态。
 */
@Serializable
public enum class TaskStatus {
    /** 排队中。 */
    @SerialName("queued") QUEUED,

    /** 运行中。 */
    @SerialName("running") RUNNING,

    /** 已成功完成。 */
    @SerialName("succeeded") SUCCEEDED,

    /** 执行失败。 */
    @SerialName("failed") FAILED,

    /** 已取消。 */
    @SerialName("cancelled") CANCELLED,

    /** 超时终止。 */
    @SerialName("timeout") TIMEOUT,
}

/**
 * 一次 Agent 执行任务。
 *
 * @property id          任务唯一标识，形如 `task_xxx`。
 * @property agentId     执行任务的 Agent ID。
 * @property sessionId   会话 ID。
 * @property status      当前状态。
 * @property input       输入文本。
 * @property output      最终输出（任务成功后有值）。
 * @property error       失败原因（任务失败后有值）。
 * @property usage       token 用量。
 * @property progress    进度 0.0 ~ 1.0（运行中有效）。
 * @property createdAt   创建时间。
 * @property startedAt   开始执行时间。
 * @property finishedAt  结束时间。
 */
@Serializable
public data class Task(
    val id: String,
    @SerialName("agent_id") val agentId: String,
    @SerialName("session_id") val sessionId: String? = null,
    val status: TaskStatus = TaskStatus.QUEUED,
    val input: String? = null,
    val output: String? = null,
    val error: String? = null,
    val usage: TokenUsage? = null,
    val progress: Double = 0.0,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("started_at") val startedAt: String? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
) {
    /** 任务是否已终态（成功 / 失败 / 取消 / 超时）。 */
    public fun isTerminal(): Boolean = status in TERMINAL_STATUSES

    /** 任务是否执行成功。 */
    public fun isSucceeded(): Boolean = status == TaskStatus.SUCCEEDED

    public companion object {
        /** 所有终态状态集合。 */
        public val TERMINAL_STATUSES: Set<TaskStatus> = setOf(
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        )
    }
}

/**
 * 任务列表过滤参数。
 *
 * @property agentId   按 Agent 过滤。
 * @property sessionId 按会话过滤。
 * @property status    按状态过滤。
 * @property page      分页参数。
 */
public data class TaskListFilter(
    val agentId: String? = null,
    val sessionId: String? = null,
    val status: TaskStatus? = null,
    val page: PageRequest = PageRequest(),
) {
    /** 转换为 URL 查询参数。 */
    internal fun toQueryParams(): Map<String, String> = buildMap {
        putAll(page.toQueryParams())
        agentId?.let { put("agent_id", it) }
        sessionId?.let { put("session_id", it) }
        status?.let { put("status", it.name.lowercase()) }
    }
}

/**
 * 会话（Session）信息。
 *
 * @property id          会话 ID，形如 `sess_xxx`。
 * @property agentId     所属 Agent ID。
 * @property title       会话标题（可自动生成）。
 * @property taskCount   会话中的任务数量。
 * @property metadata    自定义元数据。
 * @property createdAt   创建时间。
 * @property updatedAt   最近更新时间。
 */
@Serializable
public data class Session(
    val id: String,
    @SerialName("agent_id") val agentId: String,
    val title: String? = null,
    @SerialName("task_count") val taskCount: Long = 0,
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

/**
 * 创建会话的请求体。
 *
 * @property title    会话标题，省略时由服务端自动生成。
 * @property metadata 自定义元数据。
 */
@Serializable
public data class SessionCreateRequest(
    val title: String? = null,
    val metadata: Map<String, String> = emptyMap(),
)

/**
 * SDK 健康检查结果。
 *
 * @property status   服务状态：`ok` / `degraded` / `down`。
 * @property version  服务端版本。
 * @property latencyMs 探测延迟（毫秒）。
 */
@Serializable
public data class HealthStatus(
    val status: String = "ok",
    val version: String? = null,
    @SerialName("latency_ms") val latencyMs: Long? = null,
) {
    /** 服务是否健康。 */
    public fun isHealthy(): Boolean = status == "ok"
}
