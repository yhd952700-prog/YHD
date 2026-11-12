package liuhao

import "fmt"

// LiuHaoError is the base error type for all LiuHao API errors.
//
// It contains the error code, human-readable message, and the HTTP
// status code from the response. Use type assertions or the Is* helper
// functions to check for specific error types.
type LiuHaoError struct {
	// Code is the machine-readable error code from the API.
	Code string

	// Message is a human-readable description of the error.
	Message string

	// HTTPStatus is the HTTP status code from the response.
	HTTPStatus int
}

// Error implements the error interface.
func (e *LiuHaoError) Error() string {
	if e.Code != "" {
		return fmt.Sprintf("liuhao: %s (code: %s, status: %d)", e.Message, e.Code, e.HTTPStatus)
	}
	return fmt.Sprintf("liuhao: %s (status: %d)", e.Message, e.HTTPStatus)
}

// AuthenticationError indicates an authentication or authorization failure.
//
// This error is returned when the API key is missing, invalid, expired,
// or does not have sufficient permissions for the requested operation.
// HTTP status codes: 401 (Unauthorized), 403 (Forbidden).
type AuthenticationError struct {
	LiuHaoError
}

// NotFoundError indicates that the requested resource was not found.
//
// This error is returned when the requested entity (agent, memory,
// knowledge base, etc.) does not exist or has been deleted.
// HTTP status code: 404 (Not Found).
type NotFoundError struct {
	LiuHaoError
}

// ValidationError indicates that the request data failed validation.
//
// This error is returned when the request parameters are malformed,
// missing required fields, or contain invalid values.
// HTTP status codes: 400 (Bad Request), 422 (Unprocessable Entity).
type ValidationError struct {
	LiuHaoError
	// Details contains field-specific validation errors.
	Details map[string]interface{}
}

// RateLimitError indicates that the rate limit has been exceeded.
//
// This error is returned when too many requests have been made within
// a given time window. Clients should implement backoff and retry logic.
// HTTP status code: 429 (Too Many Requests).
type RateLimitError struct {
	LiuHaoError
}

// APIError indicates a general API error that doesn't fit other categories.
//
// This includes server-side errors (5xx) and other unexpected responses.
type APIError struct {
	LiuHaoError
}

// TimeoutError indicates that a request timed out.
//
// This can happen when the server takes too long to respond or when
// the client's timeout duration is exceeded.
// HTTP status codes: 408 (Request Timeout), 504 (Gateway Timeout).
type TimeoutError struct {
	LiuHaoError
}

// IsLiuHaoError reports whether err is a LiuHaoError (any subclass).
//
// This function returns true for any error originating from the LiuHao API,
// including authentication, not found, validation, rate limit, and
// other API errors.
func IsLiuHaoError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*LiuHaoError)
	if ok {
		return true
	}
	// Check all sub-types
	_, ok = err.(*AuthenticationError)
	if ok {
		return true
	}
	_, ok = err.(*NotFoundError)
	if ok {
		return true
	}
	_, ok = err.(*ValidationError)
	if ok {
		return true
	}
	_, ok = err.(*RateLimitError)
	if ok {
		return true
	}
	_, ok = err.(*APIError)
	if ok {
		return true
	}
	_, ok = err.(*TimeoutError)
	return ok
}

// IsAuthenticationError reports whether err is an AuthenticationError.
//
// Use this to check if an error was caused by authentication or
// authorization issues (invalid API key, insufficient permissions, etc.).
func IsAuthenticationError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*AuthenticationError)
	return ok
}

// IsNotFoundError reports whether err is a NotFoundError.
//
// Use this to check if an error was caused by a requested resource
// not existing (e.g., agent not found, memory item not found).
func IsNotFoundError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*NotFoundError)
	return ok
}

// IsValidationError reports whether err is a ValidationError.
//
// Use this to check if an error was caused by invalid request data
// (e.g., missing required fields, invalid parameter values).
func IsValidationError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*ValidationError)
	return ok
}

// IsRateLimitError reports whether err is a RateLimitError.
//
// Use this to check if an error was caused by exceeding the API rate
// limit. When this returns true, clients should back off before retrying.
func IsRateLimitError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*RateLimitError)
	return ok
}

// IsAPIError reports whether err is a general APIError.
//
// This returns true for server-side errors (5xx) and other API errors
// that don't fit into more specific categories.
func IsAPIError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*APIError)
	return ok
}

// IsTimeoutError reports whether err is a TimeoutError.
//
// Use this to check if an error was caused by a request timeout,
// either client-side or server-side.
func IsTimeoutError(err error) bool {
	if err == nil {
		return false
	}
	_, ok := err.(*TimeoutError)
	return ok
}
