using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// Category classification for memory entries.
/// </summary>
/// <remarks>
/// Categories help organize and filter memories by their nature and
/// intended use.
/// </remarks>
[SnakeCaseEnumConverter]
public enum MemoryCategory
{
    /// <summary>General, uncategorized memory.</summary>
    General,

    /// <summary>A factual memory item.</summary>
    Fact,

    /// <summary>A user preference memory.</summary>
    Preference,

    /// <summary>A conversation-derived memory.</summary>
    Conversation,

    /// <summary>A task-related memory.</summary>
    Task,

    /// <summary>A knowledge-derived memory.</summary>
    Knowledge,

    /// <summary>A user profile memory.</summary>
    UserProfile,

    /// <summary>Contextual working memory.</summary>
    Context,

    /// <summary>A user-defined custom category.</summary>
    Custom,
}

/// <summary>
/// Importance level for memory entries.
/// </summary>
/// <remarks>
/// Used for retrieval prioritization and memory pruning strategies.
/// </remarks>
[SnakeCaseEnumConverter]
public enum MemoryImportance
{
    /// <summary>Low importance; eligible for early pruning.</summary>
    Low,

    /// <summary>Medium importance (default).</summary>
    Medium,

    /// <summary>High importance; prioritized in retrieval.</summary>
    High,

    /// <summary>Critical importance; never pruned automatically.</summary>
    Critical,
}

/// <summary>
/// Represents a single memory entry.
/// </summary>
public sealed record MemoryEntry
{
    /// <summary>Unique memory identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>The actual memory content/text.</summary>
    [JsonPropertyName("content")]
    public string Content { get; init; } = string.Empty;

    /// <summary>Memory category classification.</summary>
    [JsonPropertyName("category")]
    public MemoryCategory Category { get; init; } = MemoryCategory.General;

    /// <summary>Importance level.</summary>
    [JsonPropertyName("importance")]
    public MemoryImportance Importance { get; init; } = MemoryImportance.Medium;

    /// <summary>The namespace this memory belongs to, if any.</summary>
    [JsonPropertyName("namespace")]
    public string? Namespace { get; init; }

    /// <summary>Tags for categorization and search.</summary>
    [JsonPropertyName("tags")]
    public IReadOnlyList<string> Tags { get; init; } = Array.Empty<string>();

    /// <summary>Origin of the memory (e.g. "user_input", "agent_observation").</summary>
    [JsonPropertyName("source")]
    public string Source { get; init; } = string.Empty;

    /// <summary>Optional associated entity (user, agent, task, etc.).</summary>
    [JsonPropertyName("entity_id")]
    public string? EntityId { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();

    /// <summary>Optional embedding vector (not always returned).</summary>
    [JsonPropertyName("embedding")]
    public IReadOnlyList<float>? Embedding { get; init; }

    /// <summary>ISO 8601 creation timestamp.</summary>
    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; init; }

    /// <summary>ISO 8601 last-update timestamp.</summary>
    [JsonPropertyName("updated_at")]
    public DateTimeOffset? UpdatedAt { get; init; }

    /// <summary>Number of times this memory has been accessed.</summary>
    [JsonPropertyName("access_count")]
    public long AccessCount { get; init; }

    /// <summary>ISO 8601 last access timestamp.</summary>
    [JsonPropertyName("last_accessed_at")]
    public DateTimeOffset? LastAccessedAt { get; init; }
}

/// <summary>
/// Result from a memory search query.
/// </summary>
public sealed record MemorySearchResult
{
    /// <summary>The matched memory entry.</summary>
    [JsonPropertyName("entry")]
    public MemoryEntry Entry { get; init; } = new();

    /// <summary>Relevance score (higher = more relevant, 0.0 - 1.0).</summary>
    [JsonPropertyName("score")]
    public double Score { get; init; }

    /// <summary>Type of match (semantic, keyword, hybrid).</summary>
    [JsonPropertyName("match_type")]
    public string MatchType { get; init; } = "semantic";
}

