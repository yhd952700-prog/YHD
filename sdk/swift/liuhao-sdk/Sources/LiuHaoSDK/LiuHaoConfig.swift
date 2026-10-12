//
//  LiuHaoConfig.swift
//  LiuHaoSDK
//
//  Configuration for the LiuHao AI OS SDK client.
//

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

/// Configuration for ``LiuHaoClient``.
///
/// The configuration controls the API endpoint, authentication,
/// timeouts, retry behaviour, and default headers used for every
/// request performed by the SDK.
///
/// Create a configuration explicitly, or build one from environment
/// variables using ``environment()``:
///
/// ```swift
/// let config = LiuHaoConfig(apiKey: "lh_xxx")
/// let client = LiuHaoClient(config: config)
/// ```
public struct LiuHaoConfig: Sendable, Equatable {

    // MARK: - Properties

    /// API key used for Bearer-token authentication.
    public var apiKey: String

    /// Base URL of the LiuHao AI OS API.
    public var baseURL: URL

    /// Optional organization identifier sent as the
    /// `X-Organization-ID` header on every request.
    public var organizationID: String?

    /// Optional project identifier sent as the
    /// `X-Project-ID` header on every request.
    public var projectID: String?

    /// Default timeout interval (in seconds) for each request.
    public var timeout: TimeInterval

    /// Maximum number of automatic retries for transient failures
    /// (rate limiting, server errors, network issues).
    public var maxRetries: Int

    /// Multiplier used by the exponential backoff strategy.
    ///
    /// The wait time before attempt `n` is
    /// `retryBackoffFactor * 2^n` seconds.
    public var retryBackoffFactor: TimeInterval

    /// Additional headers merged into every request.
    public var customHeaders: [String: String]

    /// When `true`, the client logs request/response diagnostics
    /// through `OSLog` (never including the API key).
    public var debug: Bool

    /// Optional custom `URLSession` used for testing or advanced
    /// transport configuration.
    public var urlSessionConfiguration: URLSessionConfiguration?

    /// User-Agent string sent with each request.
    public var userAgent: String

    // MARK: - Defaults

    /// Default production base URL of the LiuHao AI OS API.
    public static let defaultBaseURL = URL(string: "https://api.liuhao.ai/v1")!

    /// Default SDK version string, reported in the User-Agent header.
    public static let sdkVersion = "1.0.0"

    // MARK: - Initialization

    /// Creates a new SDK configuration.
    ///
    /// - Parameters:
    ///   - apiKey: API key for Bearer-token authentication.
    ///   - baseURL: Base API URL. Defaults to ``defaultBaseURL``.
    ///   - organizationID: Optional organization identifier.
    ///   - projectID: Optional project identifier.
    ///   - timeout: Request timeout in seconds. Defaults to `30`.
    ///   - maxRetries: Maximum retry attempts for transient errors. Defaults to `3`.
    ///   - retryBackoffFactor: Exponential backoff multiplier. Defaults to `0.5`.
    ///   - customHeaders: Additional headers for every request. Defaults to empty.
    ///   - debug: Enable diagnostic logging. Defaults to `false`.
    public init(
        apiKey: String,
        baseURL: URL = LiuHaoConfig.defaultBaseURL,
        organizationID: String? = nil,
        projectID: String? = nil,
        timeout: TimeInterval = 30,
        maxRetries: Int = 3,
        retryBackoffFactor: TimeInterval = 0.5,
        customHeaders: [String: String] = [:],
        debug: Bool = false
    ) {
        self.apiKey = apiKey
        self.baseURL = baseURL
        self.organizationID = organizationID
        self.projectID = projectID
        self.timeout = timeout
        self.maxRetries = maxRetries
        self.retryBackoffFactor = retryBackoffFactor
        self.customHeaders = customHeaders
        self.debug = debug
        self.urlSessionConfiguration = nil
        self.userAgent = "LiuHao-SDK-Swift/\(LiuHaoConfig.sdkVersion)"
    }

    // MARK: - Environment

    /// Builds a configuration from process environment variables.
    ///
    /// Recognized variables:
    /// - `LIUHAO_API_KEY` — API key (required)
    /// - `LIUHAO_BASE_URL` — base API URL
    /// - `LIUHAO_ORGANIZATION_ID` — organization identifier
    /// - `LIUHAO_PROJECT_ID` — project identifier
    /// - `LIUHAO_TIMEOUT` — request timeout in seconds
    ///
    /// - Returns: A populated configuration.
    /// - Throws: ``LiuHaoError/missingAPIKey`` when `LIUHAO_API_KEY`
    ///   is not set.
    public static func environment(
        processInfo: ProcessInfo = .processInfo
    ) throws -> LiuHaoConfig {
        let env = processInfo.environment

        guard let apiKey = env["LIUHAO_API_KEY"], !apiKey.isEmpty else {
            throw LiuHaoError.missingAPIKey
        }

        let baseURL = env["LIUHAO_BASE_URL"]
            .flatMap(URL.init(string:)) ?? LiuHaoConfig.defaultBaseURL

        let timeout = env["LIUHAO_TIMEOUT"]
            .flatMap(TimeInterval.init) ?? 30

        return LiuHaoConfig(
            apiKey: apiKey,
            baseURL: baseURL,
            organizationID: env["LIUHAO_ORGANIZATION_ID"],
            projectID: env["LIUHAO_PROJECT_ID"],
            timeout: timeout
        )
    }
}
