# LiuHao AI OS Go SDK

Go SDK for the LiuHao AI OS platform. Provides a clean, idiomatic Go interface for managing agents, memory, knowledge bases, tasks, and system health.

## Features

- **Agents Service**: Create, manage, and execute AI agents
- **Memory Service**: Store, retrieve, and semantically search agent memories
- **Knowledge Service**: Manage knowledge bases and document search
- **Tasks Service**: Create and monitor asynchronous tasks
- **Health Service**: Check system health and readiness
- Zero external dependencies (uses only Go standard library)
- Full `context.Context` support for cancellation and timeouts
- Typed errors with helper functions for error type checking
- Functional options pattern for client configuration
- Comprehensive test coverage with mock server tests

## Installation

```bash
go get github.com/liuhao-ai/liuhao-go-sdk
```

## Quick Start

```go
package main

import (
    "context"
    "fmt"
    "log"

    liuhao "github.com/liuhao-ai/liuhao-go-sdk"
)

func main() {
    // Create a new client
    client := liuhao.NewClient(
        liuhao.WithAPIKey("your-api-key"),
        liuhao.WithBaseURL("https://api.liuhao.ai/v1"),
    )

    // List agents
    agents, err := client.Agents.List(context.Background(), nil)
    if err != nil {
        log.Fatal(err)
    }

    for _, agent := range agents.Agents {
        fmt.Printf("Agent: %s (%s)\n", agent.Name, agent.ID)
    }
}
```

## Usage Examples

### Creating an Agent

```go
agent, err := client.Agents.Create(ctx, &liuhao.AgentCreateOptions{
    Name:         "Code Assistant",
    Type:         liuhao.AgentTypeCode,
    Model:        "gpt-4",
    SystemPrompt: "You are an expert Go programmer.",
    Tools:        []string{"code_interpreter", "web_search"},
    Metadata: map[string]string{
        "department": "engineering",
    },
})
if err != nil {
    if liuhao.IsValidationError(err) {
        fmt.Println("Invalid agent configuration")
    }
    log.Fatal(err)
}
fmt.Printf("Created agent: %s\n", agent.ID)
```

### Executing an Agent

```go
execution, err := client.Agents.Execute(ctx, "agent-id", &liuhao.AgentExecuteOptions{
    Input:     "Write a function to reverse a string in Go",
    MaxTokens: 500,
})
if err != nil {
    log.Fatal(err)
}
fmt.Printf("Execution ID: %s, Status: %s\n", execution.ID, execution.Status)
```

### Memory Management

```go
// Store a memory
memory, err := client.Memory.Store(ctx, &liuhao.MemoryStoreOptions{
    Content:    "User prefers dark mode and uses VS Code",
    Category:   liuhao.MemoryCategoryPreference,
    Tags:       []string{"ui", "tools"},
    AgentID:    "agent-id",
    Importance: 0.8,
})

// Search memories semantically
results, err := client.Memory.Search(ctx, &liuhao.MemorySearchOptions{
    Query:   "What are the user's UI preferences?",
    AgentID: "agent-id",
    Limit:   5,
})
for _, result := range results {
    fmt.Printf("[%.2f] %s\n", result.Score, result.Item.Content)
}
```

### Knowledge Bases

```go
// Create a knowledge base
kb, err := client.Knowledge.CreateBase(ctx, &liuhao.KnowledgeBaseCreateOptions{
    Name:        "Product Docs",
    Description: "Internal product documentation",
})

// Upload a document
doc, err := client.Knowledge.UploadDocument(ctx, kb.ID, &liuhao.DocumentUploadOptions{
    Title:   "API Reference",
    Content: "The REST API provides endpoints for...",
    Type:    "markdown",
    Tags:    []string{"api", "reference"},
})

// Search knowledge base
results, err := client.Knowledge.Search(ctx, &liuhao.KnowledgeSearchOptions{
    Query:            "How do I authenticate?",
    KnowledgeBaseIDs: []string{kb.ID},
    Limit:            5,
})
```

### Tasks

```go
// Create a task
task, err := client.Tasks.Create(ctx, &liuhao.TaskCreateOptions{
    Type:        "document.process",
    Description: "Process uploaded documents",
    Params: map[string]interface{}{
        "knowledge_base_id": "kb-id",
        "chunk_size":        1000,
    },
    MaxRetries: 3,
})

// Check task status
status, err := client.Tasks.Status(ctx, task.ID)
if status == liuhao.TaskStatusCompleted {
    result, _ := client.Tasks.Results(ctx, task.ID)
    fmt.Printf("Task result: %v\n", result)
}
```

### Health Checks

```go
// Basic health check
health, err := client.Health.Check(ctx)
fmt.Printf("Status: %s, Version: %s\n", health.Status, health.Version)

// Readiness check
ready, err := client.Health.Ready(ctx)
if ready {
    fmt.Println("System is ready")
}

// Detailed health with component status
detailed, err := client.Health.Detailed(ctx)
if detailed.Database != nil {
    fmt.Printf("DB status: %s, latency: %dms\n",
        detailed.Database.Status, detailed.Database.LatencyMs)
}
```

## Error Handling

The SDK provides typed errors for common API error conditions:

```go
agent, err := client.Agents.Get(ctx, "agent-id")
if err != nil {
    switch {
    case liuhao.IsNotFoundError(err):
        fmt.Println("Agent not found")
    case liuhao.IsAuthenticationError(err):
        fmt.Println("Invalid API key")
    case liuhao.IsRateLimitError(err):
        fmt.Println("Rate limit exceeded, please retry later")
    case liuhao.IsValidationError(err):
        fmt.Println("Invalid request parameters")
    case liuhao.IsTimeoutError(err):
        fmt.Println("Request timed out")
    default:
        fmt.Printf("API error: %v\n", err)
    }
    return
}
```

## Configuration Options

```go
client := liuhao.NewClient(
    liuhao.WithAPIKey("your-api-key"),
    liuhao.WithBaseURL("https://api.liuhao.ai/v1"),
    liuhao.WithTimeout(60*time.Second),
    // Or use a custom HTTP client:
    // liuhao.WithHTTPClient(&http.Client{...}),
)
```

## Pagination

List endpoints support pagination via `ListOptions`:

```go
resp, err := client.Agents.List(ctx, &liuhao.ListOptions{
    Page:     1,
    PageSize: 20,
    Sort:     "created_at",
    Order:    "desc",
    Query:    "assistant",
})

fmt.Printf("Page %d of %d, total: %d\n",
    resp.Page, resp.TotalPages, resp.Total)

if resp.HasNext {
    // Fetch next page
}
```

## Development

### Running Tests

```bash
cd sdk/go/liuhao
go test -v ./...
```

### Project Structure

```
sdk/go/liuhao/
├── go.mod              # Go module definition
├── liuhao.go           # Core client and request handling
├── types.go            # All struct types and enums
├── errors.go           # Error types and helper functions
├── agents.go           # Agents service
├── memory.go           # Memory service
├── knowledge.go        # Knowledge base service
├── tasks.go            # Tasks service
├── health.go           # Health check service
├── client_test.go      # Client tests
├── agents_test.go      # Agents service tests
├── memory_test.go      # Memory service tests
└── README.md           # This file
```

## License

MIT License
