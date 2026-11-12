package liuhao

import "time"

// AgentStatus represents the current status of an agent.
type AgentStatus int

const (
	// AgentStatusUnknown is the default zero value.
	AgentStatusUnknown AgentStatus = iota
	// AgentStatusIdle indicates the agent is idle and ready for tasks.
	AgentStatusIdle
	// AgentStatusRunning indicates the agent is currently executing a task.
	AgentStatusRunning
	// AgentStatusPaused indicates the agent execution is paused.
	AgentStatusPaused
	// AgentStatusCompleted indicates the agent has completed its task successfully.
	AgentStatusCompleted
	// AgentStatusFailed indicates the agent has failed its task.
	AgentStatusFailed
	// AgentStatusCancelled indicates the agent execution was cancelled.
	AgentStatusCancelled
	// AgentStatusStarting indicates the agent is in the process of starting up.
	AgentStatusStarting
	// AgentStatusStopping indicates the agent is in the process of stopping.
	AgentStatusStopping
)

// String returns the string representation of the agent status.
func (s AgentStatus) String() string {
	switch s {
	case AgentStatusIdle:
		return "idle"
	case AgentStatusRunning:
		return "running"
	case AgentStatusPaused:
		return "paused"
	case AgentStatusCompleted:
		return "completed"
	case AgentStatusFailed:
		return "failed"
	case AgentStatusCancelled:
		return "cancelled"
	case AgentStatusStarting:
		return "starting"
	case AgentStatusStopping:
		return "stopping"
	default:
		return "unknown"
	}
}

// MarshalJSON implements the json.Marshaler interface.
func (s AgentStatus) MarshalJSON() ([]byte, error) {
	return []byte(`"` + s.String() + `"`), nil
}

// UnmarshalJSON implements the json.Unmarshaler interface.
func (s *AgentStatus) UnmarshalJSON(data []byte) error {
	str := string(data)
	if len(str) >= 2 && str[0] == '"' && str[len(str)-1] == '"' {
		str = str[1 : len(str)-1]
	}
	switch str {
	case "idle":
		*s = AgentStatusIdle
	case "running":
		*s = AgentStatusRunning
	case "paused":
		*s = AgentStatusPaused
	case "completed":
		*s = AgentStatusCompleted
	case "failed":
		*s = AgentStatusFailed
	case "cancelled":
		*s = AgentStatusCancelled
	case "starting":
		*s = AgentStatusStarting
	case "stopping":
		*s = AgentStatusStopping
	default:
		*s = AgentStatusUnknown
	}
	return nil
}

// AgentType represents the type of an agent.
type AgentType int

const (
	// AgentTypeUnknown is the default zero value.
	AgentTypeUnknown AgentType = iota
	// AgentTypeChat is a conversational chat agent.
	AgentTypeChat
	// AgentTypeWorkflow is an agent that executes predefined workflows.
	AgentTypeWorkflow
	// AgentTypeResearch is a research-focused agent.
	AgentTypeResearch
	// AgentTypeCode is a code generation and analysis agent.
	AgentTypeCode
	// AgentTypeCustom is a user-defined custom agent type.
	AgentTypeCustom
)

// String returns the string representation of the agent type.
func (t AgentType) String() string {
	switch t {
	case AgentTypeChat:
		return "chat"
	case AgentTypeWorkflow:
		return "workflow"
	case AgentTypeResearch:
		return "research"
	case AgentTypeCode:
		return "code"
	case AgentTypeCustom:
		return "custom"
	default:
		return "unknown"
	}
}

// MarshalJSON implements the json.Marshaler interface.
func (t AgentType) MarshalJSON() ([]byte, error) {
	return []byte(`"` + t.String() + `"`), nil
}

