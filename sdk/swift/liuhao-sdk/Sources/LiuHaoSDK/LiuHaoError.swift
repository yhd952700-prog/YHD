//
//  LiuHaoError.swift
//  LiuHaoSDK
//
//  Error types for the LiuHao AI OS SDK.
//

import Foundation

/// Errors thrown by the LiuHao AI OS SDK.
///
/// All network, decoding, and API-level failures are surfaced through
/// this single enum. API errors carry the machine-readable `code`,
/// the platform request identifier, and structured details whenever
/// the server provides them.
public enum LiuHaoError: Error, Sendable {

    /// No API key was provided. Set ``LiuHaoConfig/apiKey`` or the
    /// `LIUHAO_API_KEY` environment variable.
    case missingAPIKey

    /// The request could not be built (invalid URL or body).
    case invalidRequest(String)

    /// Authentication failed (HTTP 401).
    case authenticationFailed(message: String, requestID: String?)

    /// The requested resource does not exist (HTTP 404).
    case notFound(message: String, requestID: String?)

    /// The request payload failed server-side validation (HTTP 400/422).
    case validation(message: String, code: String?, details: [String: JSONValue]?)

    /// Rate limit exceeded (HTTP 429). Retry after `retryAfter` seconds.
    case rateLimited(message: String, retryAfter: TimeInterval?, requestID: String?)

    /// The server returned an unexpected error (HTTP 5xx or other 4xx).
    case api(statusCode: Int, message: String, code: String?, requestID: String?)

    /// The request timed out.
    case timeout

    /// A transport-level error occurred (no connectivity, TLS failure, ...).
    case network(underlying: String)

    /// The response body could not be decoded into the expected type.
    case decoding(underlying: String)

    /// The server response was not valid JSON or had an unexpected shape.
    case invalidResponse

    /// The operation was cancelled.
    case cancelled

    /// An unspecified error.
    case unknown(message: String)
}

// MARK: - LocalizedError

extension LiuHaoError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .missingAPIKey:
            return "Missing API key. Set LiuHaoConfig.apiKey or the LIUHAO_API_KEY environment variable."
        case .invalidRequest(let reason):
            return "Invalid request: \(reason)"
        case .authenticationFailed(let message, let requestID):
            return Self.format("Authentication failed: \(message)", requestID: requestID)
        case .notFound(let message, let requestID):
            return Self.format("Not found: \(message)", requestID: requestID)
        case .validation(let message, let code, _):
            if let code { return "Validation error (\(code)): \(message)" }
            return "Validation error: \(message)"
        case .rateLimited(let message, let retryAfter, let requestID):
            var text = "Rate limited: \(message)"
            if let retryAfter { text += " (retry after \(Int(retryAfter))s)" }
            return Self.format(text, requestID: requestID)
        case .api(let statusCode, let message, let code, let requestID):
            var text = "API error \(statusCode)"
            if let code { text += " [\(code)]" }
            text += ": \(message)"
            return Self.format(text, requestID: requestID)
        case .timeout:
            return "The request timed out."
        case .network(let underlying):
            return "Network error: \(underlying)"
        case .decoding(let underlying):
            return "Failed to decode response: \(underlying)"
        case .invalidResponse:
            return "The server returned an invalid response."
        case .cancelled:
            return "The operation was cancelled."
        case .unknown(let message):
            return "Unknown error: \(message)"
        }
    }

    private static func format(_ message: String, requestID: String?) -> String {
        guard let requestID else { return message }
        return "\(message) (request-id: \(requestID))"
    }
}

// MARK: - Equatable

extension LiuHaoError: Equatable {
    public static func == (lhs: LiuHaoError, rhs: LiuHaoError) -> Bool {
        lhs.errorDescription == rhs.errorDescription
    }
}

// MARK: - Retry classification

extension LiuHaoError {
    /// Whether the error represents a transient failure that may succeed
    /// when retried.
    var isRetryable: Bool {
        switch self {
        case .rateLimited, .timeout, .network:
            return true
        case .api(let statusCode, _, _, _):
            return statusCode >= 500
        default:
            return false
        }
    }
}
