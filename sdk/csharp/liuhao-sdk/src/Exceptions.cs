namespace LiuHao.Sdk;

/// <summary>
/// Base exception class for all LiuHao SDK errors.
/// </summary>
/// <remarks>
/// All custom SDK exceptions inherit from this class, allowing callers to
/// catch every SDK-related error with a single <c>catch</c> clause.
/// </remarks>
public class LiuHaoException : Exception
{
    /// <summary>
    /// Gets the error code returned by the API, if any.
    /// </summary>
    public string? Code { get; }

    /// <summary>
    /// Gets the unique request identifier for debugging, if provided
    /// by the API (via the <c>X-Request-ID</c> response header).
    /// </summary>
    public string? RequestId { get; }

    /// <summary>
    /// Gets the HTTP status code of the failed response, if available.
    /// </summary>
    public int? StatusCode { get; }

    /// <summary>
    /// Gets additional structured error details returned by the API.
    /// </summary>
    public IReadOnlyDictionary<string, object?> Details { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="LiuHaoException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="innerException">Optional inner exception.</param>
    public LiuHaoException(
        string message,
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        Exception? innerException = null)
        : base(message, innerException)
    {
        Code = code;
        RequestId = requestId;
        StatusCode = statusCode;
        Details = details ?? new Dictionary<string, object?>();
    }

    /// <inheritdoc/>
    public override string ToString()
    {
        var parts = new List<string> { Message };
        if (!string.IsNullOrEmpty(Code))
        {
            parts.Add($"(code: {Code})");
        }

        if (StatusCode.HasValue)
        {
            parts.Add($"(status: {StatusCode.Value})");
        }

        if (!string.IsNullOrEmpty(RequestId))
        {
            parts.Add($"[request_id: {RequestId}]");
        }

        return $"{GetType().Name}: {string.Join(' ', parts)}";
    }
}

/// <summary>
/// Raised when authentication fails.
/// </summary>
/// <remarks>
/// This typically indicates an invalid API key, missing credentials, or an
/// expired token. Associated HTTP status codes: 401, 403.
/// </remarks>
public class AuthenticationException : LiuHaoException
{
    /// <summary>
    /// Initializes a new instance of the <see cref="AuthenticationException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    public AuthenticationException(
        string message = "Authentication failed. Please check your API key.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null)
        : base(message, code, requestId, statusCode, details)
    {
    }
}

/// <summary>
/// Raised when a requested resource is not found.
/// </summary>
/// <remarks>
/// This typically indicates that the entity (agent, memory entry, knowledge
/// base, etc.) does not exist or has been deleted.
/// Associated HTTP status code: 404.
/// </remarks>
public class NotFoundException : LiuHaoException
{
    /// <summary>
    /// Gets the type of resource that was not found, if known
    /// (e.g. "agent", "memory", "knowledge_base").
    /// </summary>
    public string? ResourceType { get; }

