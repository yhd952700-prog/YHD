namespace LiuHao.Sdk;

/// <summary>
/// Configuration for the LiuHao AI OS SDK client.
/// </summary>
/// <remarks>
/// <para>
/// All settings can be configured programmatically, via environment variables
/// (prefixed with <c>LIUHAO_</c>), or by passing a pre-built instance to
/// <see cref="LiuHaoClient"/>.
/// </para>
/// <para>Environment variable mapping:</para>
/// <list type="bullet">
///   <item><c>LIUHAO_API_KEY</c> → <see cref="ApiKey"/></item>
///   <item><c>LIUHAO_BASE_URL</c> → <see cref="BaseUrl"/></item>
///   <item><c>LIUHAO_TIMEOUT</c> → <see cref="Timeout"/> (seconds)</item>
///   <item><c>LIUHAO_MAX_RETRIES</c> → <see cref="MaxRetries"/></item>
///   <item><c>LIUHAO_DEBUG</c> → <see cref="Debug"/> (true/false)</item>
///   <item><c>LIUHAO_ORGANIZATION_ID</c> → <see cref="OrganizationId"/></item>
///   <item><c>LIUHAO_PROJECT_ID</c> → <see cref="ProjectId"/></item>
/// </list>
/// </remarks>
public sealed class LiuHaoConfig
{
    /// <summary>
    /// The default base URL of the LiuHao API endpoint.
    /// </summary>
    public const string DefaultBaseUrl = "https://api.liuhao.ai/v1";

    /// <summary>
    /// Environment variable prefix used by <see cref="FromEnvironment"/>.
    /// </summary>
    public const string EnvPrefix = "LIUHAO_";

    /// <summary>
    /// Gets or sets the API key used for Bearer-token authentication
    /// with the LiuHao platform.
    /// </summary>
    public string? ApiKey { get; set; }

    /// <summary>
    /// Gets or sets the base URL of the LiuHao API endpoint.
    /// Defaults to <see cref="DefaultBaseUrl"/>.
    /// </summary>
    public string BaseUrl { get; set; } = DefaultBaseUrl;

    /// <summary>
    /// Gets or sets the per-request timeout.
    /// Defaults to 30 seconds.
    /// </summary>
    public TimeSpan Timeout { get; set; } = TimeSpan.FromSeconds(30);

    /// <summary>
    /// Gets or sets the maximum number of retry attempts for failed
    /// (transient) requests. Defaults to 3.
    /// </summary>
    public int MaxRetries { get; set; } = 3;

    /// <summary>
    /// Gets or sets the exponential backoff factor (in seconds) applied
    /// between retry attempts. The delay for attempt <c>n</c> is
    /// <c>RetryBackoffFactor * 2^n</c> seconds. Defaults to 0.5 seconds.
    /// </summary>
    public double RetryBackoffFactor { get; set; } = 0.5;

    /// <summary>
    /// Gets or sets the HTTP status codes that trigger an automatic retry.
    /// Defaults to 429, 500, 502, 503 and 504.
    /// </summary>
    public ISet<int> RetryStatusCodes { get; set; } =
        new HashSet<int> { 429, 500, 502, 503, 504 };

    /// <summary>
    /// Gets or sets a value indicating whether debug logging is enabled.
    /// When enabled, request/response details are written to the configured
    /// logger. Defaults to <see langword="false"/>.
    /// </summary>
    public bool Debug { get; set; }

    /// <summary>
    /// Gets or sets a value indicating whether SSL certificates are verified.
    /// Defaults to <see langword="true"/>. Disable only for local development.
    /// </summary>
    public bool VerifySsl { get; set; } = true;

    /// <summary>
    /// Gets or sets an optional proxy URL for HTTP requests.
    /// </summary>
    public string? Proxy { get; set; }

    /// <summary>
    /// Gets or sets additional headers sent with every request.
    /// </summary>
    public IDictionary<string, string> CustomHeaders { get; set; } =
        new Dictionary<string, string>();

    /// <summary>
    /// Gets or sets an optional organization ID for multi-tenant setups.
    /// Sent as the <c>X-Organization-ID</c> header.
    /// </summary>
    public string? OrganizationId { get; set; }

    /// <summary>
    /// Gets or sets an optional project/tenant identifier.
    /// Sent as the <c>X-Project-ID</c> header.
    /// </summary>
    public string? ProjectId { get; set; }

