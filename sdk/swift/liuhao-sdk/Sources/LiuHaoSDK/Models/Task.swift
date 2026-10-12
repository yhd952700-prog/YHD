//
//  Task.swift
//  LiuHaoSDK
//
//  Task domain models.
//

import Foundation

/// Status of a platform task.
public enum TaskStatus: String, Codable, Sendable, CaseIterable {
    case pending
    case queued
    case running
    case completed
    case failed
    case cancelled
}

/// Priority of a platform task.
public enum TaskPriority: String, Codable, Sendable, CaseIterable {
    case low
    case normal
    case high
    case urgent
}

/// A unit of work tracked by the LiuHao AI OS task system.
public struct LiuHaoTask: Codable, Sendable, Identifiable, Equatable {
    /// Unique task identifier.
    public let id: String

    /// Human-readable task title.
    public var title: String

    /// Detailed task description.
    public var description: String

    /// Current task status.
    public var status: TaskStatus

    /// Scheduling priority.
    public var priority: TaskPriority

    /// Identifier of the agent assigned to the task, if any.
    public var agentID: String?

    /// Identifier of the run produced by the task, if any.
    public var runID: String?

    /// Task input payload.
    public var input: [String: JSONValue]

    /// Task output payload, available once completed.
    public var output: [String: JSONValue]?

    /// Error message if the task failed.
    public var error: String?

    /// ISO 8601 creation timestamp.
    public let createdAt: String?

    /// ISO 8601 last-update timestamp.
    public let updatedAt: String?

    /// ISO 8601 completion timestamp.
    public let completedAt: String?

    /// Arbitrary caller-defined metadata.
    public var metadata: [String: JSONValue]

    public init(
        id: String,
        title: String,
        description: String = "",
        status: TaskStatus = .pending,
        priority: TaskPriority = .normal,
        agentID: String? = nil,
        runID: String? = nil,
        input: [String: JSONValue] = [:],
        output: [String: JSONValue]? = nil,
        error: String? = nil,
        createdAt: String? = nil,
        updatedAt: String? = nil,
        completedAt: String? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.id = id
        self.title = title
        self.description = description
        self.status = status
        self.priority = priority
        self.agentID = agentID
        self.runID = runID
        self.input = input
        self.output = output
        self.error = error
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.completedAt = completedAt
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, title, description, status, priority, input, output, error, metadata
        case agentID = "agent_id"
        case runID = "run_id"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case completedAt = "completed_at"
    }

    /// Whether the task reached a terminal state.
    public var isTerminal: Bool {
        switch status {
        case .completed, .failed, .cancelled:
            return true
        case .pending, .queued, .running:
            return false
        }
    }
}
