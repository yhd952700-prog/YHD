package liuhao

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestAgentsList tests the Agents.List method.
func TestAgentsList(t *testing.T) {
	tests := []struct {
		name      string
		opts      *ListOptions
		server    http.HandlerFunc
		wantCount int
		wantTotal int64
		wantErr   bool
	}{
		{
			name: "successful list",
			opts: &ListOptions{Page: 1, PageSize: 10},
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("method = %s, want GET", r.Method)
				}
				if r.URL.Path != "/agents" {
					t.Errorf("path = %s, want /agents", r.URL.Path)
				}
				if r.URL.Query().Get("page") != "1" {
					t.Errorf("page = %s, want 1", r.URL.Query().Get("page"))
				}
				if r.URL.Query().Get("page_size") != "10" {
					t.Errorf("page_size = %s, want 10", r.URL.Query().Get("page_size"))
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(AgentListResponse{
					PaginatedResponse: PaginatedResponse{
						Page:       1,
						PageSize:   10,
						Total:      25,
						TotalPages: 3,
						HasNext:    true,
						HasPrev:    false,
					},
					Agents: []Agent{
						{ID: "agent-1", Name: "Agent 1", Type: AgentTypeChat, Status: AgentStatusIdle},
						{ID: "agent-2", Name: "Agent 2", Type: AgentTypeCode, Status: AgentStatusRunning},
					},
				})
			},
			wantCount: 2,
			wantTotal: 25,
			wantErr:   false,
		},
		{
			name: "empty list",
			opts: &ListOptions{Page: 1, PageSize: 10},
			server: func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(AgentListResponse{
					PaginatedResponse: PaginatedResponse{
						Page:       1,
						PageSize:   10,
						Total:      0,
						TotalPages: 0,
						HasNext:    false,
						HasPrev:    false,
					},
					Agents: []Agent{},
				})
			},
			wantCount: 0,
			wantTotal: 0,
			wantErr:   false,
		},
		{
			name: "nil options",
			opts: nil,
			server: func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(AgentListResponse{
					PaginatedResponse: PaginatedResponse{
						Page:       1,
						PageSize:   20,
						Total:      5,
						TotalPages: 1,
					},
					Agents: []Agent{
						{ID: "agent-1", Name: "Test", Type: AgentTypeChat, Status: AgentStatusIdle},
					},
				})
			},
			wantCount: 1,
			wantTotal: 5,
			wantErr:   false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			resp, err := client.Agents.List(context.Background(), tt.opts)

			if (err != nil) != tt.wantErr {
				t.Errorf("List() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if len(resp.Agents) != tt.wantCount {
					t.Errorf("len(Agents) = %d, want %d", len(resp.Agents), tt.wantCount)
				}
				if resp.Total != tt.wantTotal {
					t.Errorf("Total = %d, want %d", resp.Total, tt.wantTotal)
				}
			}
		})
	}
}

// TestAgentsGet tests the Agents.Get method.
func TestAgentsGet(t *testing.T) {
	tests := []struct {
		name      string
		agentID   string
		server    http.HandlerFunc
		wantName  string
		wantType  AgentType
		wantErr   bool
	}{
		{
			name:    "successful get",
			agentID: "agent-123",
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("method = %s, want GET", r.Method)
				}
				if r.URL.Path != "/agents/agent-123" {
					t.Errorf("path = %s, want /agents/agent-123", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(Agent{
					ID:          "agent-123",
					Name:        "My Agent",
					Description: "A test agent",
					Type:        AgentTypeChat,
					Status:      AgentStatusIdle,
					Model:       "gpt-4",
					CreatedAt:   time.Now(),
					UpdatedAt:   time.Now(),
				})
			},
			wantName: "My Agent",
			wantType: AgentTypeChat,
			wantErr:  false,
		},
		{
			name:    "not found",
			agentID: "nonexistent",
			server: func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusNotFound)
				json.NewEncoder(w).Encode(ErrorResponse{
					Error: struct {
						Code    string                 `json:"code"`
						Message string                 `json:"message"`
						Details map[string]interface{} `json:"details,omitempty"`
					}{
						Code:    "not_found",
						Message: "Agent not found",
					},
				})
			},
			wantErr: true,
		},
		{
			name:    "empty ID",
			agentID: "",
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with empty ID")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			agent, err := client.Agents.Get(context.Background(), tt.agentID)

			if (err != nil) != tt.wantErr {
				t.Errorf("Get() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if agent.Name != tt.wantName {
					t.Errorf("Name = %q, want %q", agent.Name, tt.wantName)
				}
				if agent.Type != tt.wantType {
					t.Errorf("Type = %v, want %v", agent.Type, tt.wantType)
				}
			}
		})
	}
}