// UnmarshalJSON implements the json.Unmarshaler interface.
func (t *AgentType) UnmarshalJSON(data []byte) error {
	str := string(data)
	if len(str) >= 2 && str[0] == '"' && str[len(str)-1] == '"' {
		str = str[1 : len(str)-1]
	}
	switch str {
	case "chat":
		*t = AgentTypeChat
	case "workflow":
		*t = AgentTypeWorkflow
	case "research":
		*t = AgentTypeResearch
	case "code":
		*t = AgentTypeCode
	case "custom":
		*t = AgentTypeCustom
	default:
		*t = AgentTypeUnknown
	}
	return nil
}

// Agent represents an AI agent in the LiuHao AI OS.
type Agent struct {
	// ID is the unique identifier of the agent.
	ID string `json:"id"`

	// Name is the human-readable name of the agent.
	Name string `json:"name"`

	// Description is a brief description of the agent's purpose.
	Description string `json:"description,omitempty"`

	// Type is the type of the agent.
	Type AgentType `json:"type"`

	// Status is the current operational status of the agent.
	Status AgentStatus `json:"status"`

	// Model is the underlying LLM model used by the agent.
	Model string `json:"model,omitempty"`

	// SystemPrompt is the system prompt that defines the agent's behavior.
	SystemPrompt string `json:"system_prompt,omitempty"`

	// Tools is a list of tool names available to the agent.
	Tools []string `json:"tools,omitempty"`

	// Metadata contains custom key-value metadata for the agent.
	Metadata map[string]string `json:"metadata,omitempty"`

	// CreatedAt is the timestamp when the agent was created.
	CreatedAt time.Time `json:"created_at"`

	// UpdatedAt is the timestamp when the agent was last updated.
	UpdatedAt time.Time `json:"updated_at"`

	// LastRunAt is the timestamp of the agent's last execution.
	LastRunAt *time.Time `json:"last_run_at,omitempty"`
}

// AgentCreateOptions specifies the parameters for creating a new agent.
type AgentCreateOptions struct {
	// Name is the human-readable name of the agent (required).
	Name string `json:"name"`

	// Description is a brief description of the agent's purpose.
	Description string `json:"description,omitempty"`

	// Type is the type of the agent (required).
	Type AgentType `json:"type"`

	// Model is the underlying LLM model to use.
	Model string `json:"model,omitempty"`

	// SystemPrompt defines the agent's behavior and persona.
	SystemPrompt string `json:"system_prompt,omitempty"`

	// Tools is a list of tool names to make available to the agent.
	Tools []string `json:"tools,omitempty"`

	// Metadata contains custom key-value metadata.
	Metadata map[string]string `json:"metadata,omitempty"`
}

// AgentUpdateOptions specifies the parameters for updating an existing agent.
type AgentUpdateOptions struct {
	// Name is the new name of the agent.
	Name *string `json:"name,omitempty"`

	// Description is the new description of the agent.
	Description *string `json:"description,omitempty"`

	// Model is the new LLM model to use.
	Model *string `json:"model,omitempty"`

	// SystemPrompt is the new system prompt.
	SystemPrompt *string `json:"system_prompt,omitempty"`

	// Tools is the new list of available tools.
	Tools *[]string `json:"tools,omitempty"`

	// Metadata is the new metadata map.
	Metadata *map[string]string `json:"metadata,omitempty"`

	// Status can be set to change the agent's operational status.
	Status *AgentStatus `json:"status,omitempty"`
}

// AgentExecuteOptions specifies parameters for executing an agent.
type AgentExecuteOptions struct {
	// Input is the input text/prompt for the agent execution.
	Input string `json:"input"`

	// Context provides additional context for the execution.
	Context map[string]interface{} `json:"context,omitempty"`

	// Stream indicates whether to stream the response.
	Stream bool `json:"stream,omitempty"`

	// MaxTokens is the maximum number of tokens to generate.
	MaxTokens int `json:"max_tokens,omitempty"`

	// Temperature controls randomness in generation (0.0-1.0).
	Temperature *float64 `json:"temperature,omitempty"`
}

