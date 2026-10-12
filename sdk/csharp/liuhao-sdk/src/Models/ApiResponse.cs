using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// A generic paginated list response from the LiuHao API.
/// </summary>
/// <typeparam name="T">The type of items contained in the page.</typeparam>
public sealed record PaginatedResponse<T>
{
    /// <summary>The items contained in the current page.</summary>
    [JsonPropertyName("items")]
    public IReadOnlyList<T> Items { get; init; } = Array.Empty<T>();

    /// <summary>Total number of items across all pages.</summary>
    [JsonPropertyName("total")]
    public long Total { get; init; }

    /// <summary>The current page number (1-based), when page-based
    /// pagination is used by the endpoint.</summary>
    [JsonPropertyName("page")]
    public int? Page { get; init; }

    /// <summary>The page size, when reported by the endpoint.</summary>
    [JsonPropertyName("page_size")]
    public int? PageSize { get; init; }

    /// <summary>The pagination offset used for this request.</summary>
    [JsonPropertyName("offset")]
    public int? Offset { get; init; }

    /// <summary>The maximum number of items requested.</summary>
    [JsonPropertyName("limit")]
    public int? Limit { get; init; }

    /// <summary>Whether a next page exists.</summary>
    [JsonPropertyName("has_next")]
    public bool? HasNext { get; init; }

    /// <summary>Whether a previous page exists.</summary>
    [JsonPropertyName("has_prev")]
    public bool? HasPrev { get; init; }
}

/// <summary>
/// A generic search response wrapping a list of scored results.
/// </summary>
/// <typeparam name="T">The type of a single search result.</typeparam>
public sealed record SearchResponse<T>
{
    /// <summary>The ranked list of search results.</summary>
    [JsonPropertyName("results")]
    public IReadOnlyList<T> Results { get; init; } = Array.Empty<T>();

    /// <summary>Total number of matching items, if reported.</summary>
    [JsonPropertyName("total")]
    public long? Total { get; init; }

    /// <summary>The query that produced these results, echoed back.</summary>
    [JsonPropertyName("query")]
    public string? Query { get; init; }

    /// <summary>Time taken to execute the search, in milliseconds.</summary>
    [JsonPropertyName("took_ms")]
    public long? TookMs { get; init; }
}

/// <summary>
/// A generic confirmation returned by mutating endpoints
/// (delete, cancel, reindex, etc.).
/// </summary>
public sealed record OperationResult
{
    /// <summary>Whether the operation succeeded.</summary>
    [JsonPropertyName("success")]
    public bool Success { get; init; }

    /// <summary>Optional human-readable message.</summary>
    [JsonPropertyName("message")]
    public string? Message { get; init; }

    /// <summary>Number of affected entities, when applicable
    /// (e.g. <c>deleted_count</c> normalized by the server).</summary>
    [JsonPropertyName("affected_count")]
    public long? AffectedCount { get; init; }
}

/// <summary>
/// Structured error payload returned by the LiuHao API.
/// </summary>
public sealed record ApiError
{
    /// <summary>Machine-readable error code.</summary>
    [JsonPropertyName("code")]
    public string? Code { get; init; }

    /// <summary>Human-readable error message.</summary>
    [JsonPropertyName("message")]
    public string? Message { get; init; }

    /// <summary>Additional structured error details.</summary>
    [JsonPropertyName("details")]
    public IReadOnlyDictionary<string, object?> Details { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Envelope of an error response returned by the LiuHao API.
/// </summary>
public sealed record ErrorResponse
{
    /// <summary>The structured error payload.</summary>
    [JsonPropertyName("error")]
    public ApiError? Error { get; init; }
}

/// <summary>
/// Overall health status of the platform.
/// </summary>
[SnakeCaseEnumConverter]
public enum HealthStatus
{
    /// <summary>All systems are operational.</summary>
    Healthy,

    /// <summary>Some systems are experiencing issues.</summary>
    Degraded,

    /// <summary>Critical systems are down.</summary>
    Unhealthy,
}

/// <summary>
/// Health information for an individual platform component.
/// </summary>
public sealed record ComponentHealth
{
    /// <summary>Health status of this component.</summary>
    [JsonPropertyName("status")]
    public HealthStatus Status { get; init; }

    /// <summary>Additional information about the component state.</summary>
    [JsonPropertyName("message")]
    public string? Message { get; init; }

    /// <summary>Response latency in milliseconds.</summary>
    [JsonPropertyName("latency_ms")]
    public long? LatencyMs { get; init; }

    /// <summary>When this component was last checked.</summary>
    [JsonPropertyName("last_checked")]
    public DateTimeOffset? LastChecked { get; init; }
}

/// <summary>
/// Overall health check result of the LiuHao AI OS platform.
/// </summary>
public sealed record HealthCheck
{
    /// <summary>Overall health status.</summary>
    [JsonPropertyName("status")]
    public HealthStatus Status { get; init; }

    /// <summary>Version of the LiuHao AI OS.</summary>
    [JsonPropertyName("version")]
    public string Version { get; init; } = string.Empty;

    /// <summary>When the health check was performed.</summary>
    [JsonPropertyName("timestamp")]
    public DateTimeOffset? Timestamp { get; init; }

    /// <summary>System uptime in seconds.</summary>
    [JsonPropertyName("uptime")]
    public long Uptime { get; init; }

    /// <summary>Per-component health information keyed by component name.</summary>
    [JsonPropertyName("components")]
    public IReadOnlyDictionary<string, ComponentHealth> Components { get; init; } =
        new Dictionary<string, ComponentHealth>();
}

/// <summary>
/// API version and platform information.
/// </summary>
public sealed record ApiInfo
{
    /// <summary>The API version string (e.g. "v1").</summary>
    [JsonPropertyName("version")]
    public string Version { get; init; } = string.Empty;

    /// <summary>The build identifier of the running service.</summary>
    [JsonPropertyName("build")]
    public string? Build { get; init; }

    /// <summary>Feature flags enabled on the platform.</summary>
    [JsonPropertyName("features")]
    public IReadOnlyList<string> Features { get; init; } = Array.Empty<string>();

    /// <summary>Server time at the moment of the request.</summary>
    [JsonPropertyName("server_time")]
    public DateTimeOffset? ServerTime { get; init; }
}
