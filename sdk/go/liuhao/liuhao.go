// Package liuhao provides a Go SDK for interacting with the LiuHao AI OS API.
//
// The LiuHao AI OS SDK offers a clean, idiomatic Go interface for managing
// agents, memory, knowledge bases, tasks, and system health within the
// LiuHao AI operating system.
//
// Basic usage:
//
//	client := liuhao.NewClient(
//	    liuhao.WithAPIKey("your-api-key"),
//	    liuhao.WithBaseURL("https://api.liuhao.ai/v1"),
//	)
//
//	agents, err := client.Agents.List(context.Background(), nil)
package liuhao

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"time"
)

const (
	// defaultBaseURL is the default API endpoint for LiuHao AI OS.
	defaultBaseURL = "https://api.liuhao.ai/v1"

	// defaultTimeout is the default HTTP client timeout.
	defaultTimeout = 30 * time.Second

	// userAgent is the HTTP User-Agent header value.
	userAgent = "liuhao-go-sdk/1.0.0"
)

// Client is the LiuHao AI OS API client.
//
// Use NewClient to create a new client with the desired options.
// The client provides access to all API services through its fields:
//
//   - Agents: Manage AI agents
//   - Memory: Manage agent memory and semantic search
//   - Knowledge: Manage knowledge bases and documents
//   - Tasks: Manage asynchronous tasks
//   - Health: Check system health and readiness
type Client struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client

	// Services
	Agents    *AgentsService
	Memory    *MemoryService
	Knowledge *KnowledgeService
	Tasks     *TasksService
	Health    *HealthService
}

// ClientOption is a functional option for configuring the Client.
type ClientOption func(*Client)

// WithBaseURL sets the base URL for the API client.
//
// Use this to point the client at a different environment (e.g., staging,
// local development). The URL should not include a trailing slash.
func WithBaseURL(baseURL string) ClientOption {
	return func(c *Client) {
		c.baseURL = baseURL
	}
}

// WithAPIKey sets the API key used for authentication.
//
// The API key is sent as a Bearer token in the Authorization header
// for all API requests.
func WithAPIKey(apiKey string) ClientOption {
	return func(c *Client) {
		c.apiKey = apiKey
	}
}

// WithHTTPClient sets a custom HTTP client for the API client.
//
// Use this to configure custom timeouts, transport settings, or
// middleware for HTTP requests.
func WithHTTPClient(httpClient *http.Client) ClientOption {
	return func(c *Client) {
		c.httpClient = httpClient
	}
}

// WithTimeout sets the HTTP request timeout.
//
// This is a convenience option that creates a new http.Client with
// the specified timeout. For more control, use WithHTTPClient.
func WithTimeout(timeout time.Duration) ClientOption {
	return func(c *Client) {
		c.httpClient = &http.Client{
			Timeout: timeout,
		}
	}
}

// NewClient creates a new LiuHao AI OS API client with the given options.
//
// At minimum, an API key should be provided via WithAPIKey. If no base URL
// is specified, the default production URL is used.
//
// Example:
//
//	client := liuhao.NewClient(
//	    liuhao.WithAPIKey("sk-..."),
//	    liuhao.WithBaseURL("https://api.liuhao.ai/v1"),
//	    liuhao.WithTimeout(60*time.Second),
//	)
func NewClient(opts ...ClientOption) *Client {
	c := &Client{
		baseURL: defaultBaseURL,
		httpClient: &http.Client{
			Timeout: defaultTimeout,
		},
	}

	for _, opt := range opts {
		opt(c)
	}

	// Initialize all services
	c.Agents = &AgentsService{client: c}
	c.Memory = &MemoryService{client: c}
	c.Knowledge = &KnowledgeService{client: c}
	c.Tasks = &TasksService{client: c}
	c.Health = &HealthService{client: c}

	return c
}

