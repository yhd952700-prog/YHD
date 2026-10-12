//
//  Agent.swift
//  LiuHaoSDK
//
//  Agent domain models.
//

import Foundation

/// Lifecycle status of an agent.
public enum AgentStatus: String, Codable, Sendable, CaseIterable {
    case active
    case inactive
    case deploying
    case failed
    case deleted
}

/// Status of a single agent run.
public enum AgentRunStatus: String, Codable, Sendable, CaseIterable {
    case pending
    case running
    case completed
    case failed
    case cancelled
    case timedOut = "timed_out"
}

/// An agent hosted on the LiuHao AI OS platform.
public struct Agent: Codable, Sendable, Identifiable, Equatable {
    /// Unique agent identifier.
    public let id: String

    /// Human-readable agent name.
    public var name: String

    /// Brief description of the agent's purpose.
    public var description: String

    /// The underlying AI model used by the agent.
    public var model: String

    /// Current lifecycle status.
    public var status: AgentStatus

    /// Capability identifiers enabled for the agent.
    public var capabilities: [String]

    /// System prompt template controlling agent behaviour.
    public var systemPrompt: String

    /// Tool names the agent may invoke.
    public var tools: [String]

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
        model: String = "default",
        status: AgentStatus = .inactive,
        capabilities: [String] = [],
        systemPrompt: String = "",
        tools: [String] = [],
        createdAt: String? = nil,
        updatedAt: String? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.id = id
        self.name = name
        self.description = description
        self.model = model
        self.status = status
        self.capabilities = capabilities
        self.systemPrompt = systemPrompt
        self.tools = tools
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, name, description, model, status, capabilities, tools, metadata
        case systemPrompt = "system_prompt"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// A single execution of an agent.
public struct AgentRun: Codable, Sendable, Identifiable, Equatable {
    /// Unique run identifier.
    public let id: String

    /// Identifier of the agent that executed this run.
    public let agentID: String

    /// Current run status.
    public var status: AgentRunStatus

    /// The input that triggered the run.
    public var input: String

    /// The generated output, if the run has completed.
    public var output: String?

    /// Error message if the run failed.
    public var error: String?

    /// ISO 8601 start timestamp.
    public let startedAt: String?

    /// ISO 8601 completion timestamp.
    public let completedAt: String?

    /// Total execution duration in milliseconds.
    public var durationMs: Int?

    /// Total tokens consumed.
    public var tokensUsed: Int?

    /// Additional run metadata.
    public var metadata: [String: JSONValue]

    public init(
        id: String,
        agentID: String,
        status: AgentRunStatus = .pending,
        input: String = "",
        output: String? = nil,
        error: String? = nil,
        startedAt: String? = nil,
        completedAt: String? = nil,
        durationMs: Int? = nil,
        tokensUsed: Int? = nil,
        metadata: [String: JSONValue] = [:]
    ) {
        self.id = id
        self.agentID = agentID
        self.status = status
        self.input = input
        self.output = output
        self.error = error
        self.startedAt = startedAt
        self.completedAt = completedAt
        self.durationMs = durationMs
        self.tokensUsed = tokensUsed
        self.metadata = metadata
    }

    enum CodingKeys: String, CodingKey {
        case id, status, input, output, error, metadata
        case agentID = "agent_id"
        case startedAt = "started_at"
        case completedAt = "completed_at"
        case durationMs = "duration_ms"
        case tokensUsed = "tokens_used"
    }

    /// Whether the run reached a terminal state.
    public var isTerminal: Bool {
        switch status {
        case .completed, .failed, .cancelled, .timedOut:
            return true
        case .pending, .running:
            return false
        }
    }
}

/// Usage and performance metrics for an agent.
public struct AgentMetrics: Codable, Sendable, Equatable {
    /// Identifier of the agent these metrics belong to.
    public let agentID: String

    /// Total number of executions.
    public let totalRuns: Int

    /// Number of successful executions.
    public let successfulRuns: Int

    /// Number of failed executions.
    public let failedRuns: Int

    /// Average execution duration in milliseconds.
    public let averageDurationMs: Double

    /// Total tokens consumed.
    public let totalTokens: Int

    /// ISO 8601 start of the metrics window.
    public let periodStart: String?

    /// ISO 8601 end of the metrics window.
    public let periodEnd: String?

    public init(
        agentID: String,
        totalRuns: Int = 0,
        successfulRuns: Int = 0,
        failedRuns: Int = 0,
        averageDurationMs: Double = 0,
        totalTokens: Int = 0,
        periodStart: String? = nil,
        periodEnd: String? = nil
    ) {
        self.agentID = agentID
        self.totalRuns = totalRuns
        self.successfulRuns = successfulRuns
        self.failedRuns = failedRuns
        self.averageDurationMs = averageDurationMs
        self.totalTokens = totalTokens
        self.periodStart = periodStart
        self.periodEnd = periodEnd
    }

    enum CodingKeys: String, CodingKey {
        case agentID = "agent_id"
        case totalRuns = "total_runs"
        case successfulRuns = "successful_runs"
        case failedRuns = "failed_runs"
        case averageDurationMs = "avg_duration_ms"
        case totalTokens = "total_tokens"
        case periodStart = "period_start"
        case periodEnd = "period_end"
    }

    /// Fraction of runs that succeeded, in `0...1`.
    public var successRate: Double {
        guard totalRuns > 0 else { return 0 }
        return Double(successfulRuns) / Double(totalRuns)
    }
}
