//
//  AgentResource.swift
//  LiuHaoSDK
//
//  Agent management and execution resource.
//

import Foundation

/// Agent management and execution resource.
///
/// Accessed through ``LiuHaoClient/agents``. Provides CRUD operations
/// for agents, execution control, run inspection, and metrics.
///
/// ```swift
/// let agent = try await client.agents.create(
///     name: "Research Assistant",
///     model: "liuhao-large",
///     systemPrompt: "You are a helpful research assistant."
/// )
/// let run = try await client.agents.run(agentID: agent.id, input: "Summarize ...")
/// ```
public struct AgentResource: Sendable {

    private let client: LiuHaoClient
    private let basePath = "/agents"

    init(client: LiuHaoClient) {
        self.client = client
    }

    // MARK: - CRUD

    /// Lists agents with pagination.
    ///
    /// - Parameters:
    ///   - limit: Maximum number of agents to return. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    ///   - status: Optional status filter.
    /// - Returns: A paginated list of ``Agent`` values.
    public func list(
        limit: Int = 20,
        offset: Int = 0,
        status: AgentStatus? = nil
    ) async throws -> PaginatedResponse<Agent> {
        try await client.get(basePath, query: [
            "limit": String(limit),
            "offset": String(offset),
            "status": status?.rawValue
        ])
    }

    /// Retrieves a single agent by identifier.
    ///
    /// - Parameter agentID: Unique agent identifier.
    /// - Returns: The requested ``Agent``.
    /// - Throws: ``LiuHaoError/notFound(message:requestID:)`` when the
    ///   agent does not exist.
    public func get(agentID: String) async throws -> Agent {
        try await client.get("\(basePath)/\(agentID)")
    }

    /// Creates a new agent.
    ///
    /// - Parameters:
    ///   - name: Human-readable agent name.
    ///   - description: Brief description of the agent's purpose.
    ///   - model: AI model identifier. Defaults to `"default"`.
    ///   - systemPrompt: System prompt controlling agent behaviour.
    ///   - tools: Tool names the agent may invoke.
    ///   - capabilities: Capability identifiers to enable.
    ///   - metadata: Arbitrary caller-defined metadata.
    /// - Returns: The created ``Agent``.
    public func create(
        name: String,
        description: String = "",
        model: String = "default",
        systemPrompt: String = "",
        tools: [String] = [],
        capabilities: [String] = [],
        metadata: [String: JSONValue] = [:]
    ) async throws -> Agent {
        let body = CreateAgentRequest(
            name: name,
            description: description,
            model: model,
            systemPrompt: systemPrompt,
            tools: tools,
            capabilities: capabilities,
            metadata: metadata
        )
        return try await client.post(basePath, body: body)
    }

    /// Updates an existing agent.
    ///
    /// Only non-`nil` parameters are modified.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent to update.
    ///   - name: New agent name.
    ///   - description: New description.
    ///   - model: New model identifier.
    ///   - systemPrompt: New system prompt.
    ///   - tools: New tool list (replaces existing).
    ///   - metadata: New metadata (replaces existing).
    /// - Returns: The updated ``Agent``.
    public func update(
        agentID: String,
        name: String? = nil,
        description: String? = nil,
        model: String? = nil,
        systemPrompt: String? = nil,
        tools: [String]? = nil,
        metadata: [String: JSONValue]? = nil
    ) async throws -> Agent {
        let body = UpdateAgentRequest(
            name: name,
            description: description,
            model: model,
            systemPrompt: systemPrompt,
            tools: tools,
            metadata: metadata
        )
        return try await client.patch("\(basePath)/\(agentID)", body: body)
    }

    /// Deletes an agent.
    ///
    /// - Parameter agentID: Identifier of the agent to delete.
    /// - Returns: A ``DeleteResponse`` confirmation.
    public func delete(agentID: String) async throws -> DeleteResponse {
        try await client.delete("\(basePath)/\(agentID)")
    }

    // MARK: - Execution

