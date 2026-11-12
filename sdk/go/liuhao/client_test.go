package liuhao

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestNewClient tests the NewClient constructor with various options.
func TestNewClient(t *testing.T) {
	tests := []struct {
		name     string
		opts     []ClientOption
		wantKey  string
		wantURL  string
		wantNil  bool
	}{
		{
			name:    "default client",
			opts:    nil,
			wantKey: "",
			wantURL: defaultBaseURL,
		},
		{
			name:    "with API key",
			opts:    []ClientOption{WithAPIKey("test-key")},
			wantKey: "test-key",
			wantURL: defaultBaseURL,
		},
		{
			name:    "with custom base URL",
			opts:    []ClientOption{WithBaseURL("http://localhost:8080/v1")},
			wantKey: "",
			wantURL: "http://localhost:8080/v1",
		},
		{
			name: "with multiple options",
			opts: []ClientOption{
				WithAPIKey("test-key"),
				WithBaseURL("http://localhost:8080/v1"),
				WithTimeout(60 * time.Second),
			},
			wantKey: "test-key",
			wantURL: "http://localhost:8080/v1",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			client := NewClient(tt.opts...)

			if client == nil {
				t.Fatal("expected non-nil client")
			}
			if client.apiKey != tt.wantKey {
				t.Errorf("apiKey = %q, want %q", client.apiKey, tt.wantKey)
			}
			if client.baseURL != tt.wantURL {
				t.Errorf("baseURL = %q, want %q", client.baseURL, tt.wantURL)
			}
			if client.httpClient == nil {
				t.Error("expected non-nil httpClient")
			}
			// Verify services are initialized
			if client.Agents == nil {
				t.Error("expected non-nil Agents service")
			}
			if client.Memory == nil {
				t.Error("expected non-nil Memory service")
			}
			if client.Knowledge == nil {
				t.Error("expected non-nil Knowledge service")
			}
			if client.Tasks == nil {
				t.Error("expected non-nil Tasks service")
			}
			if client.Health == nil {
				t.Error("expected non-nil Health service")
			}
		})
	}
}

// TestWithHTTPClient tests the WithHTTPClient option.
func TestWithHTTPClient(t *testing.T) {
	customClient := &http.Client{
		Timeout: 45 * time.Second,
	}

	client := NewClient(WithHTTPClient(customClient))

	if client.httpClient != customClient {
		t.Error("expected httpClient to be the custom client")
	}
}

// TestClientRequest tests the request method with a mock server.
func TestClientRequest(t *testing.T) {
	tests := []struct {
		name       string
		method     string
		path       string
		body       interface{}
		handler    http.HandlerFunc
		wantResult map[string]string
		wantErr    bool
		checkAuth  bool
	}{
		{
			name:   "successful GET request",
			method: http.MethodGet,
			path:   "/test",
			handler: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodGet {
					t.Errorf("method = %s, want GET", r.Method)
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(map[string]string{"message": "ok"})
			},
			wantResult: map[string]string{"message": "ok"},
			wantErr:    false,
		},
		{
			name:   "successful POST request with body",
			method: http.MethodPost,
			path:   "/test",
			body:   map[string]string{"key": "value"},
			handler: func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					t.Errorf("method = %s, want POST", r.Method)
				}
				var body map[string]string
				json.NewDecoder(r.Body).Decode(&body)
				if body["key"] != "value" {
					t.Errorf("body key = %q, want %q", body["key"], "value")
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusCreated)
				json.NewEncoder(w).Encode(map[string]string{"id": "123"})
			},
			wantResult: map[string]string{"id": "123"},
			wantErr:    false,
		},
		{
			name: "request with auth header",
			method: http.MethodGet,
			path:   "/test",
			handler: func(w http.ResponseWriter, r *http.Request) {
				auth := r.Header.Get("Authorization")
				if auth != "Bearer test-api-key" {
					t.Errorf("Authorization = %q, want %q", auth, "Bearer test-api-key")
				}
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
			},
			wantResult: map[string]string{"status": "ok"},
			wantErr:    false,
			checkAuth:  true,
		},
		{
			name: "user agent header",
			method: http.MethodGet,
			path:   "/test",
			handler: func(w http.ResponseWriter, r *http.Request) {
				ua := r.Header.Get("User-Agent")
				if ua != userAgent {
					t.Errorf("User-Agent = %q, want %q", ua, userAgent)
				}
				w.WriteHeader(http.StatusOK)
				json.NewEncoder(w).Encode(map[string]string{})
			},
			wantResult: map[string]string{},
			wantErr:    false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(tt.handler)
			defer server.Close()

			opts := []ClientOption{WithBaseURL(server.URL)}
			if tt.checkAuth {
				opts = append(opts, WithAPIKey("test-api-key"))
			}

			client := NewClient(opts...)

			var result map[string]string
			err := client.request(context.Background(), tt.method, tt.path, tt.body, &result)

			if (err != nil) != tt.wantErr {
				t.Errorf("request() error = %v, wantErr %v", err, tt.wantErr)
				return
			}

			if !tt.wantErr && tt.wantResult != nil {
				for k, v := range tt.wantResult {
					if result[k] != v {
						t.Errorf("result[%q] = %q, want %q", k, result[k], v)
					}
				}
			}
		})
	}
}

