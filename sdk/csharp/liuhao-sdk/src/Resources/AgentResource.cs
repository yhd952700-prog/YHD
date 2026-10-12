using LiuHao.Sdk.Models;

namespace LiuHao.Sdk.Resources;

/// <summary>
/// Agent management and execution resource.
/// </summary>
/// <remarks>
/// Provides CRUD operations for agents, task execution control,
/// and metrics retrieval on the LiuHao AI OS platform.
/// </remarks>
public sealed class AgentResource
{
    private const string BasePath = "/agents";

    private readonly LiuHaoClient _client;

    /// <summary>
    /// Initializes a new instance of the <see cref="AgentResource"/> class.
    /// </summary>
    /// <param name="client">The parent SDK client with request capabilities.</param>
    internal AgentResource(LiuHaoClient client)
    {
        _client = client ?? throw new ArgumentNullException(nameof(client));
    }

    // ------------------------------------------------------------------
    // Agent management (CRUD)
    // ------------------------------------------------------------------

    /// <summary>
    /// Lists agents with pagination.
    /// </summary>
    /// <param name="limit">Maximum number of agents to return.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="status">Optional filter by agent status.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of agents.</returns>
    public Task<PaginatedResponse<Agent>> ListAsync(
        int limit = 20,
        int offset = 0,
        AgentStatus? status = null,
        CancellationToken cancellationToken = default)
    {
        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
            ["status"] = status?.ToSnakeCase(),
        };