// AgentExecution represents the result of an agent execution.
type AgentExecution struct {
	// ID is the unique identifier of this execution.
	ID string `json:"id"`

	// AgentID is the ID of the agent that was executed.
	AgentID string `json:"agent_id"`

	// Status is the current status of the execution.
	Status AgentStatus `json:"status"`

	// Output is the agent's output/response.
	Output string `json:"output,omitempty"`

	// Input is the input that was provided to the agent.
	Input string `json:"input,omitempty"`

	// TokensUsed is the number of tokens consumed.
	TokensUsed int `json:"tokens_used,omitempty"`

	// StartedAt is when the execution started.
	StartedAt *time.Time `json:"started_at,omitempty"`

	// CompletedAt is when the execution completed.
	CompletedAt *time.Time `json:"completed_at,omitempty"`

	// Error contains error information if the execution failed.
	Error string `json:"error,omitempty"`
}

// AgentMetrics represents performance metrics for an agent.
type AgentMetrics struct {
	// AgentID is the ID of the agent.
	AgentID string `json:"agent_id"`

	// TotalExecutions is the total number of executions.
	TotalExecutions int64 `json:"total_executions"`

	// SuccessfulExecutions is the count of successful executions.
	SuccessfulExecutions int64 `json:"successful_executions"`

	// FailedExecutions is the count of failed executions.
	FailedExecutions int64 `json:"failed_executions"`

	// AverageDurationMs is the average execution duration in milliseconds.
	AverageDurationMs float64 `json:"average_duration_ms"`

	// TotalTokensUsed is the total tokens consumed across all executions.
	TotalTokensUsed int64 `json:"total_tokens_used"`

	// LastExecutionAt is the timestamp of the most recent execution.
	LastExecutionAt *time.Time `json:"last_execution_at,omitempty"`

	// Period is the time period these metrics cover (e.g., "24h", "7d").
	Period string `json:"period,omitempty"`
}

// MemoryCategory represents the category of a memory item.
type MemoryCategory int

const (
	// MemoryCategoryUnknown is the default zero value.
	MemoryCategoryUnknown MemoryCategory = iota
	// MemoryCategoryFact is a factual memory item.
	MemoryCategoryFact
	// MemoryCategoryPreference is a user preference memory.
	MemoryCategoryPreference
	// MemoryCategoryExperience is an experience-based memory.
	MemoryCategoryExperience
	// MemoryCategoryConversation is a conversation memory.
	MemoryCategoryConversation
	// MemoryCategoryTask is a task-related memory.
	MemoryCategoryTask
	// MemoryCategoryCustom is a custom category.
	MemoryCategoryCustom
)

// String returns the string representation of the memory category.
func (c MemoryCategory) String() string {
	switch c {
	case MemoryCategoryFact:
		return "fact"
	case MemoryCategoryPreference:
		return "preference"
	case MemoryCategoryExperience:
		return "experience"
	case MemoryCategoryConversation:
		return "conversation"
	case MemoryCategoryTask:
		return "task"
	case MemoryCategoryCustom:
		return "custom"
	default:
		return "unknown"
	}
}

// MarshalJSON implements the json.Marshaler interface.
func (c MemoryCategory) MarshalJSON() ([]byte, error) {
	return []byte(`"` + c.String() + `"`), nil
}

// UnmarshalJSON implements the json.Unmarshaler interface.
func (c *MemoryCategory) UnmarshalJSON(data []byte) error {
	str := string(data)
	if len(str) >= 2 && str[0] == '"' && str[len(str)-1] == '"' {
		str = str[1 : len(str)-1]
	}
	switch str {
	case "fact":
		*c = MemoryCategoryFact
	case "preference":
		*c = MemoryCategoryPreference
	case "experience":
		*c = MemoryCategoryExperience
	case "conversation":
		*c = MemoryCategoryConversation
	case "task":
		*c = MemoryCategoryTask
	case "custom":
		*c = MemoryCategoryCustom
	default:
		*c = MemoryCategoryUnknown
	}
	return nil
}

