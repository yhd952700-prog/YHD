using System.Net;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using LiuHao.Sdk.Models;
using LiuHao.Sdk.Resources;

namespace LiuHao.Sdk;

/// <summary>
/// Main client for the LiuHao AI OS platform.
/// </summary>
/// <remarks>
/// <para>
/// Provides a unified, resource-oriented interface to all LiuHao API
/// resources, with built-in Bearer-token authentication, JSON
/// serialization, error mapping, and exponential-backoff retries.
/// </para>
/// <example>
/// <code>
/// using var client = new LiuHaoClient("your-api-key");
/// var agents = await client.Agents.ListAsync();
/// var run = await client.Agents.ExecuteAsync("agent-id", "Hello, world!");
/// </code>
/// </example>
/// </remarks>
public sealed class LiuHaoClient : IDisposable, IAsyncDisposable
{
    /// <summary>
    /// The SDK version sent in the <c>User-Agent</c> header.
    /// </summary>
    public const string SdkVersion = "1.0.0";

    private const string UserAgent = "LiuHao-SDK-DotNet/" + SdkVersion;

    private readonly HttpClient _httpClient;
    private readonly bool _ownsHttpClient;
    private readonly JsonSerializerOptions _jsonOptions;
    private bool _disposed;

    /// <summary>
    /// Gets the active SDK configuration.
    /// </summary>
    public LiuHaoConfig Config { get; }

    /// <summary>
    /// Gets the agent management resource.
    /// </summary>
    public AgentResource Agents { get; }

    /// <summary>
    /// Gets the memory operations resource.
    /// </summary>
    public MemoryResource Memory { get; }

    /// <summary>
    /// Gets the knowledge base management resource.
    /// </summary>
    public KnowledgeResource Knowledge { get; }

    /// <summary>
    /// Initializes a new instance of the <see cref="LiuHaoClient"/> class.
    /// </summary>
    /// <param name="apiKey">
    /// API key for authentication. Overrides the value from
    /// <see cref="LiuHaoConfig.FromEnvironment"/>.
    /// </param>
    /// <param name="baseUrl">
    /// Optional base API URL override. Overrides configuration and
    /// environment values when provided.
    /// </param>
    /// <param name="config">
    /// Optional pre-built configuration. When omitted, configuration is
    /// loaded from environment variables prefixed with <c>LIUHAO_</c>.
    /// </param>
    /// <param name="httpClient">
    /// Optional externally managed <see cref="HttpClient"/>. When provided,
    /// the SDK does not dispose it.
    /// </param>
    /// <remarks>
    /// Configuration precedence (highest first):
    /// <list type="number">
    ///   <item>Explicit constructor arguments</item>
    ///   <item>The <paramref name="config"/> instance</item>
    ///   <item>Environment variables (<c>LIUHAO_*</c>)</item>
    ///   <item>Default values</item>
    /// </list>
    /// </remarks>
    public LiuHaoClient(
        string? apiKey = null,
        string? baseUrl = null,
        LiuHaoConfig? config = null,
        HttpClient? httpClient = null)
    {
        Config = (config ?? LiuHaoConfig.FromEnvironment()).Clone();

        if (!string.IsNullOrEmpty(apiKey))
        {
            Config.ApiKey = apiKey;
        }

        if (!string.IsNullOrEmpty(baseUrl))
        {
            Config.BaseUrl = baseUrl;
        }

        _jsonOptions = LiuHaoJsonSerializerOptions.Default;

        if (httpClient is not null)
        {
            _httpClient = httpClient;
            _ownsHttpClient = false;
        }
        else
        {
            _httpClient = CreateHttpClient(Config);
            _ownsHttpClient = true;
        }

        Agents = new AgentResource(this);
        Memory = new MemoryResource(this);
        Knowledge = new KnowledgeResource(this);
    }

    // ------------------------------------------------------------------
    // HTTP verbs
    // ------------------------------------------------------------------