    /// Executes a task on the agent.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent to run.
    ///   - input: The input/query for the agent to process.
    ///   - context: Optional additional context data.
    ///   - stream: Whether to request a streamed response.
    /// - Returns: The created ``AgentRun``.
    public func run(
        agentID: String,
        input: String,
        context: [String: JSONValue]? = nil,
        stream: Bool = false
    ) async throws -> AgentRun {
        let body = RunAgentRequest(input: input, stream: stream, context: context)
        return try await client.post("\(basePath)/\(agentID)/runs", body: body)
    }

    /// Fetches the status of an agent run.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent.
    ///   - runID: Identifier of the run.
    /// - Returns: The current ``AgentRun`` state.
    public func runStatus(agentID: String, runID: String) async throws -> AgentRun {
        try await client.get("\(basePath)/\(agentID)/runs/\(runID)")
    }

    /// Cancels a running agent task.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent.
    ///   - runID: Identifier of the run to cancel.
    /// - Returns: The updated ``AgentRun``.
    public func cancelRun(agentID: String, runID: String) async throws -> AgentRun {
        try await client.post(
            "\(basePath)/\(agentID)/runs/\(runID)/cancel",
            body: EmptyBody()
        )
    }

    /// Lists execution runs for an agent.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent.
    ///   - limit: Maximum number of runs to return. Defaults to `20`.
    ///   - offset: Pagination offset. Defaults to `0`.
    ///   - status: Optional run status filter.
    /// - Returns: A paginated list of ``AgentRun`` values.
    public func listRuns(
        agentID: String,
        limit: Int = 20,
        offset: Int = 0,
        status: AgentRunStatus? = nil
    ) async throws -> PaginatedResponse<AgentRun> {
        try await client.get("\(basePath)/\(agentID)/runs", query: [
            "limit": String(limit),
            "offset": String(offset),
            "status": status?.rawValue
        ])
    }

    /// Waits for a run to reach a terminal state, polling at a fixed
    /// interval.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent.
    ///   - runID: Identifier of the run.
    ///   - pollInterval: Seconds between status polls. Defaults to `1`.
    ///   - timeout: Maximum wait time in seconds. Defaults to `120`.
    /// - Returns: The final ``AgentRun``.
    /// - Throws: ``LiuHaoError/timeout`` when the timeout elapses.
    public func waitForRun(
        agentID: String,
        runID: String,
        pollInterval: TimeInterval = 1,
        timeout: TimeInterval = 120
    ) async throws -> AgentRun {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            let run = try await runStatus(agentID: agentID, runID: runID)
            if run.isTerminal {
                return run
            }
            if Date() >= deadline {
                throw LiuHaoError.timeout
            }
            try await Task.sleep(nanoseconds: UInt64(pollInterval * 1_000_000_000))
        }
    }

    // MARK: - Metrics

    /// Fetches usage and performance metrics for an agent.
    ///
    /// - Parameters:
    ///   - agentID: Identifier of the agent.
    ///   - periodStart: ISO 8601 start of the metrics window.
    ///   - periodEnd: ISO 8601 end of the metrics window.
    /// - Returns: The ``AgentMetrics`` for the requested window.
    public func metrics(
        agentID: String,
        periodStart: String? = nil,
        periodEnd: String? = nil
    ) async throws -> AgentMetrics {
        try await client.get("\(basePath)/\(agentID)/metrics", query: [
            "period_start": periodStart,
            "period_end": periodEnd
        ])
    }
}

// MARK: - Request bodies

private struct CreateAgentRequest: Encodable, Sendable {
    let name: String
    let description: String
    let model: String
    let systemPrompt: String
    let tools: [String]
    let capabilities: [String]
    let metadata: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case name, description, model, tools, capabilities, metadata
        case systemPrompt = "system_prompt"
    }
}

private struct UpdateAgentRequest: Encodable, Sendable {
    let name: String?
    let description: String?
    let model: String?
    let systemPrompt: String?
    let tools: [String]?
    let metadata: [String: JSONValue]?

    enum CodingKeys: String, CodingKey {
        case name, description, model, tools, metadata
        case systemPrompt = "system_prompt"
    }
}

private struct RunAgentRequest: Encodable, Sendable {
    let input: String
    let stream: Bool
    let context: [String: JSONValue]?
}

struct EmptyBody: Encodable, Sendable {}
