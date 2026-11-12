package liuhao

import (
	"context"
	"fmt"
	"net/http"
)

// AgentsService provides methods for managing agents in the LiuHao AI OS.
//
// Use Client.Agents to access this service. Agents are the core entities
// in LiuHao AI OS that can execute tasks, use tools, and maintain memory.
type AgentsService struct {
	client *Client
}

// List retrieves a paginated list of agents.
//
// The opts parameter can be used to control pagination, sorting, and
// filtering. Pass nil to use default options.
//
// Example:
//
//	resp, err := client.Agents.List(ctx, &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	    Sort:     "created_at",
//	    Order:    "desc",
//	})
func (s *AgentsService) List(ctx context.Context, opts *ListOptions) (*AgentListResponse, error) {
	path := "/agents"
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result AgentListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Get retrieves a single agent by its ID.
//
// Returns a NotFoundError if the agent does not exist.
//
// Example:
//
//	agent, err := client.Agents.Get(ctx, "agent_abc123")
//	if liuhao.IsNotFoundError(err) {
//	    fmt.Println("Agent not found")
//	}
func (s *AgentsService) Get(ctx context.Context, id string) (*Agent, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s", id)

	var result Agent
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Create creates a new agent with the specified options.
//
// The Name and Type fields are required. Returns a ValidationError if
// required fields are missing or invalid.
//
// Example:
//
//	agent, err := client.Agents.Create(ctx, &liuhao.AgentCreateOptions{
//	    Name:         "My Assistant",
//	    Type:         liuhao.AgentTypeChat,
//	    Model:        "gpt-4",
//	    SystemPrompt: "You are a helpful assistant.",
//	    Tools:        []string{"web_search", "code_interpreter"},
//	})
func (s *AgentsService) Create(ctx context.Context, opts *AgentCreateOptions) (*Agent, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "agent create options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Name == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_name",
				Message:    "agent name is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/agents"

	var result Agent
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Update updates an existing agent.
//
// Only the fields that are set in the opts parameter will be updated.
// Returns a NotFoundError if the agent does not exist.
//
// Example:
//
//	newName := "Updated Assistant"
//	agent, err := client.Agents.Update(ctx, "agent_abc123", &liuhao.AgentUpdateOptions{
//	    Name: &newName,
//	})
func (s *AgentsService) Update(ctx context.Context, id string, opts *AgentUpdateOptions) (*Agent, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "agent update options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s", id)

	var result Agent
	if err := s.client.request(ctx, http.MethodPatch, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Delete deletes an agent by its ID.
//
// Returns a NotFoundError if the agent does not exist. This operation
// is irreversible.
//
// Example:
//
//	err := client.Agents.Delete(ctx, "agent_abc123")
//	if err != nil {
//	    // handle error
//	}
func (s *AgentsService) Delete(ctx context.Context, id string) error {
	if id == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s", id)

	if err := s.client.request(ctx, http.MethodDelete, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// Execute triggers an agent execution with the given input.
//
// This starts an asynchronous execution of the agent. Use GetStatus
// to check the execution status, or wait for the result.
//
// Example:
//
//	execution, err := client.Agents.Execute(ctx, "agent_abc123", &liuhao.AgentExecuteOptions{
//	    Input: "Write a hello world program in Go",
//	})
func (s *AgentsService) Execute(ctx context.Context, agentID string, opts *AgentExecuteOptions) (*AgentExecution, error) {
	if agentID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "execute options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Input == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_input",
				Message:    "execute input must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s/execute", agentID)

	var result AgentExecution
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// GetStatus retrieves the status of an agent execution.
//
// Use this to poll for completion after calling Execute.
//
// Example:
//
//	execution, err := client.Agents.GetStatus(ctx, "agent_abc123", "exec_xyz")
//	if execution.Status == liuhao.AgentStatusCompleted {
//	    fmt.Println(execution.Output)
//	}
func (s *AgentsService) GetStatus(ctx context.Context, agentID, executionID string) (*AgentExecution, error) {
	if agentID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_agent_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if executionID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_execution_id",
				Message:    "execution ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s/executions/%s", agentID, executionID)

	var result AgentExecution
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Cancel cancels a running agent execution.
//
// Returns an error if the execution cannot be cancelled (e.g., it has
// already completed or does not exist).
//
// Example:
//
//	err := client.Agents.Cancel(ctx, "agent_abc123", "exec_xyz")
func (s *AgentsService) Cancel(ctx context.Context, agentID, executionID string) error {
	if agentID == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_agent_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if executionID == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_execution_id",
				Message:    "execution ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s/executions/%s/cancel", agentID, executionID)

	if err := s.client.request(ctx, http.MethodPost, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// Metrics retrieves performance metrics for an agent.
//
// The period parameter specifies the time range for metrics (e.g.,
// "24h", "7d", "30d"). Leave empty to use the default period.
//
// Example:
//
//	metrics, err := client.Agents.Metrics(ctx, "agent_abc123", "24h")
//	fmt.Printf("Total executions: %d\n", metrics.TotalExecutions)
func (s *AgentsService) Metrics(ctx context.Context, agentID, period string) (*AgentMetrics, error) {
	if agentID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_agent_id",
				Message:    "agent ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/agents/%s/metrics", agentID)
	if period != "" {
		path = addQueryParams(path, "period="+period)
	}

	var result AgentMetrics
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}
