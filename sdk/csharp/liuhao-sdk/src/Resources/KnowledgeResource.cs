using LiuHao.Sdk.Models;

namespace LiuHao.Sdk.Resources;

/// <summary>
/// Knowledge base management resource.
/// </summary>
/// <remarks>
/// Provides CRUD operations for knowledge bases, document ingestion and
/// indexing, semantic search, and collection management on the LiuHao
/// AI OS platform.
/// </remarks>
public sealed class KnowledgeResource
{
    private const string BasePath = "/knowledge";

    private readonly LiuHaoClient _client;

    /// <summary>
    /// Initializes a new instance of the <see cref="KnowledgeResource"/> class.
    /// </summary>
    /// <param name="client">The parent SDK client with request capabilities.</param>
    internal KnowledgeResource(LiuHaoClient client)
    {
        _client = client ?? throw new ArgumentNullException(nameof(client));
    }

    // ------------------------------------------------------------------
    // Knowledge base management
    // ------------------------------------------------------------------

    /// <summary>
    /// Lists knowledge bases with pagination.
    /// </summary>
    /// <param name="limit">Maximum number of knowledge bases to return.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="status">Optional filter by status.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of knowledge bases.</returns>
    public Task<PaginatedResponse<KnowledgeBase>> ListBasesAsync(
        int limit = 20,
        int offset = 0,
        KnowledgeBaseStatus? status = null,
        CancellationToken cancellationToken = default)
    {
        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
            ["status"] = status?.ToSnakeCase(),
        };