// TestClientErrorResponses tests error handling for various HTTP error codes.
func TestClientErrorResponses(t *testing.T) {
	tests := []struct {
		name       string
		statusCode int
		errBody    ErrorResponse
		checkFunc  func(error) bool
		errType    string
	}{
		{
			name:       "authentication error (401)",
			statusCode: http.StatusUnauthorized,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "unauthorized",
					Message: "Invalid API key",
				},
			},
			checkFunc: IsAuthenticationError,
			errType:   "AuthenticationError",
		},
		{
			name:       "forbidden error (403)",
			statusCode: http.StatusForbidden,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "forbidden",
					Message: "Insufficient permissions",
				},
			},
			checkFunc: IsAuthenticationError,
			errType:   "AuthenticationError",
		},
		{
			name:       "not found error (404)",
			statusCode: http.StatusNotFound,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "not_found",
					Message: "Resource not found",
				},
			},
			checkFunc: IsNotFoundError,
			errType:   "NotFoundError",
		},
		{
			name:       "validation error (400)",
			statusCode: http.StatusBadRequest,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "validation_error",
					Message: "Invalid request",
					Details: map[string]interface{}{"field": "name is required"},
				},
			},
			checkFunc: IsValidationError,
			errType:   "ValidationError",
		},
		{
			name:       "unprocessable entity (422)",
			statusCode: http.StatusUnprocessableEntity,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "invalid_data",
					Message: "Data validation failed",
				},
			},
			checkFunc: IsValidationError,
			errType:   "ValidationError",
		},
		{
			name:       "rate limit error (429)",
			statusCode: http.StatusTooManyRequests,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "rate_limit_exceeded",
					Message: "Too many requests",
				},
			},
			checkFunc: IsRateLimitError,
			errType:   "RateLimitError",
		},
		{
			name:       "timeout error (408)",
			statusCode: http.StatusRequestTimeout,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "timeout",
					Message: "Request timed out",
				},
			},
			checkFunc: IsTimeoutError,
			errType:   "TimeoutError",
		},
		{
			name:       "server error (500)",
			statusCode: http.StatusInternalServerError,
			errBody: ErrorResponse{
				Error: struct {
					Code    string                 `json:"code"`
					Message string                 `json:"message"`
					Details map[string]interface{} `json:"details,omitempty"`
				}{
					Code:    "internal_error",
					Message: "Internal server error",
				},
			},
			checkFunc: IsAPIError,
			errType:   "APIError",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(tt.statusCode)
				json.NewEncoder(w).Encode(tt.errBody)
			}))
			defer server.Close()

			client := NewClient(WithBaseURL(server.URL))

			var result map[string]interface{}
			err := client.request(context.Background(), http.MethodGet, "/test", nil, &result)

			if err == nil {
				t.Fatal("expected error, got nil")
			}

			if !tt.checkFunc(err) {
				t.Errorf("error type check failed, expected %s", tt.errType)
			}

			// All should also pass IsLiuHaoError
			if !IsLiuHaoError(err) {
				t.Error("expected error to pass IsLiuHaoError check")
			}

			// Verify error message format
			errMsg := err.Error()
			if errMsg == "" {
				t.Error("expected non-empty error message")
			}
		})
	}
}

