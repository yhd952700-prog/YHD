//
//  LiuHaoSDKTests.swift
//  LiuHaoSDKTests
//
//  Unit tests for the LiuHao AI OS Swift SDK.
//

import XCTest
@testable import LiuHaoSDK

final class LiuHaoSDKTests: XCTestCase {

    // MARK: - Configuration

    func testConfigDefaults() {
        let config = LiuHaoConfig(apiKey: "test-key")
        XCTAssertEqual(config.baseURL, LiuHaoConfig.defaultBaseURL)
        XCTAssertEqual(config.timeout, 30)
        XCTAssertEqual(config.maxRetries, 3)
        XCTAssertEqual(config.userAgent, "LiuHao-SDK-Swift/\(LiuHaoConfig.sdkVersion)")
    }

    func testConfigFromEnvironmentMissingKey() {
        XCTAssertThrowsError(try LiuHaoConfig.environment()) { error in
            guard case LiuHaoError.missingAPIKey = error else {
                return XCTFail("Expected missingAPIKey, got \(error)")
            }
        }
    }

    // MARK: - Models

    func testAgentCodingRoundTrip() throws {
        let agent = Agent(
            id: "agent-1",
            name: "Assistant",
            description: "Helpful agent",
            model: "liuhao-large",
            status: .active,
            capabilities: ["chat"],
            systemPrompt: "Be helpful",
            tools: ["search"],
            metadata: ["team": "research"]
        )
        let data = try JSONEncoder().encode(agent)
        let decoded = try JSONDecoder().decode(Agent.self, from: data)
        XCTAssertEqual(agent, decoded)
    }

    func testAgentRunTerminalStates() {
        var run = AgentRun(id: "run-1", agentID: "agent-1", status: .running)
        XCTAssertFalse(run.isTerminal)
        run.status = .completed
        XCTAssertTrue(run.isTerminal)
        run.status = .timedOut
        XCTAssertTrue(run.isTerminal)
    }

    func testMemoryEntryCodingKeys() throws {
        let entry = MemoryEntry(
            id: "mem-1",
            content: "User likes dark mode",
            category: .preference,
            entityID: "user-42"
        )
        let data = try JSONEncoder().encode(entry)
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        XCTAssertEqual(json?["entity_id"] as? String, "user-42")
        XCTAssertEqual(json?["category"] as? String, "preference")
    }

    func testJSONValueRoundTrip() throws {
        let value: [String: JSONValue] = [
            "name": "liuhao",
            "count": 3,
            "ratio": 0.5,
            "active": true,
            "tags": ["a", "b"],
            "nested": ["key": "value"]
        ]
        let data = try JSONEncoder().encode(value)
        let decoded = try JSONDecoder().decode([String: JSONValue].self, from: data)
        XCTAssertEqual(value, decoded)
        XCTAssertEqual(decoded["count"]?.intValue, 3)
        XCTAssertEqual(decoded["ratio"]?.doubleValue, 0.5)
    }

    // MARK: - Errors

    func testErrorDescriptions() {
        XCTAssertNotNil(LiuHaoError.missingAPIKey.errorDescription)
        XCTAssertNotNil(LiuHaoError.timeout.errorDescription)
        XCTAssertTrue(
            LiuHaoError.notFound(message: "gone", requestID: "req-1")
                .errorDescription!.contains("req-1")
        )
    }

    func testRetryableClassification() {
        XCTAssertTrue(LiuHaoError.timeout.isRetryable)
        XCTAssertTrue(
            LiuHaoError.api(statusCode: 503, message: "unavailable", code: nil, requestID: nil)
                .isRetryable
        )
        XCTAssertFalse(
            LiuHaoError.api(statusCode: 400, message: "bad", code: nil, requestID: nil)
                .isRetryable
        )
        XCTAssertFalse(
            LiuHaoError.authenticationFailed(message: "no", requestID: nil).isRetryable
        )
    }

    // MARK: - Pagination

    func testPaginationHasMore() {
        let page = PaginatedResponse(items: [1, 2, 3], total: 10, limit: 3, offset: 0)
        XCTAssertTrue(page.hasMore)
        let last = PaginatedResponse(items: [1], total: 4, limit: 3, offset: 3)
        XCTAssertFalse(last.hasMore)
    }
}
