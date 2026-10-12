//
//  MemoryResource.swift
//  LiuHaoSDK
//
//  Memory operations resource.
//

import Foundation

/// Memory operations resource.
///
/// Accessed through ``LiuHaoClient/memory``. Provides storage,
/// retrieval, semantic search, and management of memory entries.
///
/// ```swift
/// let entry = try await client.memory.store(
///     content: "User prefers dark mode",
///     category: .preference,
///     importance: .high,
///     tags: ["ui", "settings"]
/// )
/// let results = try await client.memory.search(query: "UI preferences")
/// ```
public struct MemoryResource: Sendable {

    private let client: LiuHaoClient
    private let basePath = "/memory"

    init(client: LiuHaoClient) {
        self.client = client
    }

    // MARK: - Storage

    /// Stores a new memory entry.
    ///
    /// - Parameters:
    ///   - content: The memory content.
    ///   - category: Category classification. Defaults to ``MemoryCategory/general``.
    ///   - importance: Importance level. Defaults to ``MemoryImportance/medium``.
    ///   - tags: Tags for categorization and search.
    ///   - source: Origin of the memory (e.g. `"user_input"`).
    ///   - entityID: Optional associated entity identifier.
    ///   - metadata: Arbitrary caller-defined metadata.
    /// - Returns: The stored ``MemoryEntry``.
    public func store(
        content: String,
        category: MemoryCategory = .general,
        importance: MemoryImportance = .medium,
        tags: [String] = [],
        source: String = "",
        entityID: String? = nil,
        metadata: [String: JSONValue] = [:]
    ) async throws -> MemoryEntry {
        let body = StoreMemoryRequest(
            content: content,
            category: category,
            importance: importance,
            tags: tags,
            source: source,
            entityID: entityID,
            metadata: metadata
        )
        return try await client.post(basePath, body: body)
    }

    /// Retrieves a memory entry by identifier.
    ///
    /// - Parameter memoryID: Unique memory identifier.
    /// - Returns: The requested ``MemoryEntry``.
    /// - Throws: ``LiuHaoError/notFound(message:requestID:)`` when the
    ///   entry does not exist.
    public func get(memoryID: String) async throws -> MemoryEntry {
        try await client.get("\(basePath)/\(memoryID)")
    }

    /// Updates an existing memory entry.
    ///
    /// Only non-`nil` parameters are modified.
    ///
    /// - Parameters:
    ///   - memoryID: Identifier of the entry to update.
    ///   - content: New content.
    ///   - category: New category.
    ///   - importance: New importance level.
    ///   - tags: New tag list (replaces existing).
    ///   - metadata: New metadata (replaces existing).
    /// - Returns: The updated ``MemoryEntry``.
    public func update(
        memoryID: String,
        content: String? = nil,
        category: MemoryCategory? = nil,
        importance: MemoryImportance? = nil,
        tags: [String]? = nil,
        metadata: [String: JSONValue]? = nil
    ) async throws -> MemoryEntry {
        let body = UpdateMemoryRequest(
            content: content,
            category: category,
            importance: importance,
            tags: tags,
            metadata: metadata
        )
        return try await client.patch("\(basePath)/\(memoryID)", body: body)
    }

    /// Deletes a memory entry.
    ///
    /// - Parameter memoryID: Identifier of the entry to delete.
    /// - Returns: A ``DeleteResponse`` confirmation.
    public func delete(memoryID: String) async throws -> DeleteResponse {
        try await client.delete("\(basePath)/\(memoryID)")
    }

    // MARK: - Retrieval

    /// Lists memory entries with pagination and optional filters.
    ///
    /// - Parameters:
    ///   - limit: Maximum number of entries to return. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    ///   - category: Optional category filter.
    ///   - entityID: Optional entity filter.
    /// - Returns: A paginated list of ``MemoryEntry`` values.
    public func list(
        limit: Int = 20,
        offset: Int = 0,
        category: MemoryCategory? = nil,
        entityID: String? = nil
    ) async throws -> PaginatedResponse<MemoryEntry> {
        try await client.get(basePath, query: [
            "limit": String(limit),
            "offset": String(offset),
            "category": category?.rawValue,
            "entity_id": entityID
        ])
    }

    /// Performs a semantic search over the memory store.
    ///
    /// - Parameters:
    ///   - query: Natural-language search query.
    ///   - limit: Maximum number of results. Defaults to `10`.
    ///   - category: Optional category filter.
    ///   - minScore: Minimum relevance score (0–1). Defaults to `0`.
    /// - Returns: Ranked ``MemorySearchResult`` values.
    public func search(
        query: String,
        limit: Int = 10,
        category: MemoryCategory? = nil,
        minScore: Double = 0
    ) async throws -> [MemorySearchResult] {
        let body = SearchMemoryRequest(
            query: query,
            limit: limit,
            category: category,
            minScore: minScore
        )
        let response: SearchMemoryResponse = try await client.post(
            "\(basePath)/search",
            body: body
        )
        return response.results
    }

    /// Retrieves memories associated with a specific entity.
    ///
    /// - Parameters:
    ///   - entityID: The entity identifier.
    ///   - limit: Maximum number of entries. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    /// - Returns: A paginated list of ``MemoryEntry`` values.
    public func forEntity(
        entityID: String,
        limit: Int = 20,
        offset: Int = 0
    ) async throws -> PaginatedResponse<MemoryEntry> {
        try await list(limit: limit, offset: offset, entityID: entityID)
    }

    // MARK: - Maintenance

    /// Fetches aggregate statistics about the memory store.
    ///
    /// - Returns: ``MemoryStats`` for the current project.
    public func stats() async throws -> MemoryStats {
        try await client.get("\(basePath)/stats")
    }

    /// Deletes all memory entries matching the given filters.
    ///
    /// - Parameters:
    ///   - category: Optional category filter.
    ///   - entityID: Optional entity filter.
    /// - Returns: A ``DeleteResponse`` confirmation.
    public func purge(
        category: MemoryCategory? = nil,
        entityID: String? = nil
    ) async throws -> DeleteResponse {
        try await client.request(
            "POST",
            "\(basePath)/purge",
            body: PurgeMemoryRequest(category: category, entityID: entityID)
        )
    }
}

// MARK: - Request / response bodies

private struct StoreMemoryRequest: Encodable, Sendable {
    let content: String
    let category: MemoryCategory
    let importance: MemoryImportance
    let tags: [String]
    let source: String
    let entityID: String?
    let metadata: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case content, category, importance, tags, source, metadata
        case entityID = "entity_id"
    }
}

private struct UpdateMemoryRequest: Encodable, Sendable {
    let content: String?
    let category: MemoryCategory?
    let importance: MemoryImportance?
    let tags: [String]?
    let metadata: [String: JSONValue]?
}

private struct SearchMemoryRequest: Encodable, Sendable {
    let query: String
    let limit: Int
    let category: MemoryCategory?
    let minScore: Double

    enum CodingKeys: String, CodingKey {
        case query, limit, category
        case minScore = "min_score"
    }
}

private struct SearchMemoryResponse: Decodable, Sendable {
    let results: [MemorySearchResult]
}

private struct PurgeMemoryRequest: Encodable, Sendable {
    let category: MemoryCategory?
    let entityID: String?

    enum CodingKeys: String, CodingKey {
        case category
        case entityID = "entity_id"
    }
}
