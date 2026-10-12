using System.Text.Json.Serialization;

namespace LiuHao.Sdk.Models;

/// <summary>
/// Lifecycle status of a knowledge base.
/// </summary>
[SnakeCaseEnumConverter]
public enum KnowledgeBaseStatus
{
    /// <summary>The knowledge base is being created.</summary>
    Creating,

    /// <summary>The knowledge base is ready for use.</summary>
    Ready,

    /// <summary>The knowledge base is currently indexing documents.</summary>
    Indexing,

    /// <summary>The knowledge base is in an error state.</summary>
    Error,

    /// <summary>The knowledge base has been deleted.</summary>
    Deleted,
}

/// <summary>
/// Processing status of a document in a knowledge base.
/// </summary>
[SnakeCaseEnumConverter]
public enum DocumentStatus
{
    /// <summary>The document has been uploaded but not yet processed.</summary>
    Uploaded,

    /// <summary>The document is being parsed.</summary>
    Parsing,

    /// <summary>The document is being indexed.</summary>
    Indexing,

    /// <summary>The document is ready for search.</summary>
    Ready,

    /// <summary>Document processing failed.</summary>
    Failed,

    /// <summary>The document has been deleted.</summary>
    Deleted,
}

/// <summary>
/// Supported document formats for knowledge base upload.
/// </summary>
[SnakeCaseEnumConverter]
public enum DocumentFormat
{
    /// <summary>Plain text.</summary>
    Txt,

    /// <summary>PDF document.</summary>
    Pdf,

    /// <summary>Microsoft Word document.</summary>
    Docx,

    /// <summary>Markdown document.</summary>
    Markdown,

    /// <summary>HTML document.</summary>
    Html,

    /// <summary>CSV data.</summary>
    Csv,

    /// <summary>JSON data.</summary>
    Json,
}

/// <summary>
/// Search mode for knowledge base queries.
/// </summary>
[SnakeCaseEnumConverter]
public enum SearchMode
{
    /// <summary>Pure vector/semantic similarity search.</summary>
    Semantic,

    /// <summary>Keyword (BM25-style) search.</summary>
    Keyword,

    /// <summary>Combined keyword + semantic search.</summary>
    Hybrid,
}

/// <summary>
/// Represents a knowledge base.
/// </summary>
public sealed record KnowledgeBase
{
    /// <summary>Unique knowledge base identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>Human-readable knowledge base name.</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Brief description of the knowledge base content.</summary>
    [JsonPropertyName("description")]
    public string Description { get; init; } = string.Empty;

    /// <summary>Current lifecycle status.</summary>
    [JsonPropertyName("status")]
    public KnowledgeBaseStatus Status { get; init; } = KnowledgeBaseStatus.Creating;

    /// <summary>Number of documents in the base.</summary>
    [JsonPropertyName("document_count")]
    public long DocumentCount { get; init; }

    /// <summary>Number of text chunks indexed.</summary>
    [JsonPropertyName("chunk_count")]
    public long ChunkCount { get; init; }

    /// <summary>Model used for vector embeddings.</summary>
    [JsonPropertyName("embedding_model")]
    public string EmbeddingModel { get; init; } = "default";

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
/// Represents a document within a knowledge base.
/// </summary>
public sealed record KnowledgeDocument
{
    /// <summary>Unique document identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>Parent knowledge base ID.</summary>
    [JsonPropertyName("knowledge_base_id")]
    public string KnowledgeBaseId { get; init; } = string.Empty;

    /// <summary>Original filename.</summary>
    [JsonPropertyName("filename")]
    public string Filename { get; init; } = string.Empty;

    /// <summary>Document format/type.</summary>
    [JsonPropertyName("format")]
    public DocumentFormat Format { get; init; } = DocumentFormat.Txt;

    /// <summary>Processing status.</summary>
    [JsonPropertyName("status")]
    public DocumentStatus Status { get; init; } = DocumentStatus.Uploaded;

    /// <summary>File size in bytes.</summary>
    [JsonPropertyName("size_bytes")]
    public long SizeBytes { get; init; }

    /// <summary>Number of chunks created from this document.</summary>
    [JsonPropertyName("chunk_count")]
    public long ChunkCount { get; init; }

    /// <summary>Error details if processing failed.</summary>
    [JsonPropertyName("error_message")]
    public string? ErrorMessage { get; init; }

    /// <summary>ISO 8601 upload timestamp.</summary>
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
/// Represents a named collection of documents within a knowledge base.
/// </summary>
public sealed record KnowledgeCollection
{
    /// <summary>Unique collection identifier.</summary>
    [JsonPropertyName("id")]
    public string Id { get; init; } = string.Empty;

