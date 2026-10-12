package liuhao

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestMemoryStore tests the Memory.Store method.
func TestMemoryStore(t *testing.T) {
	tests := []struct {
		name         string
		opts         *MemoryStoreOptions
		server       http.HandlerFunc
		wantContent  string
		wantCategory MemoryCategory
		wantErr      bool
	}{
		{
			name: "successful store",
			opts: &MemoryStoreOptions{
				Content:    "User prefers dark mode",
				Category:   MemoryCategoryPreference,
				Tags:       []string{"ui", "preference"},
				AgentID:    "agent-1",
				Importance: 0.8,
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				if r.URL.Path != "/memory" {
					t.Errorf("path = %s, want /memory", r.URL.Path)
				}
				var body MemoryStoreOptions
				json.NewDecoder(r.Body).Decode(&body)
				if body.Content != "User prefers dark mode" {
					t.Errorf("content = %q, want %q", body.Content, "User prefers dark mode")
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusCreated)
				json.NewEncoder(w).Encode(MemoryItem{
					ID:         "mem-1",
					Content:    body.Content,
					Category:   body.Category,
					Tags:       body.Tags,
					Importance: body.Importance,
					AgentID:    body.AgentID,
					CreatedAt:  time.Now(),
					UpdatedAt:  time.Now(),
				})
			},
			wantContent:  "User prefers dark mode",
			wantCategory: MemoryCategoryPreference,
			wantErr:      false,
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
			name: "empty content",
			opts: &MemoryStoreOptions{
				Category: MemoryCategoryFact,
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with empty content")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			item, err := client.Memory.Store(context.Background(), tt.opts)

			if (err != nil) != tt.wantErr {
				t.Errorf("Store() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if item.Content != tt.wantContent {
					t.Errorf("Content = %q, want %q", item.Content, tt.wantContent)
				}
				if item.Category != tt.wantCategory {
					t.Errorf("Category = %v, want %v", item.Category, tt.wantCategory)
				}
				if item.ID == "" {
					t.Error("expected non-empty ID")
				}
			}
		})
	}
}

// TestMemoryRetrieve tests the Memory.Retrieve method.
func TestMemoryRetrieve(t *testing.T) {
	tests := []struct {
		name       string
		memoryID   string
		server     http.HandlerFunc
		wantContent string
		wantErr    bool
	}{
		{
			name:     "successful retrieve",
			memoryID: "mem-123",
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("method = %s, want GET", r.Method)
				}
				if r.URL.Path != "/memory/mem-123" {
					t.Errorf("path = %s, want /memory/mem-123", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(MemoryItem{
					ID:         "mem-123",
					Content:    "A test memory",
					Category:   MemoryCategoryFact,
					Tags:       []string{"test"},
					Importance: 0.5,
					CreatedAt:  time.Now(),
					UpdatedAt:  time.Now(),
				})
			},
			wantContent: "A test memory",
			wantErr:     false,
		},
		{
			name:     "not found",
			memoryID: "nonexistent",
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
						Message: "Memory not found",
					},
				})
			},
			wantErr: true,
		},
		{
			name:     "empty ID",
			memoryID: "",
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
			item, err := client.Memory.Retrieve(context.Background(), tt.memoryID)

			if (err != nil) != tt.wantErr {
				t.Errorf("Retrieve() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if item.Content != tt.wantContent {
					t.Errorf("Content = %q, want %q", item.Content, tt.wantContent)
				}
			}
		})
	}
}

// TestMemorySearch tests the Memory.Search method.
func TestMemorySearch(t *testing.T) {
	tests := []struct {
		name      string
		opts      *MemorySearchOptions
		server    http.HandlerFunc
		wantCount int
		wantErr   bool
	}{
		{
			name: "successful search",
			opts: &MemorySearchOptions{
				Query:   "user preferences",
				Limit:   5,
				AgentID: "agent-1",
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				if r.URL.Path != "/memory/search" {
					t.Errorf("path = %s, want /memory/search", r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]interface{}{
					"results": []MemorySearchResult{
						{
							Item: MemoryItem{
								ID:       "mem-1",
								Content:  "User prefers dark mode",
								Category: MemoryCategoryPreference,
							},
							Score: 0.95,
						},
						{
							Item: MemoryItem{
								ID:       "mem-2",
								Content:  "User likes blue color",
								Category: MemoryCategoryPreference,
							},
							Score: 0.82,
						},
					},
				})
			},
			wantCount: 2,
			wantErr:   false,
		},
		{
			name: "no results",
			opts: &MemorySearchOptions{
				Query: "nonexistent query",
				Limit: 10,
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				json.NewEncoder(w).Encode(map[string]interface{}{
					"results": []MemorySearchResult{},
				})
			},
			wantCount: 0,
			wantErr:   false,
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
			name: "empty query",
			opts: &MemorySearchOptions{
				Limit: 10,
			},
			server: func(w http.ResponseWriter, r *http.Request) {
				t.Error("server should not be called with empty query")
			},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(tt.server))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			results, err := client.Memory.Search(context.Background(), tt.opts)

			if (err != nil) != tt.wantErr {
				t.Errorf("Search() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr {
				if len(results) != tt.wantCount {
					t.Errorf("len(results) = %d, want %d", len(results), tt.wantCount)
				}
				// Verify scores are valid
				for _, r := range results {
					if r.Score < 0 || r.Score > 1 {
						t.Errorf("score = %f, expected between 0 and 1", r.Score)
					}
					if r.Item.ID == "" {
						t.Error("expected non-empty memory item ID")
					}
				}
			}
		})
	}
}

