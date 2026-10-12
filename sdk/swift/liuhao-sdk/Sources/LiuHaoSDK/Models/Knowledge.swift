//
//  Knowledge.swift
//  LiuHaoSDK
//
//  Knowledge base domain models.
//

import Foundation

/// Lifecycle status of a knowledge base.
public enum KnowledgeBaseStatus: String, Codable, Sendable, CaseIterable {
    case creating
    case ready
    case indexing
    case error
    case deleted
}

/// Processing status of a document in a knowledge base.
public enum DocumentStatus: String, Codable, Sendable, CaseIterable {
    case uploaded
    case parsing
    case indexing
    case ready
    case failed
    case deleted
}

/// Supported document formats for knowledge base upload.
public enum DocumentFormat: String, Codable, Sendable, CaseIterable {
    case txt
    case pdf
    case docx
    case markdown
    case html
    case csv
    case json
}

/// Search mode for knowledge base queries.
public enum KnowledgeSearchMode: String, Codable, Sendable, CaseIterable {
    case semantic
    case keyword
    case hybrid
}

/// A knowledge base on the LiuHao AI OS platform.
public struct KnowledgeBase: Codable, Sendable, Identifiable, Equatable {
    /// Unique knowledge base identifier.
    public let id: String

    /// Human-readable name.
    public var name: String

    /// Brief description of the knowledge base content.
    public var description: String

    /// Current lifecycle status.
    public var status: KnowledgeBaseStatus

    /// Number of documents in the base.
    public let documentCount: Int

    /// Number of indexed text chunks.
    public let chunkCount: Int

    /// Model used for vector embeddings.
    public var embeddingModel: String

    /// ISO 8601 creation timestamp.
    public let createdAt: String?

    /// ISO 8601 last-update timestamp.
    public let updatedAt: String?

    /// Arbitrary caller-defined metadata.
    public var metadata: [String: JSONValue]

    public init(
        id: String,
        name: String,
        description: String = "",
        status: KnowledgeBaseStatus = .creating,
        documentCount: Int = 0,
        chunkCount: Int = 0,
        embeddingModel: String = "default",
        createdAt: String? = nil,
        updatedAt: String? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.id = id
        self.name = name
        self.description = description
        self.status = status
        self.documentCount = documentCount
        self.chunkCount = chunkCount
        self.embeddingModel = embeddingModel
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, name, description, status, metadata
        case documentCount = "document_count"
        case chunkCount = "chunk_count"
        case embeddingModel = "embedding_model"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// A document within a knowledge base.
public struct KnowledgeDocument: Codable, Sendable, Identifiable, Equatable {
    /// Unique document identifier.
    public let id: String

    /// Parent knowledge base identifier.
    public let knowledgeBaseID: String

    /// Original filename.
    public var filename: String

    /// Document format.
    public let format: DocumentFormat

    /// Processing status.
    public var status: DocumentStatus

    /// File size in bytes.
    public let sizeBytes: Int

    /// Number of chunks created from this document.
    public let chunkCount: Int

    /// Error details if processing failed.
    public let errorMessage: String?

    /// ISO 8601 upload timestamp.
    public let createdAt: String?

    /// ISO 8601 last-update timestamp.
    public let updatedAt: String?

    /// Arbitrary caller-defined metadata.
    public var metadata: [String: JSONValue]

    public init(
        id: String,
        knowledgeBaseID: String,
        filename: String,
        format: DocumentFormat = .txt,
        status: DocumentStatus = .uploaded,
        sizeBytes: Int = 0,
        chunkCount: Int = 0,
        errorMessage: String? = nil,
        createdAt: String? = nil,
        updatedAt: String? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.id = id
        self.knowledgeBaseID = knowledgeBaseID
        self.filename = filename
        self.format = format
        self.status = status
        self.sizeBytes = sizeBytes
        self.chunkCount = chunkCount
        self.errorMessage = errorMessage
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, filename, format, status, metadata
        case knowledgeBaseID = "knowledge_base_id"
        case sizeBytes = "size_bytes"
        case chunkCount = "chunk_count"
        case errorMessage = "error_message"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// A single knowledge base search result.
public struct KnowledgeSearchResult: Codable, Sendable, Equatable {
    /// Matched chunk content.
    public let content: String

    /// Identifier of the document the chunk belongs to.
    public let documentID: String

    /// Relevance score; higher is more relevant.
    public let score: Double

    /// Chunk index within the source document.
    public let chunkIndex: Int?

    /// Additional result metadata.
    public let metadata: [String: JSONValue]

    public init(
        content: String,
        documentID: String,
        score: Double,
        chunkIndex: Int? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.content = content
        self.documentID = documentID
        self.score = score
        self.chunkIndex = chunkIndex
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case content, score, metadata
        case documentID = "document_id"
        case chunkIndex = "chunk_index"
    }
}