// TestAgentsCreate tests the Agents.Create method.
func TestAgentsCreate(t *testing.T) {
	tests := []struct {
		name      string
		opts      *AgentCreateOptions
		server    http.HandlerFunc
		wantName  string
		wantErr   bool
	}{
		{
			name: "successful create",
			opts: &AgentCreateOptions{
				Name:         "New Agent",
				Type:         AgentTypeChat,
				Model:        "gpt-4",
				SystemPrompt: "You are helpful",
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				var body AgentCreateOptions
				json.NewDecoder(r.Body).Decode(&body)
				if body.Name != "New Agent" {
					t.Errorf("name = %q, want %q", body.Name, "New Agent")
				}
				if body.Type != AgentTypeChat {
					t.Errorf("type = %v, want %v", body.Type, AgentTypeChat)
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusCreated)
				json.NewEncoder(w).Encode(Agent{
					ID:          "new-agent-1",
					Name:        body.Name,
					Type:        body.Type,
					Status:      AgentStatusIdle,
					Model:       body.Model,
					CreatedAt:   time.Now(),
					UpdatedAt:   time.Now(),
				})
			},
			wantName: "New Agent",
			wantErr:  false,
		},
		{
			name: "nil options",
			opts: nil,
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with nil options")
			},
			wantErr: true,
		},
		{
			name: "missing name",
			opts: &AgentCreateOptions{
				Type: AgentTypeChat,
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with missing name")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			agent, err := client.Agents.Create(context.Background(), tt.opts)

			if (err != nil) != tt.wantErr {
				t.Errorf("Create() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if agent.Name != tt.wantName {
					t.Errorf("Name = %q, want %q", agent.Name, tt.wantName)
				}
				if agent.ID == "" {
					t.Error("expected non-empty agent ID")
				}
			}
		})
	}
}