// TestListOptionsToQueryParams tests the toQueryParams method.
func TestListOptionsToQueryParams(t *testing.T) {
	tests := []struct {
		name string
		opts *ListOptions
		want map[string]string
	}{
		{
			name: "nil options",
			opts: nil,
			want: map[string]string{},
		},
		{
			name: "all fields set",
			opts: &ListOptions{
				Page:     2,
				PageSize: 50,
				Sort:     "created_at",
				Order:    "desc",
				Query:    "test",
			},
			want: map[string]string{
				"page":      "2",
				"page_size": "50",
				"sort":      "created_at",
				"order":     "desc",
				"query":     "test",
			},
		},
		{
			name: "partial fields",
			opts: &ListOptions{
				Page:     1,
				PageSize: 20,
			},
			want: map[string]string{
				"page":      "1",
				"page_size": "20",
			},
		},
		{
			name: "empty options struct",
			opts: &ListOptions{},
			want: map[string]string{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			query := tt.opts.toQueryParams()
			// Just verify it doesn't panic and produces a valid string
			if tt.opts == nil || (tt.opts.Page == 0 && tt.opts.PageSize == 0 && tt.opts.Sort == "" && tt.opts.Order == "" && tt.opts.Query == "") {
				if query != "" {
					t.Errorf("expected empty query string, got %q", query)
				}
			} else {
				if query == "" {
					t.Error("expected non-empty query string")
				}
			}
		})
	}
}

// TestErrorHelperFunctions tests the error type checking helper functions.
func TestErrorHelperFunctions(t *testing.T) {
	tests := []struct {
		name      string
		err       error
		isLiuHao  bool
		isAuth    bool
		isNotFound bool
		isValidation bool
		isRateLimit bool
		isAPI     bool
		isTimeout bool
	}{
		{
			name: "nil error",
			err:  nil,
		},
		{
			name:     "AuthenticationError",
			err:      &AuthenticationError{LiuHaoError{Code: "auth", Message: "auth failed", HTTPStatus: 401}},
			isLiuHao: true,
			isAuth:   true,
		},
		{
			name:       "NotFoundError",
			err:        &NotFoundError{LiuHaoError{Code: "nf", Message: "not found", HTTPStatus: 404}},
			isLiuHao:   true,
			isNotFound: true,
		},
		{
			name:         "ValidationError",
			err:          &ValidationError{LiuHaoError: LiuHaoError{Code: "val", Message: "bad request", HTTPStatus: 400}},
			isLiuHao:     true,
			isValidation: true,
		},
		{
			name:        "RateLimitError",
			err:         &RateLimitError{LiuHaoError{Code: "rl", Message: "rate limited", HTTPStatus: 429}},
			isLiuHao:    true,
			isRateLimit: true,
		},
		{
			name:    "APIError",
			err:     &APIError{LiuHaoError{Code: "api", Message: "server error", HTTPStatus: 500}},
			isLiuHao: true,
			isAPI:   true,
		},
		{
			name:      "TimeoutError",
			err:       &TimeoutError{LiuHaoError{Code: "to", Message: "timeout", HTTPStatus: 408}},
			isLiuHao:  true,
			isTimeout: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := IsLiuHaoError(tt.err); got != tt.isLiuHao {
				t.Errorf("IsLiuHaoError() = %v, want %v", got, tt.isLiuHao)
			}
			if got := IsAuthenticationError(tt.err); got != tt.isAuth {
				t.Errorf("IsAuthenticationError() = %v, want %v", got, tt.isAuth)
			}
			if got := IsNotFoundError(tt.err); got != tt.isNotFound {
				t.Errorf("IsNotFoundError() = %v, want %v", got, tt.isNotFound)
			}
			if got := IsValidationError(tt.err); got != tt.isValidation {
				t.Errorf("IsValidationError() = %v, want %v", got, tt.isValidation)
			}
			if got := IsRateLimitError(tt.err); got != tt.isRateLimit {
				t.Errorf("IsRateLimitError() = %v, want %v", got, tt.isRateLimit)
			}
			if got := IsAPIError(tt.err); got != tt.isAPI {
				t.Errorf("IsAPIError() = %v, want %v", got, tt.isAPI)
			}
			if got := IsTimeoutError(tt.err); got != tt.isTimeout {
				t.Errorf("IsTimeoutError() = %v, want %v", got, tt.isTimeout)
			}
		})
	}
}

// TestContextCancellation tests that request respects context cancellation.
func TestContextCancellation(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Block until the request is cancelled
		time.Sleep(500 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	client := NewClient(WithBaseURL(server.URL))

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Cancel immediately

	var result map[string]interface{}
	err := client.request(ctx, http.MethodGet, "/test", nil, &result)

	if err == nil {
		t.Error("expected error due to cancelled context, got nil")
	}
}
