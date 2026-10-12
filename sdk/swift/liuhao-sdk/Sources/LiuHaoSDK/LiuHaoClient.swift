//
//  LiuHaoClient.swift
//  LiuHaoSDK
//
//  Main entry point for the LiuHao AI OS Swift SDK.
//

import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(os)
import os
#endif

/// Main client for the LiuHao AI OS platform.
///
/// `LiuHaoClient` provides a unified, resource-oriented interface to
/// the LiuHao API with built-in Bearer-token authentication,
/// automatic retries with exponential backoff, and typed `Codable`
/// responses. All APIs are `async`/`await` and the client is safe to
/// share across tasks.
///
/// ```swift
/// let client = try LiuHaoClient(apiKey: "lh_xxx")
/// let agents = try await client.agents.list()
/// let run = try await client.agents.run(agentID: "agent-1", input: "Hello")
/// ```
public final class LiuHaoClient: Sendable {

    // MARK: - Properties

    /// The active SDK configuration.
    public let config: LiuHaoConfig

    /// Agent management and execution resource.
    public let agents: AgentResource

    /// Memory operations resource.
    public let memory: MemoryResource

    /// Knowledge base management resource.
    public let knowledge: KnowledgeResource

    /// Underlying URL session.
    let session: URLSession

    /// Shared JSON encoder.
    let encoder: JSONEncoder

    /// Shared JSON decoder.
    let decoder: JSONDecoder

    #if canImport(os)
    private let logger = Logger(subsystem: "ai.liuhao.sdk", category: "LiuHaoClient")
    #endif

    // MARK: - Initialization

    /// Creates a client from an explicit configuration.
    ///
    /// - Parameters:
    ///   - config: The SDK configuration.
    ///   - session: Optional custom `URLSession`. When `nil`, a session
    ///     is built from ``LiuHaoConfig/timeout``.
    public init(config: LiuHaoConfig, session: URLSession? = nil) {
        self.config = config

        if let session {
            self.session = session
        } else {
            let sessionConfig = config.urlSessionConfiguration ?? .default
            sessionConfig.timeoutIntervalForRequest = config.timeout
            self.session = URLSession(configuration: sessionConfig)
        }

        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()

        self.agents = AgentResource(client: self)
        self.memory = MemoryResource(client: self)
        self.knowledge = KnowledgeResource(client: self)
    }

    /// Convenience initializer using an API key and optional base URL.
    ///
    /// - Parameters:
    ///   - apiKey: API key for Bearer-token authentication.
    ///   - baseURL: Base API URL. Defaults to ``LiuHaoConfig/defaultBaseURL``.
    ///   - timeout: Request timeout in seconds. Defaults to `30`.
    public convenience init(
        apiKey: String,
        baseURL: URL = LiuHaoConfig.defaultBaseURL,
        timeout: TimeInterval = 30
    ) {
        let config = LiuHaoConfig(apiKey: apiKey, baseURL: baseURL, timeout: timeout)
        self.init(config: config)
    }

    /// Creates a client from environment variables.
    ///
    /// See ``LiuHaoConfig/environment(processInfo:)`` for the
    /// recognized variables.
    ///
    /// - Throws: ``LiuHaoError/missingAPIKey`` when `LIUHAO_API_KEY` is unset.
    public static func fromEnvironment() throws -> LiuHaoClient {
        LiuHaoClient(config: try LiuHaoConfig.environment())
    }

    // MARK: - HTTP verbs

    /// Performs an HTTP `GET` request and decodes the response.
    func get<Response: Decodable & Sendable>(
        _ path: String,
        query: [String: String?] = [:]
    ) async throws -> Response {
        try await request("GET", path, query: query)
    }

