/**
 * Agents Resource
 *
 * Provides methods for managing and interacting with AI agents
 * in the LiuHao AI OS system.
 */

import type { LiuHaoClient } from '../client';
import type {
  Agent,
  AgentCreateParams,
  AgentExecuteParams,
  AgentExecuteResult,
  AgentListParams,
  AgentMetrics,
  AgentStatus,
  AgentUpdateParams,
  PaginatedResponse,
  Task,
} from '../types';

/**
 * Resource class for agent-related API operations.
 *
 * @example
 * ```ts
 * const client = new LiuHaoClient({ apiKey: '...' });
 *
 * // List all agents
 * const agents = await client.agents.list();
 *
 * // Create a new agent
 * const agent = await client.agents.create({
 *   name: 'My Agent',
 *   model: 'gpt-4',
 * });
 * ```
 */
export class AgentsResource {
  private readonly client: LiuHaoClient;

  /**
   * @param client - LiuHaoClient instance
   */
  constructor(client: LiuHaoClient) {
    this.client = client;
  }

  /**
   * List agents with pagination and filtering.
   *
   * @param params - List parameters (pagination, filters, sorting)
   * @returns Paginated list of agents
   *
   * @example
   * ```ts
   * const result = await client.agents.list({
   *   page: 1,
   *   pageSize: 10,
   *   status: AgentStatus.RUNNING,
   * });
   * console.log(result.items); // Array of Agent objects
   * ```
   */
  async list(params?: AgentListParams): Promise<PaginatedResponse<Agent>> {
    const queryParams = this.buildListParams(params);
    return this.client.get<PaginatedResponse<Agent>>('/api/v1/agents', queryParams);
  }

