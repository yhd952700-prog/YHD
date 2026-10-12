# @liuhao/sdk

> LiuHao AI OS JavaScript SDK - TypeScript client for the LiuHao AI Operating System API.

## Overview

The LiuHao AI OS SDK provides a fully typed, production-ready TypeScript client for interacting with the LiuHao AI Operating System. It includes comprehensive support for agents, memory, knowledge bases, tasks, and health monitoring.

## Features

- **Fully Typed**: Complete TypeScript type definitions for all API endpoints
- **Promise-based API**: All methods return native Promises with async/await support
- **Automatic Retries**: Built-in retry logic with exponential backoff and jitter
- **Error Handling**: Custom error classes for different failure types (authentication, validation, rate limiting, etc.)
- **Environment Support**: Configuration via environment variables or explicit options
- **Pagination**: First-class support for paginated API responses
- **Resource-oriented**: Clear resource-based API organization (agents, memory, knowledge, tasks, health)

## Installation

### npm

```bash
npm install @liuhao/sdk
```

### yarn

```bash
yarn add @liuhao/sdk
```

### pnpm

```bash
pnpm add @liuhao/sdk
```

## Quick Start

### Basic Usage

```typescript
import { LiuHaoClient } from '@liuhao/sdk';

// Create a client with explicit configuration
const client = new LiuHaoClient({
  baseUrl: 'https://api.liuhao.ai',
  apiKey: 'your-api-key-here',
});

// Or use environment variables:
// LIUHAO_API_KEY=your-api-key
// LIUHAO_BASE_URL=https://api.liuhao.ai
// const client = new LiuHaoClient();
```

### Working with Agents

```typescript
import { AgentStatus } from '@liuhao/sdk';

// List all agents
const agents = await client.agents.list({
  page: 1,
  pageSize: 10,
  status: AgentStatus.RUNNING,
});

console.log(`Found ${agents.total} agents`);
agents.items.forEach((agent) => {
  console.log(`- ${agent.name} (${agent.status})`);
});

// Create a new agent
const agent = await client.agents.create({
  name: 'Research Assistant',
  model: 'gpt-4',
  description: 'Helps with research tasks',
  capabilities: ['web-search', 'analysis'],
  config: { temperature: 0.7 },
});

// Execute an agent
const result = await client.agents.execute(agent.id, {
  input: 'What are the latest developments in AI?',
  maxTokens: 500,
});

console.log(`Response: ${result.output}`);
console.log(`Tokens used: ${result.usage?.totalTokens}`);

// Get agent metrics
const metrics = await client.agents.metrics(agent.id, 24);
console.log(`Total executions: ${metrics.totalExecutions}`);
```

### Working with Memory

```typescript
import { MemoryCategory } from '@liuhao/sdk';

// Store a memory
const memory = await client.memory.store({
  content: 'User prefers dark mode and blue accents',
  category: MemoryCategory.PREFERENCE,
  ownerId: 'user-123',
  importance: 0.8,
  tags: ['ui', 'theme', 'preference'],
});

// Search memories semantically
const results = await client.memory.search({
  query: 'What display settings does the user like?',
  ownerId: 'user-123',
  category: MemoryCategory.PREFERENCE,
  minSimilarity: 0.7,
  limit: 5,
});

results.items.forEach((item) => {
  console.log(`[${item.similarity?.toFixed(2)}] ${item.content}`);
});

// List all memories
const allMemories = await client.memory.list({
  ownerId: 'user-123',
  page: 1,
  pageSize: 20,
});
```

### Working with Knowledge Bases

```typescript
import { KnowledgeBaseType } from '@liuhao/sdk';

// Create a knowledge base
const base = await client.knowledge.createBase({
  name: 'Product Documentation',
  description: 'Internal product docs and guides',
  type: KnowledgeBaseType.HYBRID,
  embeddingModel: 'text-embedding-3-small',
});

// Upload a document
const doc = await client.knowledge.uploadDocument(base.id, {
  title: 'API Reference Guide',
  content: 'Full API documentation...',
  tags: ['api', 'reference'],
  chunk: true,
  chunkSize: 1000,
  chunkOverlap: 200,
});

// Search the knowledge base
const searchResults = await client.knowledge.search(base.id, {
  query: 'How do I authenticate with the API?',
  limit: 5,
  minScore: 0.7,
  rerank: true,
});

searchResults.items.forEach((result) => {
  console.log(`[${result.score.toFixed(2)}] ${result.documentTitle}`);
  console.log(result.text);
});
```

### Working with Tasks

```typescript
import { TaskStatus } from '@liuhao/sdk';

// Create an async task
const task = await client.tasks.create({
  name: 'Generate Monthly Report',
  type: 'report.generation',
  agentId: 'agent-123',
  input: { month: '2024-01', format: 'pdf' },
  priority: 5,
  maxRetries: 3,
});

// Poll for status
let status = await client.tasks.status(task.id);
while (status === TaskStatus.RUNNING || status === TaskStatus.PENDING) {
  await new Promise((resolve) => setTimeout(resolve, 1000));
  status = await client.tasks.status(task.id);
}

// Get results
if (status === TaskStatus.COMPLETED) {
  const results = await client.tasks.results(task.id);
  console.log('Task completed:', results);
}
```

### Health Checks