// MemoryItem represents a single memory entry in the agent's memory store.
type MemoryItem struct {
	// ID is the unique identifier of the memory item.
	ID string `json:"id"`

	// Content is the textual content of the memory.
	Content string `json:"content"`

	// Category classifies the type of memory.
	Category MemoryCategory `json:"category"`

	// Tags are additional labels for organizing and searching memories.
	Tags []string `json:"tags,omitempty"`

	// Importance is a score from 0.0 to 1.0 indicating how important this memory is.
	Importance float64 `json:"importance,omitempty"`

	// AgentID is the ID of the agent this memory belongs to.
	AgentID string `json:"agent_id,omitempty"`

	// Source describes where the memory originated from.
	Source string `json:"source,omitempty"`

	// Metadata contains additional key-value data.
	Metadata map[string]string `json:"metadata,omitempty"`

	// CreatedAt is when the memory was created.
	CreatedAt time.Time `json:"created_at"`

	// UpdatedAt is when the memory was last updated.
	UpdatedAt time.Time `json:"updated_at"`

	// LastAccessedAt is when the memory was last accessed.
	LastAccessedAt *time.Time `json:"last_accessed_at,omitempty"`
}

// MemoryStoreOptions specifies parameters for storing a new memory item.
type MemoryStoreOptions struct {
	// Content is the textual content of the memory (required).
	Content string `json:"content"`

	// Category classifies the memory type.
	Category MemoryCategory `json:"category"`

	// Tags are additional labels for the memory.
	Tags []string `json:"tags,omitempty"`

	// Importance is a score from 0.0 to 1.0.
	Importance float64 `json:"importance,omitempty"`

	// AgentID associates the memory with a specific agent.
	AgentID string `json:"agent_id,omitempty"`

	// Source describes the origin of the memory.
	Source string `json:"source,omitempty"`

	// Metadata contains additional key-value data.
	Metadata map[string]string `json:"metadata,omitempty"`
}

// MemoryUpdateOptions specifies parameters for updating a memory item.
type MemoryUpdateOptions struct {
	// Content is the new content of the memory.
	Content *string `json:"content,omitempty"`

	// Category is the new category.
	Category *MemoryCategory `json:"category,omitempty"`

	// Tags are the new tags.
	Tags *[]string `json:"tags,omitempty"`

	// Importance is the new importance score.
	Importance *float64 `json:"importance,omitempty"`

	// Metadata is the new metadata map.
	Metadata *map[string]string `json:"metadata,omitempty"`
}

// MemorySearchOptions specifies parameters for searching memories.
type MemorySearchOptions struct {
	// Query is the search query text (required for semantic search).
	Query string `json:"query,omitempty"`

	// Category filters results by memory category.
	Category *MemoryCategory `json:"category,omitempty"`

	// Tags filters results that match all specified tags.
	Tags []string `json:"tags,omitempty"`

	// AgentID filters memories by agent.
	AgentID string `json:"agent_id,omitempty"`

	// MinImportance filters memories with importance above this threshold.
	MinImportance *float64 `json:"min_importance,omitempty"`

	// Limit is the maximum number of results to return.
	Limit int `json:"limit,omitempty"`

	// Threshold is the minimum similarity score (0.0-1.0) for semantic search.
	Threshold *float64 `json:"threshold,omitempty"`

	// SortBy is the field to sort by ("relevance", "created_at", "importance").
	SortBy string `json:"sort_by,omitempty"`
}

// MemorySearchResult represents a single search result with relevance score.
type MemorySearchResult struct {
	// Item is the memory item.
	Item MemoryItem `json:"item"`

	// Score is the relevance/similarity score (0.0-1.0).
	Score float64 `json:"score"`
}

