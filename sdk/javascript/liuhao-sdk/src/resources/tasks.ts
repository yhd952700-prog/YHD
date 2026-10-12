/**
 * Tasks Resource
 *
 * Provides methods for managing async tasks in the
 * LiuHao AI OS system.
 */

import type { LiuHaoClient } from '../client';
import type {
  PaginatedResponse,
  Task,
  TaskCreateParams,
  TaskListParams,
  TaskRetryParams,
  TaskStatus,
} from '../types';

/**
 * Resource class for task-related API operations.
 *
 * Tasks represent asynchronous operations that are executed
 * by agents or background workers. Use this resource to
 * create, monitor, and manage tasks.
 *
 * @example
 * ```ts
 * const client = new LiuHaoClient({ apiKey: '...' });
 *
 * // Create a task
 * const task = await client.tasks.create({
 *   name: 'Process Report',
 *   type: 'report.generation',
 *   input: { format: 'pdf', data: {...} },
 * });
 *
 * // Check status
 * const status = await client.tasks.status(task.id);
 * ```
 */
export class TasksResource {
  private readonly client: LiuHaoClient;

  /**
   * @param client - LiuHaoClient instance
   */
  constructor(client: LiuHaoClient) {
    this.client = client;
  }

  /**
   * Create a new task.
   *
   * @param params - Task creation parameters
   * @returns The created task
   * @throws {ValidationError} If parameters are invalid
   *
   * @example
   * ```ts
   * const task = await client.tasks.create({
   *   name: 'Generate Monthly Report',
   *   type: 'report.generation',
   *   agentId: 'agent-123',
   *   input: {
   *     month: '2024-01',
   *     format: 'pdf',
   *   },
   *   priority: 5,
   *   maxRetries: 3,
   * });
   * ```
   */
  async create(params: TaskCreateParams): Promise<Task> {
    if (!params.name) {
      throw new Error('Task name is required');
    }
    if (!params.type) {
      throw new Error('Task type is required');
    }
    if (!params.input) {
      throw new Error('Task input is required');
    }
    return this.client.post<Task>('/api/v1/tasks', params);
  }

  /**
   * Get a task by ID.
   *
   * @param id - Task ID
   * @returns Task details
   * @throws {NotFoundError} If the task does not exist
   *
   * @example
   * ```ts
   * const task = await client.tasks.get('task-123');
   * console.log(task.status);
   * console.log(task.progress);
   * ```
   */
  async get(id: string): Promise<Task> {
    if (!id) {
      throw new Error('Task ID is required');
    }
    return this.client.get<Task>(`/api/v1/tasks/${id}`);
  }

  /**
   * List tasks with pagination and filtering.
   *
   * @param params - List parameters (pagination, filters, sorting)
   * @returns Paginated list of tasks
   *
   * @example
   * ```ts
   * const result = await client.tasks.list({
   *   page: 1,
   *   pageSize: 20,
   *   status: TaskStatus.RUNNING,
   *   agentId: 'agent-123',
   * });
   * ```
   */
  async list(params?: TaskListParams): Promise<PaginatedResponse<Task>> {
    const queryParams = this.buildListParams(params);
    return this.client.get<PaginatedResponse<Task>>('/api/v1/tasks', queryParams);
  }

  /**
   * Cancel a task.
   *
   * Attempts to cancel a running or pending task.
   *
   * @param id - Task ID
   * @returns The cancelled task
   * @throws {NotFoundError} If the task does not exist
   *
   * @example
   * ```ts
   * const task = await client.tasks.cancel('task-123');
   * console.log(task.status); // 'cancelled'
   * ```
   */
  async cancel(id: string): Promise<Task> {
    if (!id) {
      throw new Error('Task ID is required');
    }
    return this.client.post<Task>(`/api/v1/tasks/${id}/cancel`);
  }

  /**
   * Get the current status of a task.
   *
   * @param id - Task ID
   * @returns Current task status
   *
   * @example
   * ```ts
   * const status = await client.tasks.status('task-123');
   * if (status === TaskStatus.COMPLETED) {
   *   const result = await client.tasks.results('task-123');
   * }
   * ```
   */
  async status(id: string): Promise<TaskStatus> {
    if (!id) {
      throw new Error('Task ID is required');
    }
    const response = await this.client.get<{ status: TaskStatus }>(
      `/api/v1/tasks/${id}/status`,
    );
    return response.status;
  }

  /**
   * Get the results of a completed task.
   *
   * @param id - Task ID
   * @returns Task output/results
   * @throws {NotFoundError} If the task does not exist
   *
   * @example
   * ```ts
   * const results = await client.tasks.results('task-123');
   * console.log(results);
   * ```
   */
  async results(id: string): Promise<Record<string, unknown>> {
    if (!id) {
      throw new Error('Task ID is required');
    }
    const response = await this.client.get<{ output: Record<string, unknown> }>(
      `/api/v1/tasks/${id}/results`,
    );
    return response.output;
  }

  /**
   * Retry a failed task.
   *
   * Re-queues a failed task for execution, optionally with
   * updated input parameters.
   *
   * @param id - Task ID
   * @param params - Retry parameters
   * @returns The retried task
   * @throws {NotFoundError} If the task does not exist
   * @throws {ValidationError} If the task is not in a retryable state
   *
   * @example
   * ```ts
   * const task = await client.tasks.retry('task-123', {
   *   input: { ...updatedInput },
   *   additionalRetries: 2,
   * });
   * ```
   */
  async retry(id: string, params?: TaskRetryParams): Promise<Task> {
    if (!id) {
      throw new Error('Task ID is required');
    }
    return this.client.post<Task>(`/api/v1/tasks/${id}/retry`, params);
  }

  // --------------------------------------------------------
  // Private helpers
  // --------------------------------------------------------

  /**
   * Build query parameters from task list params.
   */
  private buildListParams(params?: TaskListParams): Record<string, unknown> {
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
    if (params.type !== undefined) {
      queryParams.type = params.type;
    }
    if (params.agentId !== undefined) {
      queryParams.agentId = params.agentId;
    }
    if (params.priority !== undefined) {
      queryParams.priority = params.priority;
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