    /// <summary>
    /// Performs an HTTP GET request and deserializes the JSON response.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="path">API endpoint path, relative to the base URL.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional request headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    /// <exception cref="AuthenticationException">Authentication failed.</exception>
    /// <exception cref="NotFoundException">Resource not found.</exception>
    /// <exception cref="RateLimitException">Rate limit exceeded.</exception>
    /// <exception cref="ValidationException">Request validation failed.</exception>
    /// <exception cref="ApiException">Other API errors.</exception>
    public Task<T> GetAsync<T>(
        string path,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
        => SendAsync<T>(HttpMethod.Get, path, null, queryParams, headers, cancellationToken);

    /// <summary>
    /// Performs an HTTP POST request with an optional JSON body.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="path">API endpoint path.</param>
    /// <param name="body">Optional request body, serialized as JSON.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional request headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    public Task<T> PostAsync<T>(
        string path,
        object? body = null,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
        => SendAsync<T>(HttpMethod.Post, path, body, queryParams, headers, cancellationToken);

    /// <summary>
    /// Performs an HTTP PUT request with an optional JSON body.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="path">API endpoint path.</param>
    /// <param name="body">Optional request body, serialized as JSON.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional request headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    public Task<T> PutAsync<T>(
        string path,
        object? body = null,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
        => SendAsync<T>(HttpMethod.Put, path, body, queryParams, headers, cancellationToken);

    /// <summary>
    /// Performs an HTTP PATCH request with an optional JSON body.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="path">API endpoint path.</param>
    /// <param name="body">Optional request body, serialized as JSON.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional request headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    public Task<T> PatchAsync<T>(
        string path,
        object? body = null,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
        => SendAsync<T>(HttpMethod.Patch, path, body, queryParams, headers, cancellationToken);

    /// <summary>
    /// Performs an HTTP DELETE request.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="path">API endpoint path.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional request headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    public Task<T> DeleteAsync<T>(
        string path,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
        => SendAsync<T>(HttpMethod.Delete, path, null, queryParams, headers, cancellationToken);

    // ------------------------------------------------------------------
    // Core request pipeline
    // ------------------------------------------------------------------

    /// <summary>
    /// Sends an HTTP request with retry logic, authentication, and
    /// error mapping, then deserializes the JSON response.
    /// </summary>
    /// <typeparam name="T">The response payload type.</typeparam>
    /// <param name="method">The HTTP method.</param>
    /// <param name="path">API endpoint path, relative to the base URL.</param>
    /// <param name="body">Optional JSON-serializable request body.</param>
    /// <param name="queryParams">Optional query parameters.</param>
    /// <param name="headers">Optional additional headers.</param>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The deserialized response payload.</returns>
    /// <exception cref="LiuHaoException">
    /// One of the mapped SDK exceptions for any non-success status code.
    /// </exception>
    public async Task<T> SendAsync<T>(
        HttpMethod method,
        string path,
        object? body = null,
        IReadOnlyDictionary<string, string?>? queryParams = null,
        IReadOnlyDictionary<string, string>? headers = null,
        CancellationToken cancellationToken = default)
    {
        if (_disposed)
        {
            throw new ObjectDisposedException(nameof(LiuHaoClient));
        }

        var url = BuildUrl(path, queryParams);
        var maxRetries = Config.MaxRetries;
        LiuHaoException? lastException = null;

        for (var attempt = 0; attempt <= maxRetries; attempt++)
        {
            try
            {
                using var request = BuildRequest(method, url, body, headers);
                using var response = await _httpClient
                    .SendAsync(request, HttpCompletionOption.ResponseHeadersRead, cancellationToken)
                    .ConfigureAwait(false);

                var statusCode = (int)response.StatusCode;
                var content = response.Content is null
                    ? null
                    : await response.Content.ReadAsStringAsync(cancellationToken)
                        .ConfigureAwait(false);

                if (ExceptionMapper.IsSuccess(statusCode))
                {
                    return Deserialize<T>(content);
                }

                var exception = MapError(response, statusCode, content);

                if (!ShouldRetry(statusCode) || attempt >= maxRetries)
                {
                    throw exception;
                }

                lastException = exception;
                await DelayAsync(attempt, exception as RateLimitException, cancellationToken)
                    .ConfigureAwait(false);
            }
            catch (TaskCanceledException ex) when (!cancellationToken.IsCancellationRequested)
            {
                lastException = new LiuHaoTimeoutException(
                    timeout: Config.Timeout,
                    innerException: ex);

                if (attempt >= maxRetries)
                {
                    throw lastException;
                }

                await DelayAsync(attempt, null, cancellationToken).ConfigureAwait(false);
            }
            catch (HttpRequestException ex)
            {
                lastException = new ApiException(
                    message: $"Request failed: {ex.Message}",
                    code: "request_error",
                    innerException: ex);

                if (attempt >= maxRetries)
                {
                    throw lastException;
                }

                await DelayAsync(attempt, null, cancellationToken).ConfigureAwait(false);
            }
        }

        throw lastException ?? new ApiException(message: "Unknown request error.");
    }

    // ------------------------------------------------------------------
    // Diagnostics helpers
    // ------------------------------------------------------------------

    /// <summary>
    /// Checks the health of the LiuHao API.
    /// </summary>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>The platform health status.</returns>
    public Task<HealthCheck> HealthCheckAsync(
        CancellationToken cancellationToken = default)
        => GetAsync<HealthCheck>("/health", cancellationToken: cancellationToken);

    /// <summary>
    /// Gets API version and platform information.
    /// </summary>
    /// <param name="cancellationToken">Cancellation token.</param>
    /// <returns>API info including version, build, and feature flags.</returns>
    public Task<ApiInfo> GetApiInfoAsync(
        CancellationToken cancellationToken = default)
        => GetAsync<ApiInfo>("/info", cancellationToken: cancellationToken);

    // ------------------------------------------------------------------
    // Internal helpers
    // ------------------------------------------------------------------

    internal string BuildUrl(
        string path,
        IReadOnlyDictionary<string, string?>? queryParams)
    {
        var baseUrl = Config.BaseUrl.TrimEnd('/');
        var relative = path.TrimStart('/');
        var builder = new StringBuilder($"{baseUrl}/{relative}");

        if (queryParams is not null && queryParams.Count > 0)
        {
            var first = true;
            foreach (var (key, value) in queryParams)
            {
                if (value is null)
                {
                    continue;
                }

                builder.Append(first ? '?' : '&');
                first = false;
                builder.Append(Uri.EscapeDataString(key));
                builder.Append('=');
                builder.Append(Uri.EscapeDataString(value));
            }
        }

        return builder.ToString();
    }

    private HttpRequestMessage BuildRequest(
        HttpMethod method,
        string url,
        object? body,
        IReadOnlyDictionary<string, string>? headers)
    {
        var request = new HttpRequestMessage(method, url);

        request.Headers.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        request.Headers.UserAgent.ParseAdd(UserAgent);

        if (!string.IsNullOrEmpty(Config.ApiKey))
        {
            request.Headers.Authorization =
                new AuthenticationHeaderValue("Bearer", Config.ApiKey);
        }

        if (!string.IsNullOrEmpty(Config.OrganizationId))
        {
            request.Headers.TryAddWithoutValidation("X-Organization-ID", Config.OrganizationId);
        }

        if (!string.IsNullOrEmpty(Config.ProjectId))
        {
            request.Headers.TryAddWithoutValidation("X-Project-ID", Config.ProjectId);
        }

        foreach (var (key, value) in Config.CustomHeaders)
        {
            request.Headers.TryAddWithoutValidation(key, value);
        }

        if (headers is not null)
        {
            foreach (var (key, value) in headers)
            {
                request.Headers.TryAddWithoutValidation(key, value);
            }
        }

        if (body is not null)
        {
            var json = JsonSerializer.Serialize(body, _jsonOptions);
            request.Content = new StringContent(json, Encoding.UTF8, "application/json");
        }

        return request;
    }

    private T Deserialize<T>(string? content)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            if (default(T) is null)
            {
                return default!;
            }

            throw new ApiException(
                message: "The API returned an empty response body.",
                code: "empty_response");
        }