// TestAgentsDelete tests the Agents.Delete method.
func TestAgentsDelete(t *testing.T) {
	tests := []struct {
		name    string
		agentID string
		server  http.HandlerFunc
		wantErr bool
	}{
		{
			name:    "successful delete",
			agentID: "agent-123",
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Errorf("method = %s, want DELETE", r.Method)
				}
				if r.URL.Path != "/agents/agent-123" {
					t.Errorf("path = %s, want /agents/agent-123", r.URL.Path)
				}
				w.WriteHeader(http.StatusNoContent)
			},
			wantErr: false,
		},
		{
			name:    "empty ID",
			agentID: "",
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with empty ID")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			err := client.Agents.Delete(context.Background(), tt.agentID)

			if (err != nil) != tt.wantErr {
				t.Errorf("Delete() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

// TestAgentsExecute tests the Agents.Execute method.
func TestAgentsExecute(t *testing.T) {
	tests := []struct {
		name      string
		agentID   string
		opts      *AgentExecuteOptions
		server    http.HandlerFunc
		wantStatus AgentStatus
		wantErr   bool
	}{
		{
			name:    "successful execute",
			agentID: "agent-123",
			opts: &AgentExecuteOptions{
				Input: "Hello, world!",
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				if r.URL.Path != "/agents/agent-123/execute" {
					t.Errorf("path = %s, want /agents/agent-123/execute", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(AgentExecution{
					ID:      "exec-1",
					AgentID: "agent-123",
					Status:  AgentStatusRunning,
					Input:   "Hello, world!",
				})
			},
			wantStatus: AgentStatusRunning,
			wantErr:    false,
		},
		{
			name:    "nil options",
			agentID: "agent-123",
			opts:    nil,
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with nil options")
			},
			wantErr: true,
		},
		{
			name:    "empty input",
			agentID: "agent-123",
			opts:    &AgentExecuteOptions{},
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with empty input")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			exec, err := client.Agents.Execute(context.Background(), tt.agentID, tt.opts)

			if (err != nil) != tt.wantErr {
				t.Errorf("Execute() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if exec.Status != tt.wantStatus {
					t.Errorf("Status = %v, want %v", exec.Status, tt.wantStatus)
				}
			}
		})
	}
}

// TestAgentsMetrics tests the Agents.Metrics method.
func TestAgentsMetrics(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/agents/agent-123/metrics" {
			t.Errorf("path = %s, want /agents/agent-123/metrics", r.URL.Path)
		}
		if r.URL.Query().Get("period") != "24h" {
			t.Errorf("period = %s, want 24h", r.URL.Query().Get("period"))
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(AgentMetrics{
			AgentID:              "agent-123",
			TotalExecutions:      100,
			SuccessfulExecutions: 95,
			FailedExecutions:     5,
			AverageDurationMs:    2500.5,
			TotalTokensUsed:      50000,
			Period:               "24h",
		})
	}))
	defer server.Close()

	client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
	metrics, err := client.Agents.Metrics(context.Background(), "agent-123", "24h")

	if err != nil {
		t.Fatalf("Metrics() error = %v", err)
	}

	if metrics.AgentID != "agent-123" {
		t.Errorf("AgentID = %q, want %q", metrics.AgentID, "agent-123")
	}
	if metrics.TotalExecutions != 100 {
		t.Errorf("TotalExecutions = %d, want 100", metrics.TotalExecutions)
	}
	if metrics.SuccessfulExecutions != 95 {
		t.Errorf("SuccessfulExecutions = %d, want 95", metrics.SuccessfulExecutions)
	}
	if metrics.FailedExecutions != 5 {
		t.Errorf("FailedExecutions = %d, want 5", metrics.FailedExecutions)
	}
	if metrics.Period != "24h" {
		t.Errorf("Period = %q, want %q", metrics.Period, "24h")
	}
}

// TestAgentStatusString tests the AgentStatus String method.
func TestAgentStatusString(t *testing.T) {
	tests := []struct {
		status AgentStatus
		want   string
	}{
		{AgentStatusUnknown, "unknown"},
		{AgentStatusIdle, "idle"},
		{AgentStatusRunning, "running"},
		{AgentStatusPaused, "paused"},
		{AgentStatusCompleted, "completed"},
		{AgentStatusFailed, "failed"},
		{AgentStatusCancelled, "cancelled"},
		{AgentStatusStarting, "starting"},
		{AgentStatusStopping, "stopping"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := tt.status.String(); got != tt.want {
				t.Errorf("String() = %q, want %q", got, tt.want)
			}
		})
	}
}

// TestAgentTypeString tests the AgentType String method.
func TestAgentTypeString(t *testing.T) {
	tests := []struct {
		at   AgentType
		want string
	}{
		{AgentTypeUnknown, "unknown"},
		{AgentTypeChat, "chat"},
		{AgentTypeWorkflow, "workflow"},
		{AgentTypeResearch, "research"},
		{AgentTypeCode, "code"},
		{AgentTypeCustom, "custom"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := tt.at.String(); got != tt.want {
				t.Errorf("String() = %q, want %q", got, tt.want)
			}
		})
	}
}
