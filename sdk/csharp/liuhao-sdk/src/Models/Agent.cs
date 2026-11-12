using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// Current lifecycle status of an agent.
/// </summary>
[SnakeCaseEnumConverter]
public enum AgentStatus
{
    /// <summary>The agent is active and ready to accept tasks.</summary>
    Active,

    /// <summary>The agent is inactive.</summary>
    Inactive,

    /// <summary>The agent is currently being deployed.</summary>
    Deploying,

    /// <summary>The agent deployment or last execution failed.</summary>
    Failed,

    /// <summary>The agent has been deleted.</summary>
    Deleted,
}

/// <summary>
/// The type of an agent.
/// </summary>
[SnakeCaseEnumConverter]
public enum AgentType
{
    /// <summary>A conversational chat agent.</summary>
    Chat,

    /// <summary>An agent that executes predefined workflows.</summary>
    Workflow,

    /// <summary>A research-focused agent.</summary>
    Research,

    /// <summary>A code generation and analysis agent.</summary>
    Code,

    /// <summary>A user-defined custom agent type.</summary>
    Custom,
}

/// <summary>
/// Status of an agent task execution run.
/// </summary>
[SnakeCaseEnumConverter]
public enum AgentRunStatus
{
    /// <summary>The run is queued and has not started yet.</summary>
    Pending,

    /// <summary>The run is currently executing.</summary>
    Running,

    /// <summary>The run completed successfully.</summary>
    Completed,

    /// <summary>The run failed with an error.</summary>
    Failed,

    /// <summary>The run was cancelled before completion.</summary>
    Cancelled,

    /// <summary>The run exceeded its time limit.</summary>
    TimedOut,
}

/// <summary>
/// Represents an agent on the LiuHao platform.
/// </summary>
public sealed record Agent
{
    /// <summary>Unique agent identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>Human-readable agent name.</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Brief description of the agent's purpose.</summary>
    [JsonPropertyName("description")]
    public string Description { get; init; } = string.Empty;

    /// <summary>The type of the agent.</summary>
    [JsonPropertyName("type")]
    public AgentType Type { get; init; } = AgentType.Chat;

    /// <summary>The underlying AI model used by the agent.</summary>
    [JsonPropertyName("model")]
    public string Model { get; init; } = "default";

    /// <summary>Current lifecycle status.</summary>
    [JsonPropertyName("status")]
    public AgentStatus Status { get; init; } = AgentStatus.Inactive;

    /// <summary>List of capability identifiers.</summary>
    [JsonPropertyName("capabilities")]
    public IReadOnlyList<string> Capabilities { get; init; } = Array.Empty<string>();

    /// <summary>System prompt template that defines the agent's behavior.</summary>
    [JsonPropertyName("system_prompt")]
    public string SystemPrompt { get; init; } = string.Empty;

    /// <summary>List of configured tool names available to the agent.</summary>
    [JsonPropertyName("tools")]
    public IReadOnlyList<string> Tools { get; init; } = Array.Empty<string>();