```typescript
import { HealthStatus } from '@liuhao/sdk';

// Basic health check
const health = await client.health.health();
console.log(`System status: ${health.status}`);

if (health.status === HealthStatus.HEALTHY) {
  console.log('All systems operational');
}

// Detailed health with components
const detailed = await client.health.detailed();
console.log(`Uptime: ${detailed.uptimeSeconds}s`);

Object.entries(detailed.components).forEach(([name, component]) => {
  console.log(`${name}: ${component.status} (${component.responseTimeMs}ms)`);
});

// Check specific components
const components = await client.health.components(['database', 'cache']);
console.log('Database status:', components.database);
```

## Error Handling

The SDK provides custom error classes for different failure scenarios:

```typescript
import {
  LiuHaoError,
  AuthenticationError,
  NotFoundError,
  ValidationError,
  RateLimitError,
  APIError,
  TimeoutError,
} from '@liuhao/sdk';

try {
  const agent = await client.agents.get('non-existent-id');
} catch (error) {
  if (error instanceof NotFoundError) {
    console.log('Agent not found:', error.message);
  } else if (error instanceof AuthenticationError) {
    console.log('API key is invalid');
  } else if (error instanceof RateLimitError) {
    console.log(`Rate limited, retry after ${error.retryAfterMs}ms`);
  } else if (error instanceof ValidationError) {
    console.log('Validation errors:', error.fieldErrors);
  } else if (error instanceof TimeoutError) {
    console.log('Request timed out');
  } else if (error instanceof APIError) {
    console.log('Server error:', error.statusCode);
  } else if (error instanceof LiuHaoError) {
    console.log('SDK error:', error.code, error.message);
  }
}
```

## Configuration

### Configuration Options

| Option           | Type      | Default                  | Description                              |
| ---------------- | --------- | ------------------------ | ---------------------------------------- |
| `baseUrl`        | `string`  | `http://localhost:8000`  | API base URL                             |
| `apiKey`         | `string`  | `undefined`              | API key for authentication               |
| `timeout`        | `number`  | `30000`                  | Request timeout in milliseconds          |
| `retries`        | `number`  | `3`                      | Number of retry attempts for failed requests |
| `userAgent`      | `string`  | `@liuhao/sdk/1.0.0`     | User-Agent header value                  |
| `validateSSL`    | `boolean` | `true`                   | Whether to validate SSL certificates     |
| `defaultHeaders` | `object`  | `undefined`              | Additional headers for all requests      |

### Environment Variables

The SDK automatically reads configuration from these environment variables:

- `LIUHAO_API_KEY` - API key for authentication
- `LIUHAO_BASE_URL` - API base URL
- `LIUHAO_TIMEOUT` - Request timeout in milliseconds
- `LIUHAO_RETRIES` - Number of retry attempts

### Configuration Priority

1. Explicit configuration passed to the constructor
2. Environment variables
3. Default values

## API Reference

### Resources

- **`client.agents`** - Agent management and execution
  - `list(params?)` - List agents with pagination
  - `get(id)` - Get agent by ID
  - `create(params)` - Create a new agent
  - `update(id, params)` - Update an agent
  - `delete(id)` - Delete an agent
  - `execute(id, params)` - Execute an agent
  - `getStatus(id)` - Get agent status
  - `cancel(id)` - Cancel a running agent
  - `metrics(id, timeRangeHours?)` - Get agent metrics
  - `listTasks(id, params?)` - List agent tasks

- **`client.memory`** - Memory storage and search
  - `store(params)` - Store a memory item
  - `retrieve(id)` - Get a memory item by ID
  - `update(id, params)` - Update a memory item
  - `delete(id)` - Delete a memory item
  - `search(params)` - Semantic search
  - `list(params?)` - List memory items
  - `clear(ownerId, category?)` - Clear memory
  - `categories(ownerId?)` - Get memory categories

- **`client.knowledge`** - Knowledge base management
  - `listBases(params?)` - List knowledge bases
  - `createBase(params)` - Create a knowledge base
  - `getBase(baseId)` - Get knowledge base details
  - `deleteBase(baseId)` - Delete a knowledge base
  - `uploadDocument(baseId, params)` - Upload a document
  - `listDocuments(baseId, params?)` - List documents
  - `deleteDocument(baseId, documentId)` - Delete a document
  - `search(baseId, params)` - Search knowledge base
  - `createCollection(params)` - Create a collection
  - `listCollections(params?)` - List collections

- **`client.tasks`** - Asynchronous task management
  - `create(params)` - Create a task
  - `get(id)` - Get task details
  - `list(params?)` - List tasks
  - `cancel(id)` - Cancel a task
  - `status(id)` - Get task status
  - `results(id)` - Get task results
  - `retry(id, params?)` - Retry a failed task

- **`client.health`** - Health monitoring
  - `health()` - Basic health check
  - `ready()` - Readiness probe
  - `liveness()` - Liveness probe
  - `detailed()` - Detailed health with components
  - `components(names?)` - Component health statuses

## Development

### Build

```bash
npm run build
```

### Test

```bash
npm test
```

### Lint

```bash
npm run lint
```

## License

MIT

## Support

For support, please open an issue in the [LiuHao AI OS repository](https://github.com/liuhao-ai/liuhao-os/issues).