        return _client.GetAsync<PaginatedResponse<Agent>>(
            BasePath, query, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Retrieves a single agent by ID.
    /// </summary>
    /// <param name="agentId">Unique identifier of the agent.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The requested agent.</returns>
    /// <exception cref="NotFoundException">
    /// Thrown when no agent with the given ID exists.
    /// </exception>
    public Task<Agent> GetAsync(
        string agentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        return _client.GetAsync<Agent>(
            $"{BasePath}/{agentId}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Creates a new agent.
    /// </summary>
    /// <param name="options">Agent creation parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created agent.</returns>
    /// <exception cref="ValidationException">
    /// Thrown when required fields are missing or invalid.
    /// </exception>
    public Task<Agent> CreateAsync(
        AgentCreateOptions options,
        CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<Agent>(
            BasePath, options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Creates a new agent with the given name and optional settings.
    /// </summary>
    /// <param name="name">Human-readable agent name.</param>
    /// <param name="description">Brief description of the agent's purpose.</param>
    /// <param name="model">The AI model identifier to use.</param>
    /// <param name="systemPrompt">System prompt configuring behavior.</param>
    /// <param name="tools">List of tool names the agent may use.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created agent.</returns>
    public Task<Agent> CreateAsync(
        string name,
        string? description = null,
        string? model = null,
        string? systemPrompt = null,
        IReadOnlyList<string>? tools = null,
        CancellationToken cancellationToken = default)
    {
        return CreateAsync(
            new AgentCreateOptions
            {
                Name = name,
                Description = description,
                Model = model,
                SystemPrompt = systemPrompt,
                Tools = tools,
            },
            cancellationToken);
    }

    /// <summary>
    /// Updates an existing agent. Only set fields are updated.
    /// </summary>
    /// <param name="agentId">Unique identifier of the agent to update.</param>
    /// <param name="options">The fields to update.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The updated agent.</returns>
    public Task<Agent> UpdateAsync(
        string agentId,
        AgentUpdateOptions options,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        ArgumentNullException.ThrowIfNull(options);
        return _client.PatchAsync<Agent>(
            $"{BasePath}/{agentId}", options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Deletes an agent.
    /// </summary>
    /// <param name="agentId">Unique identifier of the agent to delete.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deletion confirmation.</returns>
    public Task<OperationResult> DeleteAsync(
        string agentId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        return _client.DeleteAsync<OperationResult>(
            $"{BasePath}/{agentId}", cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Agent execution
    // ------------------------------------------------------------------

    /// <summary>
    /// Executes a task on the agent.
    /// </summary>
    /// <param name="agentId">Identifier of the agent to run.</param>
    /// <param name="options">Execution parameters.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created run, including its ID and initial status.</returns>
    public Task<AgentRun> ExecuteAsync(
        string agentId,
        AgentExecuteOptions options,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        ArgumentNullException.ThrowIfNull(options);
        return _client.PostAsync<AgentRun>(
            $"{BasePath}/{agentId}/runs", options, cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Executes a task on the agent with the given input text.
    /// </summary>
    /// <param name="agentId">Identifier of the agent to run.</param>
    /// <param name="input">The input/query for the agent to process.</param>
    /// <param name="context">Optional additional context data.</param>
    /// <param name="stream">Whether to stream the response.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The created run, including its ID and initial status.</returns>
    public Task<AgentRun> ExecuteAsync(
        string agentId,
        string input,
        IReadOnlyDictionary<string, object?>? context = null,
        bool stream = false,
        CancellationToken cancellationToken = default)
    {
        return ExecuteAsync(
            agentId,
            new AgentExecuteOptions
            {
                Input = input,
                Context = context,
                Stream = stream,
            },
            cancellationToken);
    }

    /// <summary>
    /// Gets the status of an agent run.
    /// </summary>
    /// <param name="agentId">Identifier of the agent.</param>
    /// <param name="runId">Identifier of the run.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The run with its current status.</returns>
    public Task<AgentRun> GetRunAsync(
        string agentId,
        string runId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        Guard.NotNullOrEmpty(runId, nameof(runId));
        return _client.GetAsync<AgentRun>(
            $"{BasePath}/{agentId}/runs/{runId}", cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Cancels a running agent task.
    /// </summary>
    /// <param name="agentId">Identifier of the agent.</param>
    /// <param name="runId">Identifier of the run to cancel.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The cancellation confirmation.</returns>
    public Task<OperationResult> CancelRunAsync(
        string agentId,
        string runId,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));
        Guard.NotNullOrEmpty(runId, nameof(runId));
        return _client.PostAsync<OperationResult>(
            $"{BasePath}/{agentId}/runs/{runId}/cancel",
            cancellationToken: cancellationToken);
    }

    /// <summary>
    /// Lists execution runs for an agent.
    /// </summary>
    /// <param name="agentId">Identifier of the agent.</param>
    /// <param name="limit">Maximum number of runs to return.</param>
    /// <param name="offset">Pagination offset.</param>
    /// <param name="status">Optional filter by run status.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>A paginated list of runs.</returns>
    public Task<PaginatedResponse<AgentRun>> ListRunsAsync(
        string agentId,
        int limit = 20,
        int offset = 0,
        AgentRunStatus? status = null,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));

        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
            ["offset"] = offset.ToString(),
            ["status"] = status?.ToSnakeCase(),
        };

        return _client.GetAsync<PaginatedResponse<AgentRun>>(
            $"{BasePath}/{agentId}/runs", query, cancellationToken: cancellationToken);
    }

    // ------------------------------------------------------------------
    // Agent metrics
    // ------------------------------------------------------------------

    /// <summary>
    /// Gets usage and performance metrics for an agent.
    /// </summary>
    /// <param name="agentId">Identifier of the agent.</param>
    /// <param name="periodStart">Start of the metrics window.</param>
    /// <param name="periodEnd">End of the metrics window.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The agent's metrics for the requested period.</returns>
    public Task<AgentMetrics> GetMetricsAsync(
        string agentId,
        DateTimeOffset? periodStart = null,
        DateTimeOffset? periodEnd = null,
        CancellationToken cancellationToken = default)
    {
        Guard.NotNullOrEmpty(agentId, nameof(agentId));

        var query = new Dictionary<string, string?>
        {
            ["period_start"] = periodStart?.ToString("o"),
            ["period_end"] = periodEnd?.ToString("o"),
        };

        return _client.GetAsync<AgentMetrics>(
            $"{BasePath}/{agentId}/metrics", query, cancellationToken: cancellationToken);
    }
}
