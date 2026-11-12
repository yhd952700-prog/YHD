# frozen_string_literal: true

module LiuHao
  module SDK
    # Base error class for all LiuHao SDK errors.
    class LiuHaoError < StandardError
      attr_reader :status_code, :request_id

      def initialize(message = nil, status_code: nil, request_id: nil)
        super(message)
        @status_code = status_code
        @request_id = request_id
      end

      # Returns true if the error is retryable.
      def retryable?
        false
      end
    end

    # Raised when authentication fails (HTTP 401).
    class AuthenticationError < LiuHaoError
      def initialize(message = "Authentication failed", request_id: nil)
        super(message, status_code: 401, request_id: request_id)
      end
    end

    # Raised when a requested resource is not found (HTTP 404).
    class NotFoundError < LiuHaoError
      def initialize(message = "Resource not found", request_id: nil)
        super(message, status_code: 404, request_id: request_id)
      end
    end

    # Raised when request validation fails (HTTP 400).
    class ValidationError < LiuHaoError
      attr_reader :field_errors

      def initialize(message = "Validation error", field_errors: [], request_id: nil)
        super(message, status_code: 400, request_id: request_id)
        @field_errors = field_errors
      end
    end

    # Raised when rate limits are exceeded (HTTP 429).
    class RateLimitError < LiuHaoError
      attr_reader :retry_after_seconds

      def initialize(message = "Rate limit exceeded", retry_after_seconds: 0, request_id: nil)
        super(message, status_code: 429, request_id: request_id)
        @retry_after_seconds = retry_after_seconds
      end

      def retryable?
        true
      end
    end

    # Raised when the API returns an unexpected error (HTTP 5xx).
    class APIError < LiuHaoError
      RETRYABLE_STATUSES = [502, 503, 504].freeze

      def initialize(message = "API error", status_code: 500, request_id: nil)
        super(message, status_code: status_code, request_id: request_id)
      end

      def retryable?
        RETRYABLE_STATUSES.include?(status_code)
      end
    end

    # Raised when a request times out.
    class TimeoutError < LiuHaoError
      def initialize(message = "Request timed out")
        super(message)
      end

      def retryable?
        true
      end
    end

    # Raised when configuration is invalid.
    class ConfigurationError < LiuHaoError
      def initialize(message = "Configuration error")
        super(message)
      end
    end

    # Raised for network connection errors.
    class NetworkError < LiuHaoError
      def initialize(message = "Network error")
        super(message)
      end

      def retryable?
        true
      end
    end
  end
end