// KnowledgeBase represents a knowledge base in the system.
type KnowledgeBase struct {
	// ID is the unique identifier of the knowledge base.
	ID string `json:"id"`

	// Name is the human-readable name.
	Name string `json:"name"`

	// Description is a brief description.
	Description string `json:"description,omitempty"`

	// EmbeddingModel is the model used for generating embeddings.
	EmbeddingModel string `json:"embedding_model,omitempty"`

	// DocumentCount is the number of documents in the base.
	DocumentCount int `json:"document_count"`

	// VectorCount is the number of vectors/chunks in the base.
	VectorCount int `json:"vector_count"`

	// CreatedAt is when the knowledge base was created.
	CreatedAt time.Time `json:"created_at"`

	// UpdatedAt is when it was last updated.
	UpdatedAt time.Time `json:"updated_at"`
}

// KnowledgeBaseCreateOptions specifies parameters for creating a knowledge base.
type KnowledgeBaseCreateOptions struct {
	// Name is the name of the knowledge base (required).
	Name string `json:"name"`

	// Description is a brief description.
	Description string `json:"description,omitempty"`

	// EmbeddingModel is the embedding model to use.
	EmbeddingModel string `json:"embedding_model,omitempty"`
}

// Document represents a document in a knowledge base.
type Document struct {
	// ID is the unique identifier of the document.
	ID string `json:"id"`

	// KnowledgeBaseID is the ID of the containing knowledge base.
	KnowledgeBaseID string `json:"knowledge_base_id"`

	// Title is the document title.
	Title string `json:"title"`

	// Content is the full text content of the document.
	Content string `json:"content,omitempty"`

	// Source is the original source URL or file path.
	Source string `json:"source,omitempty"`

	// Type is the document type (e.g., "text", "pdf", "markdown").
	Type string `json:"type,omitempty"`

	// ChunkCount is the number of chunks this document was split into.
	ChunkCount int `json:"chunk_count"`

	// SizeBytes is the size of the document in bytes.
	SizeBytes int64 `json:"size_bytes"`

	// Tags are labels attached to the document.
	Tags []string `json:"tags,omitempty"`

	// Metadata contains additional document metadata.
	Metadata map[string]string `json:"metadata,omitempty"`

	// CreatedAt is when the document was uploaded.
	CreatedAt time.Time `json:"created_at"`

	// UpdatedAt is when the document was last updated.
	UpdatedAt time.Time `json:"updated_at"`
}

// DocumentUploadOptions specifies parameters for uploading a document.
type DocumentUploadOptions struct {
	// Title is the document title (required).
	Title string `json:"title"`

	// Content is the text content of the document (required if not using file upload).
	Content string `json:"content,omitempty"`

	// Source is the original source URL or file path.
	Source string `json:"source,omitempty"`

	// Type is the document type.
	Type string `json:"type,omitempty"`

	// Tags are labels for the document.
	Tags []string `json:"tags,omitempty"`

	// Metadata contains additional metadata.
	Metadata map[string]string `json:"metadata,omitempty"`

	// ChunkSize is the chunk size in characters for splitting.
	ChunkSize int `json:"chunk_size,omitempty"`

	// ChunkOverlap is the overlap between chunks in characters.
	ChunkOverlap int `json:"chunk_overlap,omitempty"`
}

// SearchResult represents a single knowledge base search result.
type SearchResult struct {
	// DocumentID is the ID of the source document.
	DocumentID string `json:"document_id"`

	// DocumentTitle is the title of the source document.
	DocumentTitle string `json:"document_title"`

	// ChunkID is the ID of the matching chunk.
	ChunkID string `json:"chunk_id"`

	// Content is the text content of the matching chunk.
	Content string `json:"content"`

	// Score is the relevance/similarity score (0.0-1.0).
	Score float64 `json:"score"`

	// Metadata contains metadata from the source document.
	Metadata map[string]string `json:"metadata,omitempty"`
}

