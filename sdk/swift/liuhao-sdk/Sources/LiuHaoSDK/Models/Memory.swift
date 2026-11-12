//
//  Memory.swift
//  LiuHaoSDK
//
//  Memory domain models.
//

import Foundation

/// Category classification for memory entries.
public enum MemoryCategory: String, Codable, Sendable, CaseIterable {
    case general
    case fact
    case preference
    case conversation
    case task
    case knowledge
    case userProfile = "user_profile"
    case context
}

/// Importance level of a memory entry.
///
/// Used for retrieval prioritization and memory pruning strategies.
public enum MemoryImportance: String, Codable, Sendable, CaseIterable {
    case low
    case medium
    case high
    case critical
}

/// A single entry in the LiuHao AI OS memory system.
public struct MemoryEntry: Codable, Sendable, Identifiable, Equatable {
    /// Unique memory identifier.
    public let id: String

    /// The memory content.
    public var content: String

    /// Category classification.
    public var category: MemoryCategory

    /// Importance level.
    public var importance: MemoryImportance

    /// Tags for categorization and search.
    public var tags: [String]

    /// Origin of the memory (e.g. `"user_input"`, `"agent_observation"`).
    public var source: String

    /// Optional associated entity identifier (user, agent, task, ...).
    public var entityID: String?

    /// Arbitrary caller-defined metadata.
    public var metadata: [String: JSONValue]

    /// ISO 8601 creation timestamp.
    public let createdAt: String?

    /// ISO 8601 last-update timestamp.
    public let updatedAt: String?

    /// Number of times this memory has been accessed.
    public var accessCount: Int

    /// ISO 8601 last access timestamp.
    public var lastAccessedAt: String?

    public init(
        id: String,
        content: String,
        category: MemoryCategory = .general,
        importance: MemoryImportance = .medium,
        tags: [String] = [],
        source: String = "",
        entityID: String? = nil,
        metadata: [String: JSONValue] = [:],
        createdAt: String? = nil,
        updatedAt: String? = nil,
        accessCount: Int = 0,
        lastAccessedAt: String? = nil
    ) {
        self.id = id
        self.content = content
        self.category = category
        self.importance = importance
        self.tags = tags
        self.source = source
        self.entityID = entityID
        self.metadata = metadata
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.accessCount = accessCount
        self.lastAccessedAt = lastAccessedAt
    }

    enum CodingKeys: String, CodingKey {
        case id, content, category, importance, tags, source, metadata
        case entityID = "entity_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case accessCount = "access_count"
        case lastAccessedAt = "last_accessed_at"
    }
}

/// How a memory search result matched the query.
public enum MemoryMatchType: String, Codable, Sendable {
    case semantic
    case keyword
    case hybrid
}

/// A single memory search result.
public struct MemorySearchResult: Codable, Sendable, Equatable {
    /// The matched memory entry.
    public let entry: MemoryEntry

    /// Relevance score; higher is more relevant.
    public let score: Double

    /// How the entry matched the query.
    public let matchType: MemoryMatchType

    public init(entry: MemoryEntry, score: Double, matchType: MemoryMatchType = .semantic) {
        self.entry = entry
        self.score = score
        self.matchType = matchType
    }

    enum CodingKeys: String, CodingKey {
        case entry, score
        case matchType = "match_type"
    }
}

/// Aggregate statistics about the memory store.
public struct MemoryStats: Codable, Sendable, Equatable {
    /// Total number of stored entries.
    public let totalEntries: Int

    /// Entry counts grouped by category.
    public let byCategory: [String: Int]

    /// Entry counts grouped by importance.
    public let byImportance: [String: Int]

    /// Approximate storage size in bytes.
    public let sizeBytes: Int?

    public init(
        totalEntries: Int,
        byCategory: [String: Int] = [:],
        byImportance: [String: Int] = [:],
        sizeBytes: Int? = nil
    ) {
        self.totalEntries = totalEntries
        self.byCategory = byCategory
        self.byImportance = byImportance
        self.sizeBytes = sizeBytes
    }

    enum CodingKeys: String, CodingKey {
        case totalEntries = "total_entries"
        case byCategory = "by_category"
        case byImportance = "by_importance"
        case sizeBytes = "size_bytes"
    }
}
