package liuhao

import (
	"context"
	"fmt"
	"net/http"
)

// MemoryService provides methods for managing agent memory in the LiuHao AI OS.
//
// Use Client.Memory to access this service. The memory service allows agents
// to store, retrieve, and search for memories using both keyword and
// semantic search.
type MemoryService struct {
	client *Client
}

// Store stores a new memory item.
//
// The Content field is required. The memory can optionally be categorized,
// tagged, and associated with a specific agent.
//
// Example:
//
//	memory, err := client.Memory.Store(ctx, &liuhao.MemoryStoreOptions{
//	    Content:    "User prefers dark mode in applications.",
//	    Category:   liuhao.MemoryCategoryPreference,
//	    Tags:       []string{"ui", "preference"},
//	    AgentID:    "agent_abc123",
//	    Importance: 0.8,
//	})
func (s *MemoryService) Store(ctx context.Context, opts *MemoryStoreOptions) (*MemoryItem, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "memory store options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Content == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_content",
				Message:    "memory content is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/memory"

	var result MemoryItem
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Retrieve retrieves a single memory item by its ID.
//
// Returns a NotFoundError if the memory item does not exist.
//
// Example:
//
//	memory, err := client.Memory.Retrieve(ctx, "mem_abc123")
//	if liuhao.IsNotFoundError(err) {
//	    fmt.Println("Memory not found")
//	}
func (s *MemoryService) Retrieve(ctx context.Context, id string) (*MemoryItem, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "memory ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/memory/%s", id)

	var result MemoryItem
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Update updates an existing memory item.
//
// Only the fields that are set in the opts parameter will be updated.
// Returns a NotFoundError if the memory item does not exist.
//
// Example:
//
//	newContent := "Updated memory content"
//	memory, err := client.Memory.Update(ctx, "mem_abc123", &liuhao.MemoryUpdateOptions{
//	    Content: &newContent,
//	})
func (s *MemoryService) Update(ctx context.Context, id string, opts *MemoryUpdateOptions) (*MemoryItem, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "memory ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "memory update options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/memory/%s", id)

	var result MemoryItem
	if err := s.client.request(ctx, http.MethodPatch, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Delete deletes a memory item by its ID.
//
// Returns a NotFoundError if the memory item does not exist.
//
// Example:
//
//	err := client.Memory.Delete(ctx, "mem_abc123")
func (s *MemoryService) Delete(ctx context.Context, id string) error {
	if id == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "memory ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/memory/%s", id)

	if err := s.client.request(ctx, http.MethodDelete, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// Search performs a semantic search across memory items.
//
// The Query field is required for semantic search. Results are returned
// in order of relevance with a similarity score. Use the options to
// filter by category, tags, agent, importance threshold, and more.
//
// Example:
//
//	results, err := client.Memory.Search(ctx, &liuhao.MemorySearchOptions{
//	    Query:        "user preferences for UI",
//	    Category:     liuhao.MemoryCategoryPreference,
//	    AgentID:      "agent_abc123",
//	    MinImportance: 0.5,
//	    Limit:        10,
//	})
//	for _, r := range results {
//	    fmt.Printf("[%.2f] %s\n", r.Score, r.Item.Content)
//	}
func (s *MemoryService) Search(ctx context.Context, opts *MemorySearchOptions) ([]MemorySearchResult, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "memory search options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Query == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_query",
				Message:    "search query is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/memory/search"

	var result struct {
		Results []MemorySearchResult `json:"results"`
	}
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return result.Results, nil
}

// List retrieves a paginated list of memory items.
//
// The opts parameter controls pagination and filtering. Pass nil to use
// defaults. Use the Query field in ListOptions for basic keyword filtering.
//
// Example:
//
//	resp, err := client.Memory.List(ctx, &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	    Query:    "preference",
//	})
func (s *MemoryService) List(ctx context.Context, opts *ListOptions) (*MemoryListResponse, error) {
	path := "/memory"
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result MemoryListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Clear deletes all memory items, optionally filtered by agent or category.
//
// Use with caution as this operation is irreversible. If agentID is
// provided, only memories belonging to that agent are cleared. If
// category is provided, only memories in that category are cleared.
// Both filters can be combined.
//
// Example:
//
//	// Clear all memories for a specific agent
//	err := client.Memory.Clear(ctx, "agent_abc123", liuhao.MemoryCategoryUnknown)
//
//	// Clear all preference memories across all agents
//	err := client.Memory.Clear(ctx, "", liuhao.MemoryCategoryPreference)
//
//	// Clear ALL memories (dangerous!)
//	err := client.Memory.Clear(ctx, "", liuhao.MemoryCategoryUnknown)
func (s *MemoryService) Clear(ctx context.Context, agentID string, category MemoryCategory) error {
	path := "/memory/clear"

	body := map[string]interface{}{}
	if agentID != "" {
		body["agent_id"] = agentID
	}
	if category != MemoryCategoryUnknown {
		body["category"] = category.String()
	}

	var reqBody interface{}
	if len(body) > 0 {
		reqBody = body
	}

	if err := s.client.request(ctx, http.MethodDelete, path, reqBody, nil); err != nil {
		return err
	}
	return nil
}

// Categories returns the list of available memory categories.
//
// This returns all supported memory category types that can be used
// when storing or searching memories.
//
// Example:
//
//	categories, err := client.Memory.Categories(ctx)
//	for _, cat := range categories {
//	    fmt.Println(cat)
//	}
func (s *MemoryService) Categories(ctx context.Context) ([]MemoryCategory, error) {
	path := "/memory/categories"

	var result struct {
		Categories []string `json:"categories"`
	}
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}

	categories := make([]MemoryCategory, 0, len(result.Categories))
	for _, cat := range result.Categories {
		var mc MemoryCategory
		switch cat {
		case "fact":
			mc = MemoryCategoryFact
		case "preference":
			mc = MemoryCategoryPreference
		case "experience":
			mc = MemoryCategoryExperience
		case "conversation":
			mc = MemoryCategoryConversation
		case "task":
			mc = MemoryCategoryTask
		case "custom":
			mc = MemoryCategoryCustom
		default:
			mc = MemoryCategoryUnknown
		}
		categories = append(categories, mc)
	}
	return categories, nil
}