    /// <summary>
    /// Creates a <see cref="LiuHaoConfig"/> instance populated from
    /// environment variables prefixed with <c>LIUHAO_</c>.
    /// </summary>
    /// <returns>
    /// A configuration instance whose values are sourced from the process
    /// environment. Missing or unparseable values fall back to defaults.
    /// </returns>
    public static LiuHaoConfig FromEnvironment()
    {
        var config = new LiuHaoConfig();

        config.ApiKey = GetEnv("API_KEY") ?? config.ApiKey;
        config.BaseUrl = GetEnv("BASE_URL") ?? config.BaseUrl;
        config.Proxy = GetEnv("PROXY") ?? config.Proxy;
        config.OrganizationId = GetEnv("ORGANIZATION_ID") ?? config.OrganizationId;
        config.ProjectId = GetEnv("PROJECT_ID") ?? config.ProjectId;

        if (double.TryParse(GetEnv("TIMEOUT"), out var timeoutSeconds) && timeoutSeconds > 0)
        {
            config.Timeout = TimeSpan.FromSeconds(timeoutSeconds);
        }

        if (int.TryParse(GetEnv("MAX_RETRIES"), out var maxRetries) && maxRetries >= 0)
        {
            config.MaxRetries = maxRetries;
        }

        if (double.TryParse(GetEnv("RETRY_BACKOFF_FACTOR"), out var backoff) && backoff >= 0)
        {
            config.RetryBackoffFactor = backoff;
        }

        var debug = GetEnv("DEBUG");
        if (!string.IsNullOrEmpty(debug))
        {
            config.Debug = IsTruthy(debug);
        }

        var verifySsl = GetEnv("VERIFY_SSL");
        if (!string.IsNullOrEmpty(verifySsl))
        {
            config.VerifySsl = IsTruthy(verifySsl);
        }

        var retryCodes = GetEnv("RETRY_STATUS_CODES");
        if (!string.IsNullOrWhiteSpace(retryCodes))
        {
            var parsed = new HashSet<int>();
            foreach (var part in retryCodes.Split(','))
            {
                if (int.TryParse(part.Trim(), out var code))
                {
                    parsed.Add(code);
                }
            }

            if (parsed.Count > 0)
            {
                config.RetryStatusCodes = parsed;
            }
        }

        return config;
    }

    /// <summary>
    /// Validates the configuration and returns a list of issues found.
    /// </summary>
    /// <returns>
    /// A read-only list of human-readable validation error messages.
    /// An empty list means the configuration is valid.
    /// </returns>
    public IReadOnlyList<string> Validate()
    {
        var issues = new List<string>();

        if (string.IsNullOrWhiteSpace(ApiKey))
        {
            issues.Add("ApiKey is required for API access.");
        }

        if (string.IsNullOrWhiteSpace(BaseUrl))
        {
            issues.Add("BaseUrl must not be empty.");
        }
        else if (!BaseUrl.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                 && !BaseUrl.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
        {
            issues.Add("BaseUrl must start with http:// or https://.");
        }

        if (Timeout <= TimeSpan.Zero)
        {
            issues.Add("Timeout must be a positive duration.");
        }

        if (MaxRetries < 0)
        {
            issues.Add("MaxRetries must be non-negative.");
        }

        if (RetryBackoffFactor < 0)
        {
            issues.Add("RetryBackoffFactor must be non-negative.");
        }

        return issues;
    }

    /// <summary>
    /// Determines whether the configuration passes validation.
    /// </summary>
    /// <returns>
    /// <see langword="true"/> if <see cref="Validate"/> reports no issues;
    /// otherwise <see langword="false"/>.
    /// </returns>
    public bool IsValid() => Validate().Count == 0;

    /// <summary>
    /// Creates a shallow copy of this configuration instance.
    /// </summary>
    /// <returns>A new <see cref="LiuHaoConfig"/> with the same values.</returns>
    public LiuHaoConfig Clone()
    {
        return new LiuHaoConfig
        {
            ApiKey = ApiKey,
            BaseUrl = BaseUrl,
            Timeout = Timeout,
            MaxRetries = MaxRetries,
            RetryBackoffFactor = RetryBackoffFactor,
            RetryStatusCodes = new HashSet<int>(RetryStatusCodes),
            Debug = Debug,
            VerifySsl = VerifySsl,
            Proxy = Proxy,
            CustomHeaders = new Dictionary<string, string>(CustomHeaders),
            OrganizationId = OrganizationId,
            ProjectId = ProjectId,
        };
    }

    private static string? GetEnv(string name) =>
        Environment.GetEnvironmentVariable($"{EnvPrefix}{name}");

    private static bool IsTruthy(string value) =>
        value.Equals("1", StringComparison.OrdinalIgnoreCase)
        || value.Equals("true", StringComparison.OrdinalIgnoreCase)
        || value.Equals("yes", StringComparison.OrdinalIgnoreCase)
        || value.Equals("on", StringComparison.OrdinalIgnoreCase);
}
