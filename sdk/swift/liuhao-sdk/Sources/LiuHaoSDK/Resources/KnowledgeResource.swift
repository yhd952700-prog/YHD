//
//  KnowledgeResource.swift
//  LiuHaoSDK
//
//  Knowledge base management resource.
//

import Foundation

/// Knowledge base management resource.
///
/// Accessed through ``LiuHaoClient/knowledge``. Provides knowledge
/// base CRUD, document upload and management, and semantic search.
///
/// ```swift
/// let base = try await client.knowledge.createBase(
///     name: "Product Docs",
///     description: "Official product documentation"
/// )
/// let doc = try await client.knowledge.addDocument(
///     baseID: base.id,
///     filename: "guide.md",
///     content: markdownText,
///     format: .markdown
/// )
/// let hits = try await client.knowledge.search(baseID: base.id, query: "install")
/// ```
public struct KnowledgeResource: Sendable {

    private let client: LiuHaoClient
    private let basePath = "/knowledge"

    init(client: LiuHaoClient) {
        self.client = client
    }

    // MARK: - Knowledge base CRUD

    /// Lists knowledge bases with pagination.
    ///
    /// - Parameters:
    ///   - limit: Maximum number of bases to return. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    /// - Returns: A paginated list of ``KnowledgeBase`` values.
    public func listBases(
        limit: Int = 20,
        offset: Int = 0
    ) async throws -> PaginatedResponse<KnowledgeBase> {
        try await client.get(basePath, query: [
            "limit": String(limit),
            "offset": String(offset)
        ])
    }

    /// Retrieves a knowledge base by identifier.
    ///
    /// - Parameter baseID: Unique knowledge base identifier.
    /// - Returns: The requested ``KnowledgeBase``.
    /// - Throws: ``LiuHaoError/notFound(message:requestID:)`` when the
    ///   base does not exist.
    public func getBase(baseID: String) async throws -> KnowledgeBase {
        try await client.get("\(basePath)/\(baseID)")
    }

    /// Creates a new knowledge base.
    ///
    /// - Parameters:
    ///   - name: Human-readable knowledge base name.
    ///   - description: Brief description of the content.
    ///   - embeddingModel: Model used for vector embeddings. Defaults to `"default"`.
    ///   - metadata: Arbitrary caller-defined metadata.
    /// - Returns: The created ``KnowledgeBase``.
    public func createBase(
        name: String,
        description: String = "",
        embeddingModel: String = "default",
        metadata: [String: JSONValue] = [:]
    ) async throws -> KnowledgeBase {
        let body = CreateBaseRequest(
            name: name,
            description: description,
            embeddingModel: embeddingModel,
            metadata: metadata
        )
        return try await client.post(basePath, body: body)
    }

    /// Updates a knowledge base.
    ///
    /// Only non-`nil` parameters are modified.
    ///
    /// - Parameters:
    ///   - baseID: Identifier of the base to update.
    ///   - name: New name.
    ///   - description: New description.
    ///   - metadata: New metadata (replaces existing).
    /// - Returns: The updated ``KnowledgeBase``.
    public func updateBase(
        baseID: String,
        name: String? = nil,
        description: String? = nil,
        metadata: [String: JSONValue]? = nil
    ) async throws -> KnowledgeBase {
        let body = UpdateBaseRequest(name: name, description: description, metadata: metadata)
        return try await client.patch("\(basePath)/\(baseID)", body: body)
    }

    /// Deletes a knowledge base and all of its documents.
    ///
    /// - Parameter baseID: Identifier of the base to delete.
    /// - Returns: A ``DeleteResponse`` confirmation.
    public func deleteBase(baseID: String) async throws -> DeleteResponse {
        try await client.delete("\(basePath)/\(baseID)")
    }

    // MARK: - Documents

    /// Lists documents in a knowledge base.
    ///
    /// - Parameters:
    ///   - baseID: Knowledge base identifier.
    ///   - limit: Maximum number of documents to return. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    ///   - status: Optional processing status filter.
    /// - Returns: A paginated list of ``KnowledgeDocument`` values.
    public func listDocuments(
        baseID: String,
        limit: Int = 20,
        offset: Int = 0,
        status: DocumentStatus? = nil
    ) async throws -> PaginatedResponse<KnowledgeDocument> {
        try await client.get("\(basePath)/\(baseID)/documents", query: [
            "limit": String(limit),
            "offset": String(offset),
            "status": status?.rawValue
        ])
    }