// KnowledgeSearchOptions specifies parameters for searching knowledge bases.
type KnowledgeSearchOptions struct {
	// Query is the search query text (required).
	Query string `json:"query"`

	// KnowledgeBaseIDs limits the search to specific knowledge bases.
	KnowledgeBaseIDs []string `json:"knowledge_base_ids,omitempty"`

	// Limit is the maximum number of results to return.
	Limit int `json:"limit,omitempty"`

	// Threshold is the minimum similarity score (0.0-1.0).
	Threshold *float64 `json:"threshold,omitempty"`

	// Filters are additional metadata filters.
	Filters map[string]string `json:"filters,omitempty"`
}

// Collection represents a collection within a knowledge base.
type Collection struct {
	// ID is the unique identifier of the collection.
	ID string `json:"id"`

	// KnowledgeBaseID is the parent knowledge base ID.
	KnowledgeBaseID string `json:"knowledge_base_id"`

	// Name is the collection name.
	Name string `json:"name"`

	// Description is a brief description.
	Description string `json:"description,omitempty"`

	// DocumentCount is the number of documents in the collection.
	DocumentCount int `json:"document_count"`

	// CreatedAt is when the collection was created.
	CreatedAt time.Time `json:"created_at"`
}

// CollectionCreateOptions specifies parameters for creating a collection.
type CollectionCreateOptions struct {
	// Name is the collection name (required).
	Name string `json:"name"`

	// Description is a brief description.
	Description string `json:"description,omitempty"`
}

// TaskStatus represents the status of an asynchronous task.
type TaskStatus int

const (
	// TaskStatusUnknown is the default zero value.
	TaskStatusUnknown TaskStatus = iota
	// TaskStatusPending indicates the task is waiting to start.
	TaskStatusPending
	// TaskStatusQueued indicates the task is queued for processing.
	TaskStatusQueued
	// TaskStatusRunning indicates the task is currently executing.
	TaskStatusRunning
	// TaskStatusCompleted indicates the task completed successfully.
	TaskStatusCompleted
	// TaskStatusFailed indicates the task failed.
	TaskStatusFailed
	// TaskStatusCancelled indicates the task was cancelled.
	TaskStatusCancelled
	// TaskStatusRetrying indicates the task is being retried after a failure.
	TaskStatusRetrying
)

// String returns the string representation of the task status.
func (s TaskStatus) String() string {
	switch s {
	case TaskStatusPending:
		return "pending"
	case TaskStatusQueued:
		return "queued"
	case TaskStatusRunning:
		return "running"
	case TaskStatusCompleted:
		return "completed"
	case TaskStatusFailed:
		return "failed"
	case TaskStatusCancelled:
		return "cancelled"
	case TaskStatusRetrying:
		return "retrying"
	default:
		return "unknown"
	}
}

// MarshalJSON implements the json.Marshaler interface.
func (s TaskStatus) MarshalJSON() ([]byte, error) {
	return []byte(`"` + s.String() + `"`), nil
}

// UnmarshalJSON implements the json.Unmarshaler interface.
func (s *TaskStatus) UnmarshalJSON(data []byte) error {
	str := string(data)
	if len(str) >= 2 && str[0] == '"' && str[len(str)-1] == '"' {
		str = str[1 : len(str)-1]
	}
	switch str {
	case "pending":
		*s = TaskStatusPending
	case "queued":
		*s = TaskStatusQueued
	case "running":
		*s = TaskStatusRunning
	case "completed":
		*s = TaskStatusCompleted
	case "failed":
		*s = TaskStatusFailed
	case "cancelled":
		*s = TaskStatusCancelled
	case "retrying":
		*s = TaskStatusRetrying
	default:
		*s = TaskStatusUnknown
	}
	return nil
}