    /// Performs an HTTP `POST` request with a JSON body.
    func post<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        _ path: String,
        body: Body,
        query: [String: String?] = [:]
    ) async throws -> Response {
        try await request("POST", path, body: body, query: query)
    }

    /// Performs an HTTP `PATCH` request with a JSON body.
    func patch<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        _ path: String,
        body: Body
    ) async throws -> Response {
        try await request("PATCH", path, body: body)
    }

    /// Performs an HTTP `PUT` request with a JSON body.
    func put<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        _ path: String,
        body: Body
    ) async throws -> Response {
        try await request("PUT", path, body: body)
    }

    /// Performs an HTTP `DELETE` request.
    func delete<Response: Decodable & Sendable>(
        _ path: String
    ) async throws -> Response {
        try await request("DELETE", path)
    }

    // MARK: - Core request pipeline

    /// Sends a request without a body and decodes the response.
    func request<Response: Decodable & Sendable>(
        _ method: String,
        _ path: String,
        query: [String: String?] = [:]
    ) async throws -> Response {
        try await perform(method, path, bodyData: nil, query: query)
    }

    /// Sends a request with an encodable JSON body and decodes the response.
    func request<Body: Encodable & Sendable, Response: Decodable & Sendable>(
        _ method: String,
        _ path: String,
        body: Body,
        query: [String: String?] = [:]
    ) async throws -> Response {
        let bodyData: Data
        do {
            bodyData = try encoder.encode(body)
        } catch {
            throw LiuHaoError.invalidRequest("Failed to encode request body: \(error.localizedDescription)")
        }
        return try await perform(method, path, bodyData: bodyData, query: query)
    }

    /// Executes the request with retry logic and error mapping.
    private func perform<Response: Decodable & Sendable>(
        _ method: String,
        _ path: String,
        bodyData: Data?,
        query: [String: String?]
    ) async throws -> Response {
        let url = try buildURL(path: path, query: query)

        var attempt = 0
        while true {
            var urlRequest = URLRequest(url: url)
            urlRequest.httpMethod = method
            urlRequest.httpBody = bodyData
            applyHeaders(to: &urlRequest)

            log("→ \(method) \(url.absoluteString) (attempt \(attempt + 1)/\(config.maxRetries + 1))")

            let (data, response): (Data, URLResponse)
            do {
                (data, response) = try await session.data(for: urlRequest)
            } catch let error as URLError where error.code == .timedOut {
                throw LiuHaoError.timeout
            } catch let error as URLError where error.code == .cancelled {
                throw LiuHaoError.cancelled
            } catch {
                let wrapped = LiuHaoError.network(underlying: error.localizedDescription)
                if attempt < config.maxRetries {
                    attempt += 1
                    try await backoff(attempt: attempt, retryAfter: nil)
                    continue
                }
                throw wrapped
            }

            guard let httpResponse = response as? HTTPURLResponse else {
                throw LiuHaoError.invalidResponse
            }

            if (200..<300).contains(httpResponse.statusCode) {
                return try decodeResponse(data, as: Response.self)
            }

            let mappedError = mapError(statusCode: httpResponse.statusCode, data: data, headers: httpResponse.allHeaderFields)

            if mappedError.isRetryable, attempt < config.maxRetries {
                attempt += 1
                var retryAfter: TimeInterval?
                if case .rateLimited(_, let serverRetryAfter, _) = mappedError {
                    retryAfter = serverRetryAfter
                }
                log("⚠︎ Retryable error \(httpResponse.statusCode); retrying (attempt \(attempt + 1))")
                try await backoff(attempt: attempt, retryAfter: retryAfter)
                continue
            }

            throw mappedError
        }
    }

    // MARK: - Response handling

    private func decodeResponse<Response: Decodable>(
        _ data: Data,
        as type: Response.Type
    ) throws -> Response {
        if Response.self == EmptyResponse.self, let empty = EmptyResponse() as? Response {
            return empty
        }
        do {
            return try decoder.decode(Response.self, from: data)
        } catch {
            throw LiuHaoError.decoding(underlying: error.localizedDescription)
        }
    }

    private func mapError(
        statusCode: Int,
        data: Data,
        headers: [AnyHashable: Any]
    ) -> LiuHaoError {
        let body = try? decoder.decode(APIErrorBody.self, from: data)
        let message = body?.error.message ?? "HTTP \(statusCode) error"
        let code = body?.error.code
        let details = body?.error.details
        let requestID = (headers["X-Request-ID"] ?? headers["x-request-id"]) as? String

        switch statusCode {
        case 401, 403:
            return .authenticationFailed(message: message, requestID: requestID)
        case 404:
            return .notFound(message: message, requestID: requestID)
        case 400, 422:
            return .validation(message: message, code: code, details: details)
        case 429:
            let retryAfter = (headers["Retry-After"] ?? headers["retry-after"]) as? String
                .flatMap(TimeInterval.init)
            return .rateLimited(message: message, retryAfter: retryAfter, requestID: requestID)
        default:
            return .api(statusCode: statusCode, message: message, code: code, requestID: requestID)
        }
    }

    // MARK: - URL & headers

    private func buildURL(path: String, query: [String: String?]) throws -> URL {
        let base = config.baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let relative = path.hasPrefix("/") ? path : "/\(path)"
        guard var components = URLComponents(string: base + relative) else {
            throw LiuHaoError.invalidRequest("Could not build URL for path: \(path)")
        }
        let items = query.compactMap { key, value -> URLQueryItem? in
            guard let value else { return nil }
            return URLQueryItem(name: key, value: value)
        }
        if !items.isEmpty {
            components.queryItems = items
        }
        guard let url = components.url else {
            throw LiuHaoError.invalidRequest("Could not build URL for path: \(path)")
        }
        return url
    }

    private func applyHeaders(to request: inout URLRequest) {
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(config.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")

        if let organizationID = config.organizationID {
            request.setValue(organizationID, forHTTPHeaderField: "X-Organization-ID")
        }
        if let projectID = config.projectID {
            request.setValue(projectID, forHTTPHeaderField: "X-Project-ID")
        }
        for (name, value) in config.customHeaders {
            request.setValue(value, forHTTPHeaderField: name)
        }
    }

    // MARK: - Retry helpers

    private func backoff(attempt: Int, retryAfter: TimeInterval?) async throws {
        let delay = retryAfter ?? config.retryBackoffFactor * pow(2.0, Double(attempt - 1))
        let nanoseconds = UInt64(max(delay, 0.05) * 1_000_000_000)
        try await Task.sleep(nanoseconds: nanoseconds)
    }

    private func log(_ message: String) {
        guard config.debug else { return }
        #if canImport(os)
        logger.debug("\(message, privacy: .public)")
        #else
        print("[LiuHaoSDK] \(message)")
        #endif
    }

    // MARK: - Platform utilities

    /// Checks the health of the LiuHao API.
    ///
    /// - Returns: The platform ``HealthStatus``.
    public func healthCheck() async throws -> HealthStatus {
        try await get("/health")
    }

    /// Fetches API version and platform information.
    ///
    /// - Returns: The ``APIInfo`` for the deployment.
    public func apiInfo() async throws -> APIInfo {
        try await get("/info")
    }
}

/// Placeholder type used for endpoints that return no meaningful body.
struct EmptyResponse: Codable, Sendable {}