    /// Retrieves a document by identifier.
    ///
    /// - Parameters:
    ///   - baseID: Knowledge base identifier.
    ///   - documentID: Document identifier.
    /// - Returns: The requested ``KnowledgeDocument``.
    public func getDocument(baseID: String, documentID: String) async throws -> KnowledgeDocument {
        try await client.get("\(basePath)/\(baseID)/documents/\(documentID)")
    }

    /// Uploads a text document to a knowledge base.
    ///
    /// The document is parsed, chunked, and indexed asynchronously;
    /// poll ``getDocument(baseID:documentID:)`` until
    /// ``KnowledgeDocument/status`` becomes ``DocumentStatus/ready``.
    ///
    /// - Parameters:
    ///   - baseID: Knowledge base identifier.
    ///   - filename: Original filename.
    ///   - content: Document text content.
    ///   - format: Document format. Defaults to ``DocumentFormat/txt``.
    ///   - metadata: Arbitrary caller-defined metadata.
    /// - Returns: The created ``KnowledgeDocument``.
    public func addDocument(
        baseID: String,
        filename: String,
        content: String,
        format: DocumentFormat = .txt,
        metadata: [String: JSONValue] = [:]
    ) async throws -> KnowledgeDocument {
        let body = AddDocumentRequest(
            filename: filename,
            content: content,
            format: format,
            metadata: metadata
        )
        return try await client.post("\(basePath)/\(baseID)/documents", body: body)
    }

    /// Deletes a document from a knowledge base.
    ///
    /// - Parameters:
    ///   - baseID: Knowledge base identifier.
    ///   - documentID: Document identifier.
    /// - Returns: A ``DeleteResponse`` confirmation.
    public func deleteDocument(baseID: String, documentID: String) async throws -> DeleteResponse {
        try await client.delete("\(basePath)/\(baseID)/documents/\(documentID)")
    }

    // MARK: - Search

    /// Performs a search over a knowledge base.
    ///
    /// - Parameters:
    ///   - baseID: Knowledge base identifier.
    ///   - query: Natural-language search query.
    ///   - mode: Search mode. Defaults to ``KnowledgeSearchMode/hybrid``.
    ///   - limit: Maximum number of results. Defaults to `10`.
    ///   - minScore: Minimum relevance score (0–1). Defaults to `0`.
    /// - Returns: Ranked ``KnowledgeSearchResult`` values.
    public func search(
        baseID: String,
        query: String,
        mode: KnowledgeSearchMode = .hybrid,
        limit: Int = 10,
        minScore: Double = 0
    ) async throws -> [KnowledgeSearchResult] {
        let body = SearchKnowledgeRequest(
            query: query,
            mode: mode,
            limit: limit,
            minScore: minScore
        )
        let response: SearchKnowledgeResponse = try await client.post(
            "\(basePath)/\(baseID)/search",
            body: body
        )
        return response.results
    }
}

// MARK: - Request / response bodies

private struct CreateBaseRequest: Encodable, Sendable {
    let name: String
    let description: String
    let embeddingModel: String
    let metadata: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case name, description, metadata
        case embeddingModel = "embedding_model"
    }
}

private struct UpdateBaseRequest: Encodable, Sendable {
    let name: String?
    let description: String?
    let metadata: [String: JSONValue]?
}

private struct AddDocumentRequest: Encodable, Sendable {
    let filename: String
    let content: String
    let format: DocumentFormat
    let metadata: [String: JSONValue]
}

private struct SearchKnowledgeRequest: Encodable, Sendable {
    let query: String
    let mode: KnowledgeSearchMode
    let limit: Int
    let minScore: Double

    enum CodingKeys: String, CodingKey {
        case query, mode, limit
        case minScore = "min_score"
    }
}

private struct SearchKnowledgeResponse: Decodable, Sendable {
    let results: [KnowledgeSearchResult]
}