// request performs an HTTP request and decodes the JSON response.
//
// It handles:
//   - Building the full URL from the base URL and path
//   - Setting common headers (Authorization, Content-Type, User-Agent)
//   - Encoding the request body as JSON
//   - Sending the request with context support
//   - Handling error responses and decoding error types
//   - Decoding successful responses into the provided result type
func (c *Client) request(ctx context.Context, method, path string, body interface{}, result interface{}) error {
	// Build URL
	u, err := url.JoinPath(c.baseURL, path)
	if err != nil {
		return fmt.Errorf("invalid URL path: %w", err)
	}

	// Encode body
	var bodyReader io.Reader
	if body != nil {
		jsonBody, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("failed to marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(jsonBody)
	}

	// Create request
	req, err := http.NewRequestWithContext(ctx, method, u, bodyReader)
	if err != nil {
		return fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", userAgent)
	if c.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+c.apiKey)
	}

	// Execute request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		// Check for context cancellation or deadline
		if ctx.Err() != nil {
			return &LiuHaoError{
				Message:    ctx.Err().Error(),
				HTTPStatus: 0,
			}
		}
		// Check for timeout
		if urlErr, ok := err.(*url.Error); ok && urlErr.Timeout() {
			return &TimeoutError{
				LiuHaoError: LiuHaoError{
					Message:    "request timed out",
					HTTPStatus: 0,
				},
			}
		}
		return fmt.Errorf("request failed: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("failed to read response body: %w", err)
	}

	// Handle error responses
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return c.handleErrorResponse(resp.StatusCode, respBody)
	}

	// Decode success response if result is provided
	if result != nil && len(respBody) > 0 {
		if err := json.Unmarshal(respBody, result); err != nil {
			return fmt.Errorf("failed to decode response: %w", err)
		}
	}

	return nil
}

// handleErrorResponse parses an error response body and returns the
// appropriate typed error.
func (c *Client) handleErrorResponse(statusCode int, body []byte) error {
	var errResp ErrorResponse
	if err := json.Unmarshal(body, &errResp); err != nil {
		// If we can't parse the error body, return a generic API error
		return &APIError{
			LiuHaoError: LiuHaoError{
				Code:       "",
				Message:    string(body),
				HTTPStatus: statusCode,
			},
		}
	}

	baseErr := LiuHaoError{
		Code:       errResp.Error.Code,
		Message:    errResp.Error.Message,
		HTTPStatus: statusCode,
	}

	switch statusCode {
	case http.StatusUnauthorized, http.StatusForbidden:
		return &AuthenticationError{LiuHaoError: baseErr}
	case http.StatusNotFound:
		return &NotFoundError{LiuHaoError: baseErr}
	case http.StatusBadRequest, http.StatusUnprocessableEntity:
		return &ValidationError{LiuHaoError: baseErr, Details: errResp.Error.Details}
	case http.StatusTooManyRequests:
		return &RateLimitError{LiuHaoError: baseErr}
	case http.StatusRequestTimeout, http.StatusGatewayTimeout:
		return &TimeoutError{LiuHaoError: baseErr}
	default:
		return &APIError{LiuHaoError: baseErr}
	}
}

// ListOptions provides pagination and filtering options for list endpoints.
type ListOptions struct {
	// Page is the page number to retrieve (1-indexed).
	Page int `json:"page,omitempty"`

	// PageSize is the number of items to return per page.
	PageSize int `json:"page_size,omitempty"`

	// Sort is the field to sort by.
	Sort string `json:"sort,omitempty"`

	// Order specifies the sort order: "asc" or "desc".
	Order string `json:"order,omitempty"`

	// Query is a search/filter query string.
	Query string `json:"query,omitempty"`
}

// toQueryParams converts ListOptions to URL query parameters.
func (o *ListOptions) toQueryParams() string {
	if o == nil {
		return ""
	}

	params := url.Values{}
	if o.Page > 0 {
		params.Set("page", fmt.Sprintf("%d", o.Page))
	}
	if o.PageSize > 0 {
		params.Set("page_size", fmt.Sprintf("%d", o.PageSize))
	}
	if o.Sort != "" {
		params.Set("sort", o.Sort)
	}
	if o.Order != "" {
		params.Set("order", o.Order)
	}
	if o.Query != "" {
		params.Set("query", o.Query)
	}

	return params.Encode()
}

// addQueryParams appends query parameters to a URL path.
func addQueryParams(path string, query string) string {
	if query == "" {
		return path
	}
	if bytes.ContainsRune([]byte(path), '?') {
		return path + "&" + query
	}
	return path + "?" + query
}
