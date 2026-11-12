/**
 * AgentsResource Unit Tests
 *
 * Tests for the AgentsResource class methods.
 */

import { AgentsResource } from '../src/resources/agents';
import { AgentStatus, MemoryCategory } from '../src/types';
import type { LiuHaoClient } from '../src/client';

// Mock the client
const mockGet = jest.fn();
const mockPost = jest.fn();
const mockPatch = jest.fn();
const mockDelete = jest.fn();

const mockClient = {
  get: mockGet,
  post: mockPost,
  patch: mockPatch,
  delete: mockDelete,
} as unknown as LiuHaoClient;

describe('AgentsResource', () => {
  let agents: AgentsResource;

  beforeEach(() => {
    jest.clearAllMocks();
    agents = new AgentsResource(mockClient);
  });

  // --------------------------------------------------------
  // list
  // --------------------------------------------------------

  describe('list', () => {
    const mockResponse = {
      items: [
        {
          id: 'agent-1',
          name: 'Test Agent',
          status: AgentStatus.RUNNING,
          model: 'gpt-4',
          capabilities: [],
          config: {},
          createdAt: '2024-01-01T00:00:00Z',
          updatedAt: '2024-01-01T00:00:00Z',
        },
      ],
      total: 1,
      page: 1,
      pageSize: 10,
      totalPages: 1,
      hasNext: false,
      hasPrev: false,
    };

    it('should list agents with default params', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await agents.list();

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/agents', {});
    });

    it('should list agents with pagination params', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await agents.list({ page: 2, pageSize: 20 });

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents',
        expect.objectContaining({
          page: 2,
          pageSize: 20,
        }),
      );
    });

    it('should list agents with filters', async () => {
      mockGet.mockResolvedValue(mockResponse);

      const result = await agents.list({
        status: AgentStatus.RUNNING,
        model: 'gpt-4',
        search: 'test',
        ownerId: 'owner-1',
      });

      expect(result).toEqual(mockResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents',
        expect.objectContaining({
          status: AgentStatus.RUNNING,
          model: 'gpt-4',
          search: 'test',
          ownerId: 'owner-1',
        }),
      );
    });
  });

  // --------------------------------------------------------
  // get
  // --------------------------------------------------------

  describe('get', () => {
    const mockAgent = {
      id: 'agent-1',
      name: 'Test Agent',
      description: 'A test agent',
      status: AgentStatus.IDLE,
      model: 'gpt-4',
      capabilities: ['text-generation'],
      config: { temperature: 0.7 },
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should get an agent by ID', async () => {
      mockGet.mockResolvedValue(mockAgent);

      const result = await agents.get('agent-1');

      expect(result).toEqual(mockAgent);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/agents/agent-1');
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.get('')).rejects.toThrow('Agent ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // create
  // --------------------------------------------------------

  describe('create', () => {
    const createParams = {
      name: 'New Agent',
      model: 'gpt-4',
      description: 'A new agent',
      capabilities: ['text-generation'],
      config: { temperature: 0.7 },
    };

    const mockCreatedAgent = {
      id: 'agent-new',
      ...createParams,
      status: AgentStatus.CREATED,
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-01T00:00:00Z',
    };

    it('should create an agent', async () => {
      mockPost.mockResolvedValue(mockCreatedAgent);

      const result = await agents.create(createParams);

      expect(result).toEqual(mockCreatedAgent);
      expect(mockPost).toHaveBeenCalledWith('/api/v1/agents', createParams);
    });

    it('should throw if name is missing', async () => {
      await expect(
        agents.create({ name: '', model: 'gpt-4' }),
      ).rejects.toThrow('Agent name is required');
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if model is missing', async () => {
      await expect(
        agents.create({ name: 'Test', model: '' }),
      ).rejects.toThrow('Agent model is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // update
  // --------------------------------------------------------

  describe('update', () => {
    const updateParams = {
      name: 'Updated Agent',
      description: 'Updated description',
    };

    const mockUpdatedAgent = {
      id: 'agent-1',
      name: 'Updated Agent',
      description: 'Updated description',
      status: AgentStatus.IDLE,
      model: 'gpt-4',
      capabilities: [],
      config: {},
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-02T00:00:00Z',
    };

    it('should update an agent', async () => {
      mockPatch.mockResolvedValue(mockUpdatedAgent);

      const result = await agents.update('agent-1', updateParams);

      expect(result).toEqual(mockUpdatedAgent);
      expect(mockPatch).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1',
        updateParams,
      );
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.update('', updateParams)).rejects.toThrow(
        'Agent ID is required',
      );
      expect(mockPatch).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // delete
  // --------------------------------------------------------

  describe('delete', () => {
    it('should delete an agent', async () => {
      mockDelete.mockResolvedValue(undefined);

      await agents.delete('agent-1');

      expect(mockDelete).toHaveBeenCalledWith('/api/v1/agents/agent-1');
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.delete('')).rejects.toThrow('Agent ID is required');
      expect(mockDelete).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // execute
  // --------------------------------------------------------

  describe('execute', () => {
    const executeParams = {
      input: 'Hello, world!',
      maxTokens: 100,
      temperature: 0.7,
    };

    const mockResult = {
      executionId: 'exec-1',
      output: 'Hello! How can I help you?',
      status: AgentStatus.COMPLETED,
      usage: {
        promptTokens: 10,
        completionTokens: 20,
        totalTokens: 30,
      },
    };

    it('should execute an agent', async () => {
      mockPost.mockResolvedValue(mockResult);

      const result = await agents.execute('agent-1', executeParams);

      expect(result).toEqual(mockResult);
      expect(mockPost).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1/execute',
        executeParams,
      );
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.execute('', executeParams)).rejects.toThrow(
        'Agent ID is required',
      );
      expect(mockPost).not.toHaveBeenCalled();
    });

    it('should throw if input is missing', async () => {
      await expect(
        agents.execute('agent-1', { input: '' }),
      ).rejects.toThrow('Input is required for agent execution');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // getStatus
  // --------------------------------------------------------

  describe('getStatus', () => {
    it('should get agent status', async () => {
      mockGet.mockResolvedValue({ status: AgentStatus.RUNNING });

      const status = await agents.getStatus('agent-1');

      expect(status).toBe(AgentStatus.RUNNING);
      expect(mockGet).toHaveBeenCalledWith('/api/v1/agents/agent-1/status');
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.getStatus('')).rejects.toThrow('Agent ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // cancel
  // --------------------------------------------------------

  describe('cancel', () => {
    const mockCancelledAgent = {
      id: 'agent-1',
      name: 'Test Agent',
      status: AgentStatus.CANCELLED,
      model: 'gpt-4',
      capabilities: [],
      config: {},
      createdAt: '2024-01-01T00:00:00Z',
      updatedAt: '2024-01-02T00:00:00Z',
    };

    it('should cancel an agent', async () => {
      mockPost.mockResolvedValue(mockCancelledAgent);

      const result = await agents.cancel('agent-1');

      expect(result).toEqual(mockCancelledAgent);
      expect(mockPost).toHaveBeenCalledWith('/api/v1/agents/agent-1/cancel');
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.cancel('')).rejects.toThrow('Agent ID is required');
      expect(mockPost).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // metrics
  // --------------------------------------------------------

  describe('metrics', () => {
    const mockMetrics = {
      totalExecutions: 100,
      successfulExecutions: 95,
      failedExecutions: 5,
      averageExecutionTimeMs: 1500,
      totalTokensConsumed: 50000,
      activeTasks: 3,
      timeWindow: {
        start: '2024-01-01T00:00:00Z',
        end: '2024-01-02T00:00:00Z',
      },
    };

    it('should get agent metrics', async () => {
      mockGet.mockResolvedValue(mockMetrics);

      const result = await agents.metrics('agent-1');

      expect(result).toEqual(mockMetrics);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1/metrics',
        {},
      );
    });

    it('should get agent metrics with time range', async () => {
      mockGet.mockResolvedValue(mockMetrics);

      const result = await agents.metrics('agent-1', 48);

      expect(result).toEqual(mockMetrics);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1/metrics',
        { timeRangeHours: 48 },
      );
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.metrics('')).rejects.toThrow('Agent ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  // --------------------------------------------------------
  // listTasks
  // --------------------------------------------------------

  describe('listTasks', () => {
    const mockTasksResponse = {
      items: [],
      total: 0,
      page: 1,
      pageSize: 10,
      totalPages: 0,
      hasNext: false,
      hasPrev: false,
    };

    it('should list agent tasks', async () => {
      mockGet.mockResolvedValue(mockTasksResponse);

      const result = await agents.listTasks('agent-1');

      expect(result).toEqual(mockTasksResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1/tasks',
        {},
      );
    });

    it('should list agent tasks with params', async () => {
      mockGet.mockResolvedValue(mockTasksResponse);

      const result = await agents.listTasks('agent-1', {
        page: 2,
        pageSize: 20,
        status: 'running',
      });

      expect(result).toEqual(mockTasksResponse);
      expect(mockGet).toHaveBeenCalledWith(
        '/api/v1/agents/agent-1/tasks',
        expect.objectContaining({
          page: 2,
          pageSize: 20,
          status: 'running',
        }),
      );
    });

    it('should throw if ID is empty', async () => {
      await expect(agents.listTasks('')).rejects.toThrow('Agent ID is required');
      expect(mockGet).not.toHaveBeenCalled();
    });
  });
});