// TestMemoryDelete tests the Memory.Delete method.
func TestMemoryDelete(t *testing.T) {
	tests := []struct {
		name     string
		memID    string
		server   http.HandlerFunc
		wantErr  bool
	}{
		{
			name:  "successful delete",
			memID: "mem-123",
			server: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Errorf("method = %s, want DELETE", r.Method)
				}
				if r.URL.Path != "/memory/mem-123" {
					t.Errorf("path = %s, want /memory/mem-123", r.URL.Path)
				}
				w.WriteHeader(http.StatusNoContent)
			},
			wantErr: false,
		},
		{
			name:  "empty ID",
			memID: "",
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
			err := client.Memory.Delete(context.Background(), tt.memID)

			if (err != nil) != tt.wantErr {
				t.Errorf("Delete() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

// TestMemoryList tests the Memory.List method.
func TestMemoryList(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/memory" {
			t.Errorf("path = %s, want /memory", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(MemoryListResponse{
			PaginatedResponse: PaginatedResponse{
				Page:       1,
				PageSize:   10,
				Total:      50,
				TotalPages: 5,
				HasNext:    true,
				HasPrev:    false,
			},
			Items: []MemoryItem{
				{ID: "mem-1", Content: "Memory 1", Category: MemoryCategoryFact},
				{ID: "mem-2", Content: "Memory 2", Category: MemoryCategoryPreference},
				{ID: "mem-3", Content: "Memory 3", Category: MemoryCategoryExperience},
			},
		})
	}))
	defer server.Close()

	client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
	resp, err := client.Memory.List(context.Background(), &ListOptions{Page: 1, PageSize: 10})

	if err != nil {
		t.Fatalf("List() error = %v", err)
	}

	if len(resp.Items) != 3 {
		t.Errorf("len(Items) = %d, want 3", len(resp.Items))
	}
	if resp.Total != 50 {
		t.Errorf("Total = %d, want 50", resp.Total)
	}
	if !resp.HasNext {
		t.Error("expected HasNext = true")
	}
}

// TestMemoryClear tests the Memory.Clear method.
func TestMemoryClear(t *testing.T) {
	tests := []struct {
		name     string
		agentID  string
		category MemoryCategory
		verify   func(r *http.Request)
		wantErr  bool
	}{
		{
			name:     "clear all",
			agentID:  "",
			category: MemoryCategoryUnknown,
			verify: func(r *http.Request) {
				if r.Method != http.MethodDelete {
					t.Errorf("method = %s, want DELETE", r.Method)
				}
				if r.URL.Path != "/memory/clear" {
					t.Errorf("path = %s, want /memory/clear", r.URL.Path)
				}
			},
			wantErr: false,
		},
		{
			name:     "clear by agent",
			agentID:  "agent-1",
			category: MemoryCategoryUnknown,
			verify: func(r *http.Request) {
				var body map[string]interface{}
				json.NewDecoder(r.Body).Decode(&body)
				if body["agent_id"] != "agent-1" {
					t.Errorf("agent_id = %v, want agent-1", body["agent_id"])
				}
			},
			wantErr: false,
		},
		{
			name:     "clear by category",
			agentID:  "",
			category: MemoryCategoryPreference,
			verify: func(r *http.Request) {
				var body map[string]interface{}
				json.NewDecoder(r.Body).Decode(&body)
				if body["category"] != "preference" {
					t.Errorf("category = %v, want preference", body["category"])
				}
			},
			wantErr: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if tt.verify != nil {
					tt.verify(r)
				}
				w.WriteHeader(http.StatusNoContent)
			}))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
			err := client.Memory.Clear(context.Background(), tt.agentID, tt.category)

			if (err != nil) != tt.wantErr {
				t.Errorf("Clear() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

// TestMemoryCategoryString tests the MemoryCategory String method.
func TestMemoryCategoryString(t *testing.T) {
	tests := []struct {
		cat  MemoryCategory
		want string
	}{
		{MemoryCategoryUnknown, "unknown"},
		{MemoryCategoryFact, "fact"},
		{MemoryCategoryPreference, "preference"},
		{MemoryCategoryExperience, "experience"},
		{MemoryCategoryConversation, "conversation"},
		{MemoryCategoryTask, "task"},
		{MemoryCategoryCustom, "custom"},
	}

	for _, tt := range tests {
		t.Run(tt.want, func(t *testing.T) {
			if got := tt.cat.String(); got != tt.want {
				t.Errorf("String() = %q, want %q", got, tt.want)
			}
		})
	}
}

// TestMemoryCategories tests the Memory.Categories method.
func TestMemoryCategories(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("method = %s, want GET", r.Method)
		}
		if r.URL.Path != "/memory/categories" {
			t.Errorf("path = %s, want /memory/categories", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]interface{}{
			"categories": []string{"fact", "preference", "experience", "conversation", "task", "custom"},
		})
	}))
	defer server.Close()

	client := NewClient(WithBaseURL(server.URL), WithAPIKey("test-key"))
	categories, err := client.Memory.Categories(context.Background())

	if err != nil {
		t.Fatalf("Categories() error = %v", err)
	}

	if len(categories) != 6 {
		t.Errorf("len(categories) = %d, want 6", len(categories))
	}
}
