package com.liuhao.sdk.models

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Agent 状态枚举。
 */
@Serializable
public enum class AgentStatus {
    /** 已创建，尚未激活。 */
    @SerialName("draft") DRAFT,

    /** 正常运行中。 */
    @SerialName("active") ACTIVE,

    /** 已暂停，不接收新任务。 */
    @SerialName("paused") PAUSED,

    /** 已归档，只读。 */
    @SerialName("archived") ARCHIVED,
}

/**
 * Agent 模型配置。
 *
 * @property model        底层大模型标识，如 `gpt-4o`、`claude-3-5-sonnet`。
 * @property temperature  采样温度，0.0 ~ 2.0。
 * @property maxTokens    单次生成最大 token 数。
 * @property topP         nucleus sampling 参数。
 * @property tools        允许 Agent 使用的工具 ID 列表。
 * @property extra        其他模型相关的自定义参数。
 */
@Serializable
public data class AgentModelConfig(
    val model: String = "gpt-4o",
    val temperature: Double = 0.7,
    @SerialName("max_tokens") val maxTokens: Int = 4096,
    @SerialName("top_p") val topP: Double = 1.0,
    val tools: List<String> = emptyList(),
    val extra: JsonObject? = null,
)

/**
 * LiuHao AI OS 智能体（Agent）。
 *
 * @property id           Agent 唯一标识，形如 `agent_xxx`。
 * @property name         显示名称。
 * @property description  功能描述。
 * @property instructions 系统提示词（System Prompt）。
 * @property status       当前状态。
 * @property modelConfig  模型配置。
 * @property metadata     用户自定义键值对元数据。
 * @property knowledgeIds 绑定的知识库 ID 列表。
 * @property createdAt    创建时间（ISO-8601）。
 * @property updatedAt    最近更新时间（ISO-8601）。
 * @property lastRunAt    最近一次执行时间（ISO-8601），可能为 `null`。
 */
@Serializable
public data class Agent(
    val id: String,
    val name: String,
    val description: String? = null,
    val instructions: String? = null,
    val status: AgentStatus = AgentStatus.DRAFT,
    @SerialName("model_config") val modelConfig: AgentModelConfig = AgentModelConfig(),
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("knowledge_ids") val knowledgeIds: List<String> = emptyList(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("last_run_at") val lastRunAt: String? = null,
) {
    /** 是否处于可执行状态。 */
    public fun isExecutable(): Boolean = status == AgentStatus.ACTIVE
}

/**
 * 创建 Agent 的请求体。
 *
 * @property name         名称（必填，1~64 字符）。
 * @property description  描述。
 * @property instructions 系统提示词。
 * @property modelConfig  模型配置。
 * @property metadata     自定义元数据。
 * @property knowledgeIds 绑定的知识库 ID。
 * @property activate     创建后是否立即激活。
 */
@Serializable
public data class AgentCreateRequest(
    val name: String,
    val description: String? = null,
    val instructions: String? = null,
    @SerialName("model_config") val modelConfig: AgentModelConfig = AgentModelConfig(),
    val metadata: Map<String, String> = emptyMap(),
    @SerialName("knowledge_ids") val knowledgeIds: List<String> = emptyList(),
    val activate: Boolean = true,
) {
    init {
        require(name.isNotBlank() && name.length <= 64) { "name 长度必须在 1~64 之间" }
    }
}

/**
 * 更新 Agent 的请求体，所有字段可选，仅更新非 `null` 字段。
 */
@Serializable
public data class AgentUpdateRequest(
    val name: String? = null,
    val description: String? = null,
    val instructions: String? = null,
    @SerialName("model_config") val modelConfig: AgentModelConfig? = null,
    val metadata: Map<String, String>? = null,
    @SerialName("knowledge_ids") val knowledgeIds: List<String>? = null,
    val status: AgentStatus? = null,
)

/**
 * Agent 执行请求。
 *
 * @property input         用户输入文本。
 * @property sessionId     会话 ID，多轮对话时传入以保持上下文。
 * @property stream        是否使用流式输出（SDK 当前以聚合方式返回）。
 * @property context       附加上下文键值对，会注入提示词模板。
 * @property maxIterations Agent 工具调用最大迭代轮数。
 */
@Serializable
public data class AgentExecuteRequest(
    val input: String,
    @SerialName("session_id") val sessionId: String? = null,
    val stream: Boolean = false,
    val context: Map<String, String> = emptyMap(),
    @SerialName("max_iterations") val maxIterations: Int = 10,
) {
    init {
        require(input.isNotBlank()) { "input 不能为空" }
        require(maxIterations in 1..50) { "maxIterations 必须在 1..50 之间" }
    }
}

/**
 * Agent 执行结果。
 *
 * @property taskId       任务 ID，可用于后续查询。
 * @property agentId      执行的 Agent ID。
 * @property sessionId    会话 ID。
 * @property output       Agent 最终输出文本。
 * @property steps        中间推理/工具调用步骤。
 * @property usage        token 用量统计。
 * @property durationMs   执行耗时（毫秒）。
 * @property finishedAt   完成时间（ISO-8601）。
 */
@Serializable
public data class AgentExecuteResult(
    @SerialName("task_id") val taskId: String,
    @SerialName("agent_id") val agentId: String,
    @SerialName("session_id") val sessionId: String? = null,
    val output: String,
    val steps: List<AgentStep> = emptyList(),
    val usage: TokenUsage? = null,
    @SerialName("duration_ms") val durationMs: Long? = null,
    @SerialName("finished_at") val finishedAt: String? = null,
)

/**
 * Agent 执行过程中的单个步骤。
 *
 * @property index      步骤序号，从 0 开始。
 * @property type       步骤类型：`reasoning`、`tool_call`、`tool_result`、`output`。
 * @property content    步骤内容。
 * @property toolName   工具名（type 为 tool_* 时有值）。
 * @property latencyMs  本步骤耗时（毫秒）。
 */
@Serializable
public data class AgentStep(
    val index: Int,
    val type: String,
    val content: String,
    @SerialName("tool_name") val toolName: String? = null,
    @SerialName("latency_ms") val latencyMs: Long? = null,
)

/**
 * Token 用量统计。
 *
 * @property promptTokens     输入 token 数。
 * @property completionTokens 输出 token 数。
 * @property totalTokens      总 token 数。
 */
@Serializable
public data class TokenUsage(
    @SerialName("prompt_tokens") val promptTokens: Long = 0,
    @SerialName("completion_tokens") val completionTokens: Long = 0,
    @SerialName("total_tokens") val totalTokens: Long = 0,
)