// Task represents an asynchronous task in the system.
type Task struct {
	// ID is the unique identifier of the task.
	ID string `json:"id"`

	// Type is the type of task being performed.
	Type string `json:"type"`

	// Status is the current status of the task.
	Status TaskStatus `json:"status"`

	// Progress is the current progress percentage (0-100).
	Progress int `json:"progress,omitempty"`

	// Description is a human-readable description of the task.
	Description string `json:"description,omitempty"`

	// Result contains the task result data when completed.
	Result interface{} `json:"result,omitempty"`

	// Error contains error information if the task failed.
	Error string `json:"error,omitempty"`

	// RetryCount is the number of times the task has been retried.
	RetryCount int `json:"retry_count"`

	// MaxRetries is the maximum number of retry attempts.
	MaxRetries int `json:"max_retries"`

	// CreatedAt is when the task was created.
	CreatedAt time.Time `json:"created_at"`

	// StartedAt is when the task started executing.
	StartedAt *time.Time `json:"started_at,omitempty"`

	// CompletedAt is when the task completed.
	CompletedAt *time.Time `json:"completed_at,omitempty"`
}

// TaskCreateOptions specifies parameters for creating a new task.
type TaskCreateOptions struct {
	// Type is the type of task to create (required).
	Type string `json:"type"`

	// Description is a human-readable description.
	Description string `json:"description,omitempty"`

	// Params are task-specific parameters.
	Params map[string]interface{} `json:"params,omitempty"`

	// MaxRetries is the maximum number of retry attempts on failure.
	MaxRetries int `json:"max_retries,omitempty"`
}

// HealthStatus represents the overall health status.
type HealthStatus int

const (
	// HealthStatusUnknown is the default zero value.
	HealthStatusUnknown HealthStatus = iota
	// HealthStatusHealthy indicates all systems are operational.
	HealthStatusHealthy
	// HealthStatusDegraded indicates some systems are experiencing issues.
	HealthStatusDegraded
	// HealthStatusUnhealthy indicates critical systems are down.
	HealthStatusUnhealthy
)

// String returns the string representation of the health status.
func (s HealthStatus) String() string {
	switch s {
	case HealthStatusHealthy:
		return "healthy"
	case HealthStatusDegraded:
		return "degraded"
	case HealthStatusUnhealthy:
		return "unhealthy"
	default:
		return "unknown"
	}
}

// MarshalJSON implements the json.Marshaler interface.
func (s HealthStatus) MarshalJSON() ([]byte, error) {
	return []byte(`"` + s.String() + `"`), nil
}

// UnmarshalJSON implements the json.Unmarshaler interface.
func (s *HealthStatus) UnmarshalJSON(data []byte) error {
	str := string(data)
	if len(str) >= 2 && str[0] == '"' && str[len(str)-1] == '"' {
		str = str[1 : len(str)-1]
	}
	switch str {
	case "healthy":
		*s = HealthStatusHealthy
	case "degraded":
		*s = HealthStatusDegraded
	case "unhealthy":
		*s = HealthStatusUnhealthy
	default:
		*s = HealthStatusUnknown
	}
	return nil
}

// HealthCheck represents the overall health check result.
type HealthCheck struct {
	// Status is the overall health status.
	Status HealthStatus `json:"status"`

	// Version is the version of the LiuHao AI OS.
	Version string `json:"version"`

	// Timestamp is when the health check was performed.
	Timestamp time.Time `json:"timestamp"`

	// Uptime is the system uptime in seconds.
	Uptime int64 `json:"uptime"`

	// Components is a map of component names to their health status.
	Components map[string]ComponentHealth `json:"components,omitempty"`
}

// ComponentHealth represents the health of an individual component.
type ComponentHealth struct {
	// Status is the health status of this component.
	Status HealthStatus `json:"status"`

	// Message provides additional information about the component state.
	Message string `json:"message,omitempty"`

	// LatencyMs is the response latency in milliseconds.
	LatencyMs int64 `json:"latency_ms,omitempty"`

	// LastChecked is when this component was last checked.
	LastChecked time.Time `json:"last_checked"`
}

