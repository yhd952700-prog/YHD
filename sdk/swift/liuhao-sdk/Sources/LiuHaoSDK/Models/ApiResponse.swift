//
//  ApiResponse.swift
//  LiuHaoSDK
//
//  Generic API envelope types.
//

import Foundation

/// A paginated list response from the LiuHao API.
///
/// List endpoints return their items inside this envelope together
/// with the total number of matching records.
public struct PaginatedResponse<Item: Codable & Sendable>: Codable, Sendable {
    /// The page of items.
    public let items: [Item]

    /// Total number of records matching the query.
    public let total: Int

    /// Maximum page size used for this response.
    public let limit: Int

    /// Offset used for this response.
    public let offset: Int

    public init(items: [Item], total: Int, limit: Int, offset: Int) {
        self.items = items
        self.total = total
        self.limit = limit
        self.offset = offset
    }

    /// Whether more pages are available after this one.
    public var hasMore: Bool {
        offset + items.count < total
    }
}

/// A simple acknowledgement returned by destructive endpoints.
public struct DeleteResponse: Codable, Sendable {
    /// Identifier of the deleted resource.
    public let id: String

    /// Whether the resource was deleted.
    public let deleted: Bool

    public init(id: String, deleted: Bool) {
        self.id = id
        self.deleted = deleted
    }
}

/// Health status of the LiuHao API.
public struct HealthStatus: Codable, Sendable {
    /// Overall status string, e.g. `"ok"`.
    public let status: String

    /// API version.
    public let version: String?

    /// Server uptime in seconds.
    public let uptimeSeconds: Double?

    public init(status: String, version: String?, uptimeSeconds: Double?) {
        self.status = status
        self.version = version
        self.uptimeSeconds = uptimeSeconds
    }

    enum CodingKeys: String, CodingKey {
        case status, version
        case uptimeSeconds = "uptime_seconds"
    }
}

/// Information about the LiuHao API deployment.
public struct APIInfo: Codable, Sendable {
    /// API version string.
    public let version: String

    /// Build identifier.
    public let build: String?

    /// Feature flags enabled on the server.
    public let features: [String]

    public init(version: String, build: String?, features: [String]) {
        self.version = version
        self.build = build
        self.features = features
    }
}

/// Decoded error body returned by the API.
struct APIErrorBody: Codable, Sendable {
    struct Detail: Codable, Sendable {
        let code: String?
        let message: String
        let details: [String: JSONValue]?
    }
    let error: Detail
}
