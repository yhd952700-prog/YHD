using LiuHao.Sdk.Models;

namespace LiuHao.Sdk.Resources;

/// <summary>
/// Memory operations resource.
/// </summary>
/// <remarks>
/// Provides storage, retrieval, semantic search, namespace management,
/// and bulk operations for memory entries on the LiuHao AI OS platform.
/// </remarks>
public sealed class MemoryResource
{
    private const string BasePath = "/memory";

    private readonly LiuHaoClient _client;

    /// <summary>
    /// Initializes a new instance of the <see cref="MemoryResource"/> class.
    /// </summary>
    /// <param name="client">The parent SDK client with request capabilities.</param>
    internal MemoryResource(LiuHaoClient client)
    {
        _client = client ?? throw new ArgumentNullException(nameof(client));
    }

    // ------------------------------------------------------------------
    // Basic memory operations
    // ------------------------------------------------------------------

    /// <summary>
    /// Stores a new memory entry.
    /// </summary>
    /// <param name="options">Memory storage parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created memory entry.</returns>
    public Task<MemoryEntry> StoreAsync(
        MemoryStoreOptions options,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<MemoryEntry>(
            BasePath, options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Stores a new memory entry with the given content.
    /// </summary>
    /// <param name="content">The memory content to store.</param>
    /// <param name="category">Memory category classification.</param>
    /// <param name="importance">Importance level for prioritization.</param>
    /// <param name="ns">Optional namespace to store under.</param>
    /// <param name="tags">Tags for categorization.</param>
    /// <param name="source">Origin of the memory.</param>
    /// <param name="entityId">Optional associated entity ID.</param>
    /// <param name="metadata">Arbitrary key-value metadata.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created memory entry.</returns>
    public Task<MemoryEntry> StoreAsync(
        string content,
        MemoryCategory category = MemoryCategory.General,
        MemoryImportance importance = MemoryImportance.Medium,
        string? ns = null,
        IReadOnlyList<string>? tags = null,
        string? source = null,
        string? entityId = null,
        IReadOnlyDictionary<string, object?>? metadata = null,
        CancellationToken cancellationToken = default)
    {
        return StoreAsync(
            new MemoryStoreOptions
            {
                Content = content,
                Category = category,
                Importance = importance,
                Namespace = ns,
                Tags = tags,
                Source = source,
                EntityId = entityId,
                Metadata = metadata,
            },
            cancellationToken);
    }

    /// <summary>
    /// Retrieves a single memory entry by ID.
    /// </summary>
    /// <param name="memoryId">Unique identifier of the memory entry.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The requested memory entry.</returns>
    /// <exception cref="NotFoundException">
    /// Thrown when no memory entry with the given ID exists.
    /// </exception>
    public Task<MemoryEntry> GetAsync(
        string memoryId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(memoryId, nameof(memoryId));
        return _client.GetAsync<MemoryEntry>(
            $"{BasePath}/{memoryId}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Updates an existing memory entry. Only set fields are updated.
    /// </summary>
    /// <param name="memoryId">Unique identifier of the memory entry.</param>
    /// <param name="options">The fields to update.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The updated memory entry.</returns>
    public Task<MemoryEntry> UpdateAsync(
        string memoryId,
        MemoryUpdateOptions options,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(memoryId, nameof(memoryId));
        ArgumentNullException.ThrowIfNull(options);
        return _client.PatchAsync<MemoryEntry>(
            $"{BasePath}/{memoryId}", options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes a memory entry.
    /// </summary>
    /// <param name="memoryId">Unique identifier of the memory entry.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteAsync(
        string memoryId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(memoryId, nameof(memoryId));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/{memoryId}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Lists memory entries with filtering and pagination.
    /// </summary>
    /// <param name="limit">Maximum number of entries to return.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="category">Optional filter by category.</param>
    /// <param name="importance">Optional filter by importance level.</param>
    /// <param name="ns">Optional filter by namespace.</param>
    /// <param name="entityId">Optional filter by associated entity.</param>
    /// <param name="tags">Optional filter by tags (match any).</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of memory entries.</returns>
    public Task<PaginatedResponse<MemoryEntry>> ListAsync(
        int limit = 20,
        int offset = 0,
        MemoryCategory? category = null,
        MemoryImportance? importance = null,
        string? ns = null,
        string? entityId = null,
        IReadOnlyList<string>? tags = null,
        CancellationToken cancellationToken = default)
    {
        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
            ["category"] = category?.ToSnakeCase(),
            ["importance"] = importance?.ToSnakeCase(),
            ["namespace"] = ns,
            ["entity_id"] = entityId,
            ["tags"] = tags is null ? null : string.Join(',', tags),
        };

        return _client.GetAsync<PaginatedResponse<MemoryEntry>>(
            BasePath, query, cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Semantic search
    // ------------------------------------------------------------------

    /// <summary>
    /// Performs semantic search across memory entries.
    /// </summary>
    /// <param name="options">Search parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>
    /// Ranked search results containing memory entries and similarity scores.
    /// </returns>
    public Task<SearchResponse<MemorySearchResult>> SearchAsync(
        MemorySearchOptions options,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<SearchResponse<MemorySearchResult>>(
            $"{BasePath}/search", options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Performs semantic search across memory entries with the given query.
    /// </summary>
    /// <param name="query">The search query text.</param>
    /// <param name="limit">Maximum number of results to return.</param>
    /// <param name="category">Optional filter by category.</param>
    /// <param name="ns">Optional filter by namespace.</param>
    /// <param name="entityId">Optional filter by associated entity.</param>
    /// <param name="threshold">Minimum similarity score (0.0 - 1.0).</param>
    /// <param name="tags">Optional filter by tags.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>
    /// Ranked search results containing memory entries and similarity scores.
    /// </returns>
    public Task<SearchResponse<MemorySearchResult>> SearchAsync(
        string query,
        int limit = 10,
        MemoryCategory? category = null,
        string? ns = null,
        string? entityId = null,
        double threshold = 0.0,
        IReadOnlyList<string>? tags = null,
        CancellationToken cancellationToken = default)
    {
        return SearchAsync(
            new MemorySearchOptions
            {
                Query = query,
                Limit = limit,
                Category = category,
                Namespace = ns,
                EntityId = entityId,
                Threshold = threshold,
                Tags = tags,
            },
            cancellationToken);
    }

    /// <summary>
    /// Performs hybrid (keyword + semantic) search.
    /// </summary>
    /// <param name="query">The search query text.</param>
    /// <param name="limit">Maximum number of results.</param>
    /// <param name="keywordWeight">Weight for keyword matching (0.0 - 1.0).</param>
    /// <param name="semanticWeight">Weight for semantic similarity (0.0 - 1.0).</param>
    /// <param name="category">Optional filter by category.</param>
    /// <param name="entityId">Optional filter by entity.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>Ranked hybrid search results.</returns>
    public Task<SearchResponse<MemorySearchResult>> HybridSearchAsync(
        string query,
        int limit = 10,
        double keywordWeight = 0.3,
        double semanticWeight = 0.7,
        MemoryCategory? category = null,
        string? entityId = null,
        CancellationToken cancellationToken = default)
    {
        var payload = new Dictionary<string, object?>
        {
            ["query"] = query,
            ["limit"] = limit,
            ["keyword_weight"] = keywordWeight,
            ["semantic_weight"] = semanticWeight,
            ["category"] = category?.ToSnakeCase(),
            ["entity_id"] = entityId,
        };

        return _client.PostAsync<SearchResponse<MemorySearchResult>>(
            $"{BasePath}/search/hybrid", payload, cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Bulk operations
    // ------------------------------------------------------------------

    /// <summary>
    /// Stores multiple memory entries in one request.
    /// </summary>
    /// <param name="entries">The entries to store.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list containing the created entries.</returns>
    public Task<PaginatedResponse<MemoryEntry>> BulkStoreAsync(
        IReadOnlyList<MemoryStoreOptions> entries,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(entries);
        return _client.PostAsync<PaginatedResponse<MemoryEntry>>(
            $"{BasePath}/bulk",
            new Dictionary<string, object?> { ["entries"] = entries },
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes multiple memory entries in one request.
    /// </summary>
    /// <param name="memoryIds">Identifiers of the entries to delete.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The bulk deletion confirmation.</returns>
    public Task<OperationResult> BulkDeleteAsync(
        IReadOnlyList<string> memoryIds,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(memoryIds);
        return _client.PostAsync<OperationResult>(
            $"{BasePath}/bulk/delete",
            new Dictionary<string, object?> { ["ids"] = memoryIds },
            cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Namespace and category management
    // ------------------------------------------------------------------

    /// <summary>
    /// Lists all memory namespaces with their entry counts.
    /// </summary>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A list of namespaces.</returns>
    public Task<PaginatedResponse<MemoryNamespace>> ListNamespacesAsync(
        CancellationToken cancellationToken = default)
    {
        return _client.GetAsync<PaginatedResponse<MemoryNamespace>>(
            $"{BasePath}/namespaces", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes an entire namespace and all memory entries within it.
    /// </summary>
    /// <param name="ns">The namespace to delete.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteNamespaceAsync(
        string ns,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(ns, nameof(ns));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/namespaces/{ns}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Lists all available memory categories with their usage counts.
    /// </summary>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A list of per-category statistics.</returns>
    public Task<PaginatedResponse<MemoryCategoryStats>> ListCategoriesAsync(
        CancellationToken cancellationToken = default)
    {
        return _client.GetAsync<PaginatedResponse<MemoryCategoryStats>>(
            $"{BasePath}/categories", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Gets statistics for a specific memory category.
    /// </summary>
    /// <param name="category">The category to get statistics for.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>Statistics for the requested category.</returns>
    public Task<MemoryCategoryStats> GetCategoryStatsAsync(
        MemoryCategory category,
        CancellationToken cancellationToken = default)
    {
        return _client.GetAsync<MemoryCategoryStats>(
            $"{BasePath}/categories/{category.ToSnakeCase()}/stats",
            cancellationToken: cancellationToken);
    }
}