    /// <summary>
    /// Gets the identifier of the resource that was not found, if known.
    /// </summary>
    public string? ResourceId { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="NotFoundException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="resourceType">Optional resource type.</param>
    /// <param name="resourceId">Optional resource identifier.</param>
    public NotFoundException(
        string message = "The requested resource was not found.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        string? resourceType = null,
        string? resourceId = null)
        : base(message, code, requestId, statusCode, details)
    {
        ResourceType = resourceType;
        ResourceId = resourceId;
    }
}

/// <summary>
/// Raised when the API rate limit is exceeded.
/// </summary>
/// <remarks>
/// Callers should honor <see cref="RetryAfter"/> and implement exponential
/// backoff when catching this exception. Associated HTTP status code: 429.
/// </remarks>
public class RateLimitException : LiuHaoException
{
    /// <summary>
    /// Gets the suggested delay before retrying the request, if the API
    /// provided one (via <c>Retry-After</c> or error details).
    /// </summary>
    public TimeSpan? RetryAfter { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="RateLimitException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="retryAfter">Optional suggested retry delay.</param>
    public RateLimitException(
        string message = "Rate limit exceeded. Please reduce your request frequency.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        TimeSpan? retryAfter = null)
        : base(message, code, requestId, statusCode, details)
    {
        RetryAfter = retryAfter;
    }
}

/// <summary>
/// Raised when request data fails server-side validation.
/// </summary>
/// <remarks>
/// This typically indicates malformed input, missing required fields, or data
/// that doesn't conform to the expected schema.
/// Associated HTTP status codes: 400, 422.
/// </remarks>
public class ValidationException : LiuHaoException
{
    /// <summary>
    /// Gets per-field validation errors keyed by field name, if provided.
    /// </summary>
    public IReadOnlyDictionary<string, string> FieldErrors { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="ValidationException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="fieldErrors">Optional per-field validation errors.</param>
    public ValidationException(
        string message = "Request validation failed.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        IReadOnlyDictionary<string, string>? fieldErrors = null)
        : base(message, code, requestId, statusCode, details)
    {
        FieldErrors = fieldErrors ?? new Dictionary<string, string>();
    }
}

/// <summary>
/// Raised when a request times out.
/// </summary>
/// <remarks>
/// This indicates that the server did not respond within the configured
/// timeout period. The request may or may not have been processed.
/// </remarks>
public class LiuHaoTimeoutException : LiuHaoException
{
    /// <summary>
    /// Gets the timeout duration that was exceeded, if known.
    /// </summary>
    public TimeSpan? Timeout { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="LiuHaoTimeoutException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="timeout">Optional timeout that was exceeded.</param>
    /// <param name="innerException">Optional inner exception.</param>
    public LiuHaoTimeoutException(
        string message = "Request timed out. The server did not respond in time.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        TimeSpan? timeout = null,
        Exception? innerException = null)
        : base(message, code, requestId, statusCode, details, innerException)
    {
        Timeout = timeout;
    }
}

/// <summary>
/// Raised when the API returns an unexpected error response that does not
/// fit a more specific exception type.
/// </summary>
/// <remarks>
/// This is a catch-all for 5xx server errors and other unmapped status codes.
/// </remarks>
public class ApiException : LiuHaoException
{
    /// <summary>
    /// Initializes a new instance of the <see cref="ApiException"/> class.
    /// </summary>
    /// <param name="message">Human-readable error description.</param>
    /// <param name="code">Optional error code from the API.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="statusCode">Optional HTTP status code.</param>
    /// <param name="details">Optional structured error details.</param>
    /// <param name="innerException">Optional inner exception.</param>
    public ApiException(
        string message = "The API returned an unexpected error.",
        string? code = null,
        string? requestId = null,
        int? statusCode = null,
        IReadOnlyDictionary<string, object?>? details = null,
        Exception? innerException = null)
        : base(message, code, requestId, statusCode, details, innerException)
    {
    }
}

/// <summary>
/// Maps HTTP status codes and API error payloads to the appropriate
/// SDK exception types.
/// </summary>
public static class ExceptionMapper
{
    /// <summary>
    /// Determines whether the given HTTP status code represents a success.
    /// </summary>
    /// <param name="statusCode">The HTTP status code.</param>
    /// <returns>
    /// <see langword="true"/> if the status code is in the 2xx range.
    /// </returns>
    public static bool IsSuccess(int statusCode) =>
        statusCode >= 200 && statusCode < 300;

    /// <summary>
    /// Creates the appropriate exception for the given HTTP status code.
    /// </summary>
    /// <param name="statusCode">HTTP response status code.</param>
    /// <param name="message">Optional custom error message.</param>
    /// <param name="code">Optional error code from the API response.</param>
    /// <param name="requestId">Optional request identifier.</param>
    /// <param name="details">Optional additional error details.</param>
    /// <returns>
    /// The exception instance matching the status code, or
    /// <see langword="null"/> if the status code indicates success.
    /// </returns>
    public static LiuHaoException? FromStatusCode(
        int statusCode,
        string? message = null,
        string? code = null,
        string? requestId = null,
        IReadOnlyDictionary<string, object?>? details = null)
    {
        if (IsSuccess(statusCode))
        {
            return null;
        }

        message ??= $"HTTP {statusCode} error";

        return statusCode switch
        {
            400 or 422 => new ValidationException(
                message, code, requestId, statusCode, details,
                ExtractFieldErrors(details)),
            401 or 403 => new AuthenticationException(
                message, code, requestId, statusCode, details),
            404 => new NotFoundException(
                message, code, requestId, statusCode, details,
                ExtractString(details, "resource_type"),
                ExtractString(details, "resource_id")),
            429 => new RateLimitException(
                message, code, requestId, statusCode, details,
                ExtractRetryAfter(details)),
            _ => new ApiException(message, code, requestId, statusCode, details),
        };
    }

    private static string? ExtractString(
        IReadOnlyDictionary<string, object?>? details, string key)
    {
        if (details is not null
            && details.TryGetValue(key, out var value)
            && value is string s)
        {
            return s;
        }

        return null;
    }

    private static TimeSpan? ExtractRetryAfter(
        IReadOnlyDictionary<string, object?>? details)
    {
        if (details is not null
            && details.TryGetValue("retry_after", out var value))
        {
            if (value is double seconds)
            {
                return TimeSpan.FromSeconds(seconds);
            }

            if (value is int intSeconds)
            {
                return TimeSpan.FromSeconds(intSeconds);
            }

            if (value is string s
                && double.TryParse(s, out var parsed))
            {
                return TimeSpan.FromSeconds(parsed);
            }
        }

        return null;
    }

    private static IReadOnlyDictionary<string, string>? ExtractFieldErrors(
        IReadOnlyDictionary<string, object?>? details)
    {
        if (details is null
            || !details.TryGetValue("field_errors", out var value))
        {
            return null;
        }

        if (value is IReadOnlyDictionary<string, string> typed)
        {
            return typed;
        }

        if (value is IDictionary<string, object?> dict)
        {
            var result = new Dictionary<string, string>();
            foreach (var kvp in dict)
            {
                result[kvp.Key] = kvp.Value?.ToString() ?? string.Empty;
            }

            return result;
        }

        return null;
    }
}
