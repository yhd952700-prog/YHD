//
//  JSONValue.swift
//  LiuHaoSDK
//
//  A Codable, Sendable representation of arbitrary JSON values.
//

import Foundation

/// A type-erased JSON value.
///
/// Used for free-form payloads such as agent metadata, memory
/// attributes, and request contexts whose shape is defined by the
/// caller rather than by the SDK.
///
/// ```swift
/// let metadata: [String: JSONValue] = [
///     "team": "research",
///     "priority": .int(2),
///     "flags": .array([.string("beta")])
/// ]
/// ```
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int.self) {
            self = .int(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }
}

// MARK: - Convenience literals

extension JSONValue: ExpressibleByStringLiteral,
                     ExpressibleByIntegerLiteral,
                     ExpressibleByFloatLiteral,
                     ExpressibleByBooleanLiteral,
                     ExpressibleByArrayLiteral,
                     ExpressibleByDictionaryLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
    public init(integerLiteral value: Int) { self = .int(value) }
    public init(floatLiteral value: Double) { self = .double(value) }
    public init(booleanLiteral value: Bool) { self = .bool(value) }
    public init(arrayLiteral elements: JSONValue...) { self = .array(elements) }
    public init(dictionaryLiteral elements: (String, JSONValue)...) {
        self = .object(Dictionary(uniqueKeysWithValues: elements))
    }
}

// MARK: - Accessors

extension JSONValue {
    /// The underlying string, if the value is a string.
    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    /// The underlying integer, if the value is an integer.
    public var intValue: Int? {
        if case .int(let value) = self { return value }
        return nil
    }

    /// The underlying double, if the value is a double or integer.
    public var doubleValue: Double? {
        switch self {
        case .double(let value): return value
        case .int(let value): return Double(value)
        default: return nil
        }
    }

    /// The underlying boolean, if the value is a boolean.
    public var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }
}