  /**
   * Get a single agent by ID.
   *
   * @param id - Agent ID
   * @returns Agent details
   * @throws {NotFoundError} If the agent does not exist
   *
   * @example
   * ```ts
   * const agent = await client.agents.get('agent-123');
   * console.log(agent.name);
   * ```
   */
  async get(id: string): Promise<Agent> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    return this.client.get<Agent>(`/api/v1/agents/${id}`);
  }

  /**
   * Create a new agent.
   *
   * @param params - Agent creation parameters
   * @returns The created agent
   * @throws {ValidationError} If parameters are invalid
   *
   * @example
   * ```ts
   * const agent = await client.agents.create({
   *   name: 'Research Assistant',
 *   model: 'gpt-4',
   *   description: 'Helps with research tasks',
   *   capabilities: ['web-search', 'analysis'],
   *   config: { temperature: 0.7 },
   * });
   * ```
   */
  async create(params: AgentCreateParams): Promise<Agent> {
    if (!params.name) {
      throw new Error('Agent name is required');
    }
    if (!params.model) {
      throw new Error('Agent model is required');
    }
    return this.client.post<Agent>('/api/v1/agents', params);
  }

  /**
   * Update an existing agent.
   *
   * @param id - Agent ID
   * @param params - Fields to update
   * @returns The updated agent
   * @throws {NotFoundError} If the agent does not exist
   *
   * @example
   * ```ts
   * const agent = await client.agents.update('agent-123', {
   *   name: 'Updated Name',
   *   description: 'New description',
   * });
   * ```
   */
  async update(id: string, params: AgentUpdateParams): Promise<Agent> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    return this.client.patch<Agent>(`/api/v1/agents/${id}`, params);
  }

  /**
   * Delete an agent.
   *
   * @param id - Agent ID
   * @throws {NotFoundError} If the agent does not exist
   *
   * @example
   * ```ts
   * await client.agents.delete('agent-123');
   * ```
   */
  async delete(id: string): Promise<void> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    await this.client.delete(`/api/v1/agents/${id}`);
  }

  /**
   * Execute an agent with the given input.
   *
   * Sends input to the agent and returns the execution result.
   *
   * @param id - Agent ID
   * @param params - Execution parameters
   * @returns Execution result with output and usage info
   * @throws {NotFoundError} If the agent does not exist
   *
   * @example
   * ```ts
   * const result = await client.agents.execute('agent-123', {
   *   input: 'What is the capital of France?',
   *   maxTokens: 100,
   * });
   * console.log(result.output);
   * console.log(result.usage);
   * ```
   */
  async execute(id: string, params: AgentExecuteParams): Promise<AgentExecuteResult> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    if (!params.input) {
      throw new Error('Input is required for agent execution');
    }
    return this.client.post<AgentExecuteResult>(
      `/api/v1/agents/${id}/execute`,
      params,
    );
  }

  /**
   * Get the current status of an agent.
   *
   * @param id - Agent ID
   * @returns Current agent status
   *
   * @example
   * ```ts
   * const status = await client.agents.getStatus('agent-123');
   * console.log(status); // 'running', 'idle', etc.
   * ```
   */
  async getStatus(id: string): Promise<AgentStatus> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    const response = await this.client.get<{ status: AgentStatus }>(
      `/api/v1/agents/${id}/status`,
    );
    return response.status;
  }

  /**
   * Cancel a running agent.
   *
   * @param id - Agent ID
   * @returns The cancelled agent
   * @throws {NotFoundError} If the agent does not exist
   *
   * @example
   * ```ts
   * const agent = await client.agents.cancel('agent-123');
   * console.log(agent.status); // 'cancelled'
   * ```
   */
  async cancel(id: string): Promise<Agent> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    return this.client.post<Agent>(`/api/v1/agents/${id}/cancel`);
  }

  /**
   * Get agent metrics/statistics.
   *
   * @param id - Agent ID
   * @param timeRange - Optional time range in hours (default: 24)
   * @returns Agent metrics including execution counts and timing
   *
   * @example
   * ```ts
   * const metrics = await client.agents.metrics('agent-123', 24);
   * console.log(metrics.totalExecutions);
   * console.log(metrics.averageExecutionTimeMs);
   * ```
   */
  async metrics(id: string, timeRangeHours?: number): Promise<AgentMetrics> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    const params: Record<string, unknown> = {};
    if (timeRangeHours !== undefined) {
      params.timeRangeHours = timeRangeHours;
    }
    return this.client.get<AgentMetrics>(
      `/api/v1/agents/${id}/metrics`,
      params,
    );
  }

  /**
   * List tasks associated with an agent.
   *
   * @param id - Agent ID
   * @param params - Pagination and filter parameters
   * @returns Paginated list of tasks
   *
   * @example
   * ```ts
   * const result = await client.agents.listTasks('agent-123', {
   *   page: 1,
   *   pageSize: 20,
   * });
   * console.log(result.items); // Array of Task objects
   * ```
   */
  async listTasks(
    id: string,
    params?: { page?: number; pageSize?: number; status?: string },
  ): Promise<PaginatedResponse<Task>> {
    if (!id) {
      throw new Error('Agent ID is required');
    }
    const queryParams: Record<string, unknown> = {};
    if (params?.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params?.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }
    if (params?.status !== undefined) {
      queryParams.status = params.status;
    }
    return this.client.get<PaginatedResponse<Task>>(
      `/api/v1/agents/${id}/tasks`,
      queryParams,
    );
  }

  // --------------------------------------------------------
  // Private helpers
  // --------------------------------------------------------

  /**
   * Build query parameters from list params.
   */
  private buildListParams(params?: AgentListParams): Record<string, unknown> {
    const queryParams: Record<string, unknown> = {};

    if (!params) {
      return queryParams;
    }

    if (params.page !== undefined) {
      queryParams.page = params.page;
    }
    if (params.pageSize !== undefined) {
      queryParams.pageSize = params.pageSize;
    }
    if (params.status !== undefined) {
      queryParams.status = params.status;
    }
    if (params.model !== undefined) {
      queryParams.model = params.model;
    }
    if (params.search !== undefined) {
      queryParams.search = params.search;
    }
    if (params.ownerId !== undefined) {
      queryParams.ownerId = params.ownerId;
    }
    if (params.sortBy !== undefined) {
      queryParams.sortBy = params.sortBy;
    }
    if (params.sortDir !== undefined) {
      queryParams.sortDir = params.sortDir;
    }

    return queryParams;
  }
}