    /// <summary>ISO 8601 creation timestamp.</summary>
    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; init; }

    /// <summary>ISO 8601 last-update timestamp.</summary>
    [JsonPropertyName("updated_at")]
    public DateTimeOffset? UpdatedAt { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Represents an agent task execution run.
/// </summary>
public sealed record AgentRun
{
    /// <summary>Unique run identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>The agent that executed this run.</summary>
    [JsonPropertyName("agent_id")]
    public string AgentId { get; init; } = string.Empty;

    /// <summary>Current run status.</summary>
    [JsonPropertyName("status")]
    public AgentRunStatus Status { get; init; } = AgentRunStatus.Pending;

    /// <summary>The input/query that triggered the run.</summary>
    [JsonPropertyName("input")]
    public string Input { get; init; } = string.Empty;

    /// <summary>The generated output, if the run completed.</summary>
    [JsonPropertyName("output")]
    public string? Output { get; init; }

    /// <summary>Error message if the run failed.</summary>
    [JsonPropertyName("error")]
    public string? Error { get; init; }

    /// <summary>ISO 8601 start timestamp.</summary>
    [JsonPropertyName("started_at")]
    public DateTimeOffset? StartedAt { get; init; }

    /// <summary>ISO 8601 completion timestamp.</summary>
    [JsonPropertyName("completed_at")]
    public DateTimeOffset? CompletedAt { get; init; }

    /// <summary>Total execution duration in milliseconds.</summary>
    [JsonPropertyName("duration_ms")]
    public long? DurationMs { get; init; }

    /// <summary>Total tokens consumed.</summary>
    [JsonPropertyName("tokens_used")]
    public long? TokensUsed { get; init; }

    /// <summary>Additional run metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Usage and performance metrics for an agent.
/// </summary>
public sealed record AgentMetrics
{
    /// <summary>The agent these metrics belong to.</summary>
    [JsonPropertyName("agent_id")]
    public string AgentId { get; init; } = string.Empty;

    /// <summary>Total number of executions.</summary>
    [JsonPropertyName("total_runs")]
    public long TotalRuns { get; init; }

    /// <summary>Number of successful executions.</summary>
    [JsonPropertyName("successful_runs")]
    public long SuccessfulRuns { get; init; }

    /// <summary>Number of failed executions.</summary>
    [JsonPropertyName("failed_runs")]
    public long FailedRuns { get; init; }

    /// <summary>Average execution duration in milliseconds.</summary>
    [JsonPropertyName("avg_duration_ms")]
    public double AvgDurationMs { get; init; }

    /// <summary>Total tokens consumed.</summary>
    [JsonPropertyName("total_tokens")]
    public long TotalTokens { get; init; }

    /// <summary>Start of the metrics period.</summary>
    [JsonPropertyName("period_start")]
    public DateTimeOffset? PeriodStart { get; init; }

    /// <summary>End of the metrics period.</summary>
    [JsonPropertyName("period_end")]
    public DateTimeOffset? PeriodEnd { get; init; }
}

/// <summary>
/// Parameters for creating a new agent.
/// </summary>
public sealed record AgentCreateOptions
{
    /// <summary>Human-readable agent name (required).</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Brief description of the agent's purpose.</summary>
    [JsonPropertyName("description")]
    public string? Description { get; init; }

    /// <summary>The type of the agent.</summary>
    [JsonPropertyName("type")]
    public AgentType? Type { get; init; }

    /// <summary>The AI model identifier to use.</summary>
    [JsonPropertyName("model")]
    public string? Model { get; init; }

    /// <summary>System prompt configuring the agent's behavior.</summary>
    [JsonPropertyName("system_prompt")]
    public string? SystemPrompt { get; init; }

    /// <summary>List of tool names the agent may use.</summary>
    [JsonPropertyName("tools")]
    public IReadOnlyList<string>? Tools { get; init; }

    /// <summary>List of capability identifiers.</summary>
    [JsonPropertyName("capabilities")]
    public IReadOnlyList<string>? Capabilities { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }
}

/// <summary>
/// Parameters for updating an existing agent. Only set fields are updated.
/// </summary>
public sealed record AgentUpdateOptions
{
    /// <summary>New agent name.</summary>
    [JsonPropertyName("name")]
    public string? Name { get; init; }

    /// <summary>New description.</summary>
    [JsonPropertyName("description")]
    public string? Description { get; init; }

    /// <summary>New model identifier.</summary>
    [JsonPropertyName("model")]
    public string? Model { get; init; }

    /// <summary>New system prompt.</summary>
    [JsonPropertyName("system_prompt")]
    public string? SystemPrompt { get; init; }

    /// <summary>New list of tool names.</summary>
    [JsonPropertyName("tools")]
    public IReadOnlyList<string>? Tools { get; init; }

    /// <summary>New metadata (replaces existing).</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }

    /// <summary>New lifecycle status.</summary>
    [JsonPropertyName("status")]
    public AgentStatus? Status { get; init; }
}

/// <summary>
/// Parameters for executing a task on an agent.
/// </summary>
public sealed record AgentExecuteOptions
{
    /// <summary>The input/query for the agent to process (required).</summary>
    [JsonPropertyName("input")]
    public string Input { get; init; } = string.Empty;

    /// <summary>Optional additional context data.</summary>
    [JsonPropertyName("context")]
    public IReadOnlyDictionary<string, object?>? Context { get; init; }

    /// <summary>Whether to stream the response.</summary>
    [JsonPropertyName("stream")]
    public bool Stream { get; init; }

    /// <summary>Maximum number of tokens to generate.</summary>
    [JsonPropertyName("max_tokens")]
    public int? MaxTokens { get; init; }

    /// <summary>Sampling temperature (0.0 - 1.0).</summary>
    [JsonPropertyName("temperature")]
    public double? Temperature { get; init; }
}