/// <summary>
/// Parameters for storing a new memory entry.
/// </summary>
public sealed record MemoryStoreOptions
{
    /// <summary>The memory content to store (required).</summary>
    [JsonPropertyName("content")]
    public string Content { get; init; } = string.Empty;

    /// <summary>Memory category classification.</summary>
    [JsonPropertyName("category")]
    public MemoryCategory Category { get; init; } = MemoryCategory.General;

    /// <summary>Importance level for prioritization.</summary>
    [JsonPropertyName("importance")]
    public MemoryImportance Importance { get; init; } = MemoryImportance.Medium;

    /// <summary>Optional namespace to store the memory under.</summary>
    [JsonPropertyName("namespace")]
    public string? Namespace { get; init; }

    /// <summary>Tags for categorization.</summary>
    [JsonPropertyName("tags")]
    public IReadOnlyList<string>? Tags { get; init; }

    /// <summary>Origin of the memory.</summary>
    [JsonPropertyName("source")]
    public string? Source { get; init; }

    /// <summary>Optional associated entity ID.</summary>
    [JsonPropertyName("entity_id")]
    public string? EntityId { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }
}

/// <summary>
/// Parameters for updating an existing memory entry.
/// Only set fields are updated.
/// </summary>
public sealed record MemoryUpdateOptions
{
    /// <summary>New memory content.</summary>
    [JsonPropertyName("content")]
    public string? Content { get; init; }

    /// <summary>New category classification.</summary>
    [JsonPropertyName("category")]
    public MemoryCategory? Category { get; init; }

    /// <summary>New importance level.</summary>
    [JsonPropertyName("importance")]
    public MemoryImportance? Importance { get; init; }

    /// <summary>New tag list (replaces existing).</summary>
    [JsonPropertyName("tags")]
    public IReadOnlyList<string>? Tags { get; init; }

    /// <summary>New metadata (replaces existing).</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }
}

/// <summary>
/// Parameters for a semantic memory search.
/// </summary>
public sealed record MemorySearchOptions
{
    /// <summary>The search query text (required).</summary>
    [JsonPropertyName("query")]
    public string Query { get; init; } = string.Empty;

    /// <summary>Maximum number of results to return.</summary>
    [JsonPropertyName("limit")]
    public int Limit { get; init; } = 10;

    /// <summary>Optional filter by category.</summary>
    [JsonPropertyName("category")]
    public MemoryCategory? Category { get; init; }

    /// <summary>Optional filter by namespace.</summary>
    [JsonPropertyName("namespace")]
    public string? Namespace { get; init; }

    /// <summary>Optional filter by associated entity.</summary>
    [JsonPropertyName("entity_id")]
    public string? EntityId { get; init; }

    /// <summary>Minimum similarity score threshold (0.0 - 1.0).</summary>
    [JsonPropertyName("threshold")]
    public double Threshold { get; init; }

    /// <summary>Optional filter by tags.</summary>
    [JsonPropertyName("tags")]
    public IReadOnlyList<string>? Tags { get; init; }
}

/// <summary>
/// A named namespace within the memory system.
/// </summary>
public sealed record MemoryNamespace
{
    /// <summary>The namespace name.</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Number of memory entries stored in this namespace.</summary>
    [JsonPropertyName("entry_count")]
    public long EntryCount { get; init; }

    /// <summary>ISO 8601 creation timestamp.</summary>
    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; init; }

    /// <summary>Arbitrary namespace metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Statistics for a single memory category.
/// </summary>
public sealed record MemoryCategoryStats
{
    /// <summary>The category these statistics describe.</summary>
    [JsonPropertyName("category")]
    public MemoryCategory Category { get; init; }

    /// <summary>Number of entries in this category.</summary>
    [JsonPropertyName("count")]
    public long Count { get; init; }

    /// <summary>Total storage size in bytes, if reported.</summary>
    [JsonPropertyName("size_bytes")]
    public long? SizeBytes { get; init; }

    /// <summary>Average importance score within the category.</summary>
    [JsonPropertyName("avg_importance")]
    public double? AvgImportance { get; init; }
}