        try
        {
            var result = JsonSerializer.Deserialize<T>(content, _jsonOptions);
            if (result is null && default(T) is not null)
            {
                throw new ApiException(
                    message: "The API response deserialized to null.",
                    code: "deserialization_error");
            }

            return result!;
        }
        catch (JsonException ex)
        {
            throw new ApiException(
                message: $"Failed to deserialize API response: {ex.Message}",
                code: "deserialization_error",
                innerException: ex);
        }
    }

    private LiuHaoException MapError(
        HttpResponseMessage response,
        int statusCode,
        string? content)
    {
        string? requestId = null;
        if (response.Headers.TryGetValues("X-Request-ID", out var values))
        {
            requestId = values.FirstOrDefault();
        }

        string? code = null;
        string? message = null;
        IReadOnlyDictionary<string, object?>? details = null;

        if (!string.IsNullOrWhiteSpace(content))
        {
            try
            {
                var error = JsonSerializer.Deserialize<ErrorResponse>(content, _jsonOptions);
                code = error?.Error?.Code;
                message = error?.Error?.Message;
                details = error?.Error?.Details;
            }
            catch (JsonException)
            {
                // Body is not a structured error payload; fall through.
            }
        }

        // Merge Retry-After header into details for RateLimitException.
        if (statusCode == 429
            && response.Headers.TryGetValues("Retry-After", out var retryValues)
            && double.TryParse(retryValues.FirstOrDefault(), out var retrySeconds))
        {
            var merged = details is null
                ? new Dictionary<string, object?>()
                : new Dictionary<string, object?>(details);
            merged["retry_after"] = retrySeconds;
            details = merged;
        }

        return ExceptionMapper.FromStatusCode(
            statusCode, message, code, requestId, details)
            ?? new ApiException(message: $"HTTP {statusCode} error",
                statusCode: statusCode,
                requestId: requestId);
    }

    private bool ShouldRetry(int statusCode) =>
        Config.RetryStatusCodes.Contains(statusCode);

    private Task DelayAsync(
        int attempt,
        RateLimitException? rateLimit,
        CancellationToken cancellationToken)
    {
        var delay = rateLimit?.RetryAfter
            ?? TimeSpan.FromSeconds(Config.RetryBackoffFactor * Math.Pow(2, attempt));
        return Task.Delay(delay, cancellationToken);
    }

    private static HttpClient CreateHttpClient(LiuHaoConfig config)
    {
        var handler = new HttpClientHandler();

        if (!config.VerifySsl)
        {
            handler.ServerCertificateCustomValidationCallback =
                HttpClientHandler.DangerousAcceptAnyServerCertificateValidator;
        }

        if (!string.IsNullOrEmpty(config.Proxy)
            && Uri.TryCreate(config.Proxy, UriKind.Absolute, out var proxyUri))
        {
            handler.Proxy = new WebProxy(proxyUri);
            handler.UseProxy = true;
        }

        var client = new HttpClient(handler)
        {
            Timeout = config.Timeout,
        };

        return client;
    }

    // ------------------------------------------------------------------
    // Dispose pattern
    // ------------------------------------------------------------------

    /// <inheritdoc/>
    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }

        if (_ownsHttpClient)
        {
            _httpClient.Dispose();
        }

        _disposed = true;
        GC.SuppressFinalize(this);
    }

    /// <inheritdoc/>
    public ValueTask DisposeAsync()
    {
        Dispose();
        return ValueTask.CompletedTask;
    }
}
