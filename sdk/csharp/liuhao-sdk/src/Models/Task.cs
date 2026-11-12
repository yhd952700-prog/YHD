using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// Status of an asynchronous task.
/// </summary>
[SnakeCaseEnumConverter]
public enum LiuHaoTaskStatus
{
    /// <summary>The task is waiting to start.</summary>
    Pending,

    /// <summary>The task is queued for processing.</summary>
    Queued,

    /// <summary>The task is currently executing.</summary>
    Running,

    /// <summary>The task completed successfully.</summary>
    Completed,

    /// <summary>The task failed.</summary>
    Failed,

    /// <summary>The task was cancelled.</summary>
    Cancelled,

    /// <summary>The task is being retried after a failure.</summary>
    Retrying,
}

/// <summary>
/// Represents an asynchronous task in the LiuHao AI OS.
/// </summary>
/// <remarks>
/// Named <c>LiuHaoTask</c> to avoid ambiguity with
/// <see cref="System.Threading.Tasks.Task"/>.
/// </remarks>
public sealed record LiuHaoTask
{
    /// <summary>Unique identifier of the task.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>The type of task being performed.</summary>
    [JsonPropertyName("type")]
    public string Type { get; init; } = string.Empty;

    /// <summary>Current status of the task.</summary>
    [JsonPropertyName("status")]
    public LiuHaoTaskStatus Status { get; init; } = LiuHaoTaskStatus.Pending;

    /// <summary>Current progress percentage (0 - 100).</summary>
    [JsonPropertyName("progress")]
    public int Progress { get; init; }

    /// <summary>Human-readable description of the task.</summary>
    [JsonPropertyName("description")]
    public string Description { get; init; } = string.Empty;

    /// <summary>Task result data when completed.</summary>
    [JsonPropertyName("result")]
    public object? Result { get; init; }

    /// <summary>Error information if the task failed.</summary>
    [JsonPropertyName("error")]
    public string? Error { get; init; }

    /// <summary>Number of times the task has been retried.</summary>
    [JsonPropertyName("retry_count")]
    public int RetryCount { get; init; }

    /// <summary>Maximum number of retry attempts.</summary>
    [JsonPropertyName("max_retries")]
    public int MaxRetries { get; init; }

    /// <summary>ISO 8601 creation timestamp.</summary>
    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; init; }

    /// <summary>ISO 8601 timestamp of when the task started executing.</summary>
    [JsonPropertyName("started_at")]
    public DateTimeOffset? StartedAt { get; init; }

    /// <summary>ISO 8601 completion timestamp.</summary>
    [JsonPropertyName("completed_at")]
    public DateTimeOffset? CompletedAt { get; init; }
}

/// <summary>
/// Parameters for creating a new asynchronous task.
/// </summary>
public sealed record TaskCreateOptions
{
    /// <summary>The type of task to create (required).</summary>
    [JsonPropertyName("type")]
    public string Type { get; init; } = string.Empty;

    /// <summary>Human-readable description.</summary>
    [JsonPropertyName("description")]
    public string? Description { get; init; }

    /// <summary>Task-specific parameters.</summary>
    [JsonPropertyName("params")]
    public IReadOnlyDictionary<string, object?>? Params { get; init; }

    /// <summary>Maximum number of retry attempts on failure.</summary>
    [JsonPropertyName("max_retries")]
    public int? MaxRetries { get; init; }
}