    /// <summary>Parent knowledge base ID.</summary>
    [JsonPropertyName("knowledge_base_id")]
    public string KnowledgeBaseId { get; init; } = string.Empty;

    /// <summary>Collection name.</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Collection description.</summary>
    [JsonPropertyName("description")]
    public string Description { get; init; } = string.Empty;

    /// <summary>Number of documents in the collection.</summary>
    [JsonPropertyName("document_count")]
    public long DocumentCount { get; init; }

    /// <summary>ISO 8601 creation timestamp.</summary>
    [JsonPropertyName("created_at")]
    public DateTimeOffset? CreatedAt { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Result from a knowledge base search.
/// </summary>
public sealed record KnowledgeSearchResult
{
    /// <summary>Identifier of the matching text chunk.</summary>
    [JsonPropertyName("chunk_id")]
    public string ChunkId { get; init; } = string.Empty;

    /// <summary>Source document identifier.</summary>
    [JsonPropertyName("document_id")]
    public string DocumentId { get; init; } = string.Empty;

    /// <summary>Knowledge base identifier.</summary>
    [JsonPropertyName("knowledge_base_id")]
    public string KnowledgeBaseId { get; init; } = string.Empty;

    /// <summary>The matching text content.</summary>
    [JsonPropertyName("content")]
    public string Content { get; init; } = string.Empty;

    /// <summary>Relevance score (0.0 - 1.0).</summary>
    [JsonPropertyName("score")]
    public double Score { get; init; }

    /// <summary>Document source reference.</summary>
    [JsonPropertyName("source")]
    public string Source { get; init; } = string.Empty;

    /// <summary>Optional page number in the source document.</summary>
    [JsonPropertyName("page_number")]
    public int? PageNumber { get; init; }

    /// <summary>Metadata from the source document.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?> Metadata { get; init; } =
        new Dictionary<string, object?>();
}

/// <summary>
/// Parameters for creating a new knowledge base.
/// </summary>
public sealed record KnowledgeBaseCreateOptions
{
    /// <summary>Human-readable knowledge base name (required).</summary>
    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    /// <summary>Brief description.</summary>
    [JsonPropertyName("description")]
    public string? Description { get; init; }

    /// <summary>Embedding model to use.</summary>
    [JsonPropertyName("embedding_model")]
    public string? EmbeddingModel { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }
}

/// <summary>
/// Parameters for uploading/ingesting a document into a knowledge base.
/// </summary>
public sealed record DocumentIngestOptions
{
    /// <summary>Original filename (required).</summary>
    [JsonPropertyName("filename")]
    public string Filename { get; init; } = string.Empty;

    /// <summary>Text content of the document (required).</summary>
    [JsonPropertyName("content")]
    public string Content { get; init; } = string.Empty;

    /// <summary>Document format. Inferred from filename if omitted.</summary>
    [JsonPropertyName("format")]
    public DocumentFormat? Format { get; init; }

    /// <summary>Optional collection to add the document to.</summary>
    [JsonPropertyName("collection_id")]
    public string? CollectionId { get; init; }

    /// <summary>Chunk size in characters for splitting.</summary>
    [JsonPropertyName("chunk_size")]
    public int? ChunkSize { get; init; }

    /// <summary>Overlap between chunks in characters.</summary>
    [JsonPropertyName("chunk_overlap")]
    public int? ChunkOverlap { get; init; }

    /// <summary>Arbitrary key-value metadata.</summary>
    [JsonPropertyName("metadata")]
    public IReadOnlyDictionary<string, object?>? Metadata { get; init; }
}

/// <summary>
/// Parameters for searching a knowledge base.
/// </summary>
public sealed record KnowledgeSearchOptions
{
    /// <summary>The search query text (required).</summary>
    [JsonPropertyName("query")]
    public string Query { get; init; } = string.Empty;

    /// <summary>Maximum number of results.</summary>
    [JsonPropertyName("limit")]
    public int Limit { get; init; } = 10;

    /// <summary>Search mode (semantic, keyword, or hybrid).</summary>
    [JsonPropertyName("mode")]
    public SearchMode Mode { get; init; } = SearchMode.Semantic;

    /// <summary>Minimum relevance score (0.0 - 1.0).</summary>
    [JsonPropertyName("threshold")]
    public double Threshold { get; init; }

    /// <summary>Optional filter by collection.</summary>
    [JsonPropertyName("collection_id")]
    public string? CollectionId { get; init; }

    /// <summary>Optional metadata filters.</summary>
    [JsonPropertyName("filters")]
    public IReadOnlyDictionary<string, object?>? Filters { get; init; }
}