        return _client.GetAsync<PaginatedResponse<KnowledgeBase>>(
            $"{BasePath}/bases", query, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Retrieves a knowledge base by ID.
    /// </summary>
    /// <param name="baseId">Unique identifier of the knowledge base.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The requested knowledge base.</returns>
    /// <exception cref="NotFoundException">
    /// Thrown when no knowledge base with the given ID exists.
    /// </exception>
    public Task<KnowledgeBase> GetBaseAsync(
        string baseId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        return _client.GetAsync<KnowledgeBase>(
            $"{BasePath}/bases/{baseId}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Creates a new knowledge base.
    /// </summary>
    /// <param name="options">Creation parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created knowledge base.</returns>
    public Task<KnowledgeBase> CreateBaseAsync(
        KnowledgeBaseCreateOptions options,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<KnowledgeBase>(
            $"{BasePath}/bases", options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Creates a new knowledge base with the given name.
    /// </summary>
    /// <param name="name">Human-readable knowledge base name.</param>
    /// <param name="description">Brief description.</param>
    /// <param name="embeddingModel">Embedding model to use.</param>
    /// <param name="metadata">Arbitrary key-value metadata.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created knowledge base.</returns>
    public Task<KnowledgeBase> CreateBaseAsync(
        string name,
        string? description = null,
        string? embeddingModel = null,
        IReadOnlyDictionary<string, object?>? metadata = null,
        CancellationToken cancellationToken = default)
    {
        return CreateBaseAsync(
            new KnowledgeBaseCreateOptions
            {
                Name = name,
                Description = description,
                EmbeddingModel = embeddingModel,
                Metadata = metadata,
            },
            cancellationToken);
    }

    /// <summary>
    /// Updates an existing knowledge base.
    /// </summary>
    /// <param name="baseId">Unique identifier of the knowledge base.</param>
    /// <param name="name">New name.</param>
    /// <param name="description">New description.</param>
    /// <param name="metadata">New metadata (replaces existing).</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The updated knowledge base.</returns>
    public Task<KnowledgeBase> UpdateBaseAsync(
        string baseId,
        string? name = null,
        string? description = null,
        IReadOnlyDictionary<string, object?>? metadata = null,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));

        var payload = new Dictionary<string, object?>
        {
            ["name"] = name,
            ["description"] = description,
            ["metadata"] = metadata,
        };

        return _client.PatchAsync<KnowledgeBase>(
            $"{BasePath}/bases/{baseId}", payload, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes a knowledge base and all its contents.
    /// </summary>
    /// <param name="baseId">Unique identifier of the knowledge base.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteBaseAsync(
        string baseId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}", cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Document ingestion and indexing
    // ------------------------------------------------------------------

    /// <summary>
    /// Ingests a document into a knowledge base.
    /// </summary>
    /// <param name="baseId">Target knowledge base identifier.</param>
    /// <param name="options">Document ingestion parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The ingested document record.</returns>
    public Task<KnowledgeDocument> IngestAsync(
        string baseId,
        DocumentIngestOptions options,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        ArgumentNullException.ThrowIfNull(options);

        var format = options.Format ?? InferFormat(options.Filename);
        var payload = new Dictionary<string, object?>
        {
            ["filename"] = options.Filename,
            ["content"] = options.Content,
            ["format"] = format.ToSnakeCase(),
            ["collection_id"] = options.CollectionId,
            ["chunk_size"] = options.ChunkSize,
            ["chunk_overlap"] = options.ChunkOverlap,
            ["metadata"] = options.Metadata,
        };

        return _client.PostAsync<KnowledgeDocument>(
            $"{BasePath}/bases/{baseId}/documents", payload,
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Ingests a text document into a knowledge base.
    /// </summary>
    /// <param name="baseId">Target knowledge base identifier.</param>
    /// <param name="filename">Original filename.</param>
    /// <param name="content">Text content of the document.</param>
    /// <param name="format">Document format; inferred from filename if omitted.</param>
    /// <param name="collectionId">Optional collection to add the document to.</param>
    /// <param name="metadata">Arbitrary key-value metadata.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The ingested document record.</returns>
    public Task<KnowledgeDocument> IngestAsync(
        string baseId,
        string filename,
        string content,
        DocumentFormat? format = null,
        string? collectionId = null,
        IReadOnlyDictionary<string, object?>? metadata = null,
        CancellationToken cancellationToken = default)
    {
        return IngestAsync(
            baseId,
            new DocumentIngestOptions
            {
                Filename = filename,
                Content = content,
                Format = format,
                CollectionId = collectionId,
                Metadata = metadata,
            },
            cancellationToken);
    }

    /// <summary>
    /// Gets details of a specific document.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="documentId">Document identifier.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The requested document.</returns>
    public Task<KnowledgeDocument> GetDocumentAsync(
        string baseId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(documentId, nameof(documentId));
        return _client.GetAsync<KnowledgeDocument>(
            $"{BasePath}/bases/{baseId}/documents/{documentId}",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Lists documents in a knowledge base.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="limit">Maximum number of documents.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="status">Optional filter by status.</param>
    /// <param name="collectionId">Optional filter by collection.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of documents.</returns>
    public Task<PaginatedResponse<KnowledgeDocument>> ListDocumentsAsync(
        string baseId,
        int limit = 20,
        int offset = 0,
        DocumentStatus? status = null,
        string? collectionId = null,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));

        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
            ["status"] = status?.ToSnakeCase(),
            ["collection_id"] = collectionId,
        };

        return _client.GetAsync<PaginatedResponse<KnowledgeDocument>>(
            $"{BasePath}/bases/{baseId}/documents", query,
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes a document from a knowledge base.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="documentId">Document identifier.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteDocumentAsync(
        string baseId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(documentId, nameof(documentId));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}/documents/{documentId}",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Triggers re-indexing of a document.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="documentId">Document identifier.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The re-index confirmation.</returns>
    public Task<OperationResult> ReindexDocumentAsync(
        string baseId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(documentId, nameof(documentId));
        return _client.PostAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}/documents/{documentId}/reindex",
            cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Semantic search
    // ------------------------------------------------------------------

    /// <summary>
    /// Searches within a knowledge base.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="options">Search parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>Ranked search results.</returns>
    public Task<SearchResponse<KnowledgeSearchResult>> SearchAsync(
        string baseId,
        KnowledgeSearchOptions options,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<SearchResponse<KnowledgeSearchResult>>(
            $"{BasePath}/bases/{baseId}/search", options,
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Searches within a knowledge base with the given query.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="query">The search query text.</param>
    /// <param name="limit">Maximum number of results.</param>
    /// <param name="mode">Search mode (semantic, keyword, or hybrid).</param>
    /// <param name="threshold">Minimum relevance score (0.0 - 1.0).</param>
    /// <param name="collectionId">Optional filter by collection.</param>
    /// <param name="filters">Optional metadata filters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>Ranked search results.</returns>
    public Task<SearchResponse<KnowledgeSearchResult>> SearchAsync(
        string baseId,
        string query,
        int limit = 10,
        SearchMode mode = SearchMode.Semantic,
        double threshold = 0.0,
        string? collectionId = null,
        IReadOnlyDictionary<string, object?>? filters = null,
        CancellationToken cancellationToken = default)
    {
        return SearchAsync(
            baseId,
            new KnowledgeSearchOptions
            {
                Query = query,
                Limit = limit,
                Mode = mode,
                Threshold = threshold,
                CollectionId = collectionId,
                Filters = filters,
            },
            cancellationToken);
    }

    /// <summary>
    /// Searches across multiple knowledge bases.
    /// </summary>
    /// <param name="baseIds">List of knowledge base identifiers.</param>
    /// <param name="query">The search query text.</param>
    /// <param name="limit">Maximum number of total results.</param>
    /// <param name="mode">Search mode.</param>
    /// <param name="threshold">Minimum relevance score.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>Aggregated search results.</returns>
    public Task<SearchResponse<KnowledgeSearchResult>> MultiBaseSearchAsync(
        IReadOnlyList<string> baseIds,
        string query,
        int limit = 10,
        SearchMode mode = SearchMode.Semantic,
        double threshold = 0.0,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(baseIds);

        var payload = new Dictionary<string, object?>
        {
            ["base_ids"] = baseIds,
            ["query"] = query,
            ["limit"] = limit,
            ["mode"] = mode.ToSnakeCase(),
            ["threshold"] = threshold,
        };

        return _client.PostAsync<SearchResponse<KnowledgeSearchResult>>(
            $"{BasePath}/search", payload, cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Collection management
    // ------------------------------------------------------------------

    /// <summary>
    /// Lists collections in a knowledge base.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="limit">Maximum number of collections.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of collections.</returns>
    public Task<PaginatedResponse<KnowledgeCollection>> ListCollectionsAsync(
        string baseId,
        int limit = 20,
        int offset = 0,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));

        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
        };

        return _client.GetAsync<PaginatedResponse<KnowledgeCollection>>(
            $"{BasePath}/bases/{baseId}/collections", query,
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Creates a new collection within a knowledge base.
    /// </summary>
    /// <param name="baseId">Parent knowledge base identifier.</param>
    /// <param name="name">Collection name.</param>
    /// <param name="description">Collection description.</param>
    /// <param name="metadata">Arbitrary key-value metadata.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created collection.</returns>
    public Task<KnowledgeCollection> CreateCollectionAsync(
        string baseId,
        string name,
        string? description = null,
        IReadOnlyDictionary<string, object?>? metadata = null,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));

        var payload = new Dictionary<string, object?>
        {
            ["name"] = name,
            ["description"] = description,
            ["metadata"] = metadata,
        };

        return _client.PostAsync<KnowledgeCollection>(
            $"{BasePath}/bases/{baseId}/collections", payload,
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Gets details of a collection.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="collectionId">Collection identifier.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The requested collection.</returns>
    public Task<KnowledgeCollection> GetCollectionAsync(
        string baseId,
        string collectionId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(collectionId, nameof(collectionId));
        return _client.GetAsync<KnowledgeCollection>(
            $"{BasePath}/bases/{baseId}/collections/{collectionId}",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes a collection. Documents are not deleted, only unassigned.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="collectionId">Collection identifier.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteCollectionAsync(
        string baseId,
        string collectionId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(collectionId, nameof(collectionId));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}/collections/{collectionId}",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Adds a document to a collection.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="collectionId">Collection identifier.</param>
    /// <param name="documentId">Document identifier to add.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The operation confirmation.</returns>
    public Task<OperationResult> AddDocumentToCollectionAsync(
        string baseId,
        string collectionId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(collectionId, nameof(collectionId));
        Guard.NotNullOrEmpty(documentId, nameof(documentId));

        return _client.PostAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}/collections/{collectionId}/documents",
            new Dictionary<string, object?> { ["document_id"] = documentId },
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Removes a document from a collection.
    /// </summary>
    /// <param name="baseId">Knowledge base identifier.</param>
    /// <param name="collectionId">Collection identifier.</param>
    /// <param name="documentId">Document identifier to remove.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The operation confirmation.</returns>
    public Task<OperationResult> RemoveDocumentFromCollectionAsync(
        string baseId,
        string collectionId,
        string documentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(baseId, nameof(baseId));
        Guard.NotNullOrEmpty(collectionId, nameof(collectionId));
        Guard.NotNullOrEmpty(documentId, nameof(documentId));

        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/bases/{baseId}/collections/{collectionId}/documents/{documentId}",
            cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    /// <summary>
    /// Infers a <see cref="DocumentFormat"/> from a filename extension.
    /// Falls back to <see cref="DocumentFormat.Txt"/> for unknown extensions.
    /// </summary>
    /// <param name="filename">The filename to inspect.</param>
    /// <returns>The inferred document format.</returns>
    public static DocumentFormat InferFormat(string filename)
    {
        var extension = Path.GetExtension(filename)?.TrimStart('.').ToLowerInvariant();

        return extension switch
        {
            "pdf" => DocumentFormat.Pdf,
            "docx" or "doc" => DocumentFormat.Docx,
            "md" or "markdown" => DocumentFormat.Markdown,
            "html" or "htm" => DocumentFormat.Html,
            "csv" => DocumentFormat.Csv,
            "json" => DocumentFormat.Json,
            _ => DocumentFormat.Txt,
        };
    }
}