// DetailedHealth provides comprehensive health information.
type DetailedHealth struct {
	// Status is the overall health status.
	Status HealthStatus `json:"status"`

	// Version is the system version.
	Version string `json:"version"`

	// Timestamp is when the check was performed.
	Timestamp time.Time `json:"timestamp"`

	// Uptime is the system uptime in seconds.
	Uptime int64 `json:"uptime"`

	// Database contains database health info.
	Database *ComponentHealth `json:"database,omitempty"`

	// Redis contains Redis health info.
	Redis *ComponentHealth `json:"redis,omitempty"`

	// Storage contains storage health info.
	Storage *ComponentHealth `json:"storage,omitempty"`

	// LLM contains LLM provider health info.
	LLM *ComponentHealth `json:"llm,omitempty"`

	// VectorDB contains vector database health info.
	VectorDB *ComponentHealth `json:"vector_db,omitempty"`

	// Workers contains worker pool health info.
	Workers *WorkerHealth `json:"workers,omitempty"`

	// System contains system resource info.
	System *SystemHealth `json:"system,omitempty"`
}

// WorkerHealth represents worker pool health.
type WorkerHealth struct {
	// Status is the health status.
	Status HealthStatus `json:"status"`

	// ActiveWorkers is the number of currently active workers.
	ActiveWorkers int `json:"active_workers"`

	// TotalWorkers is the total number of workers.
	TotalWorkers int `json:"total_workers"`

	// PendingTasks is the number of tasks waiting to be processed.
	PendingTasks int64 `json:"pending_tasks"`

	// ProcessedTasks is the total number of processed tasks.
	ProcessedTasks int64 `json:"processed_tasks"`
}

// SystemHealth represents system resource health.
type SystemHealth struct {
	// Status is the health status.
	Status HealthStatus `json:"status"`

	// CPUPercent is the current CPU usage percentage.
	CPUPercent float64 `json:"cpu_percent"`

	// MemoryPercent is the current memory usage percentage.
	MemoryPercent float64 `json:"memory_percent"`

	// DiskPercent is the current disk usage percentage.
	DiskPercent float64 `json:"disk_percent"`

	// Goroutines is the number of active goroutines.
	Goroutines int `json:"goroutines"`
}

// ErrorResponse represents an error response from the API.
type ErrorResponse struct {
	Error struct {
		Code    string                 `json:"code"`
		Message string                 `json:"message"`
		Details map[string]interface{} `json:"details,omitempty"`
	} `json:"error"`
}

// PaginatedResponse represents a paginated list response.
type PaginatedResponse struct {
	// Page is the current page number.
	Page int `json:"page"`

	// PageSize is the number of items per page.
	PageSize int `json:"page_size"`

	// Total is the total number of items across all pages.
	Total int64 `json:"total"`

	// TotalPages is the total number of pages.
	TotalPages int `json:"total_pages"`

	// HasNext indicates whether there is a next page.
	HasNext bool `json:"has_next"`

	// HasPrev indicates whether there is a previous page.
	HasPrev bool `json:"has_prev"`
}

// AgentListResponse is a paginated list of agents.
type AgentListResponse struct {
	PaginatedResponse
	Agents []Agent `json:"data"`
}

// MemoryListResponse is a paginated list of memory items.
type MemoryListResponse struct {
	PaginatedResponse
	Items []MemoryItem `json:"data"`
}

// DocumentListResponse is a paginated list of documents.
type DocumentListResponse struct {
	PaginatedResponse
	Documents []Document `json:"data"`
}

// KnowledgeBaseListResponse is a paginated list of knowledge bases.
type KnowledgeBaseListResponse struct {
	PaginatedResponse
	KnowledgeBases []KnowledgeBase `json:"data"`
}

// TaskListResponse is a paginated list of tasks.
type TaskListResponse struct {
	PaginatedResponse
	Tasks []Task `json:"data"`
}

// CollectionListResponse is a paginated list of collections.
type CollectionListResponse struct {
	PaginatedResponse
	Collections []Collection `json:"data"`
}
