# frozen_string_literal: true

require "faraday"
require "json"
require "logger"

require_relative "errors"
require_relative "resources/agents"
require_relative "resources/memory"
require_relative "resources/knowledge"

module LiuHao
  module SDK
    # Main client for the LiuHao AI OS API.
    #
    # @example Creating a client
    #   client = LiuHao::SDK::Client.new(api_key: "your-api-key")
    #
    # @example With custom configuration
    #   client = LiuHao::SDK::Client.new(
    #     api_key: "your-api-key",
    #     base_url: "https://api.liuhao.ai/v1",
    #     timeout: 30,
    #     max_retries: 3
    #   )
    class Client
      DEFAULT_BASE_URL = "https://api.liuhao.ai/v1"
      DEFAULT_TIMEOUT = 30
      DEFAULT_MAX_RETRIES = 3
      DEFAULT_INITIAL_RETRY_DELAY = 1.0 # seconds
      DEFAULT_BACKOFF_MULTIPLIER = 2.0
      DEFAULT_USER_AGENT = "LiuHao-Ruby-SDK/#{VERSION}"

      attr_reader :api_key, :base_url, :timeout, :max_retries,
                  :initial_retry_delay, :backoff_multiplier, :user_agent,
                  :logger

      # @param api_key [String] Your LiuHao API key
      # @param base_url [String] Base URL for the API
      # @param timeout [Integer] Request timeout in seconds
      # @param max_retries [Integer] Maximum number of retry attempts
      # @param initial_retry_delay [Float] Initial delay before first retry (seconds)
      # @param backoff_multiplier [Float] Multiplier for exponential backoff
      # @param user_agent [String] Custom User-Agent header
      # @param logger [Logger] Logger instance
      def initialize(
        api_key:,
        base_url: DEFAULT_BASE_URL,
        timeout: DEFAULT_TIMEOUT,
        max_retries: DEFAULT_MAX_RETRIES,
        initial_retry_delay: DEFAULT_INITIAL_RETRY_DELAY,
        backoff_multiplier: DEFAULT_BACKOFF_MULTIPLIER,
        user_agent: DEFAULT_USER_AGENT,
        logger: nil
      )
        @api_key = api_key
        @base_url = base_url.end_with?("/") ? base_url.chomp("/") : base_url
        @timeout = timeout
        @max_retries = max_retries
        @initial_retry_delay = initial_retry_delay
        @backoff_multiplier = backoff_multiplier
        @user_agent = user_agent
        @logger = logger || default_logger

        validate_configuration!
      end

      # Access the Agents API.
      #
      # @return [Resources::Agents]
      def agents
        @agents ||= Resources::Agents.new(self)
      end

      # Access the Memory API.
      #
      # @return [Resources::Memory]
      def memory
        @memory ||= Resources::Memory.new(self)
      end

      # Access the Knowledge API.
      #
      # @return [Resources::Knowledge]
      def knowledge
        @knowledge ||= Resources::Knowledge.new(self)
      end

      # Sends a GET request.
      #
      # @param path [String] API endpoint path
      # @param params [Hash] Query parameters
      # @return [Hash, Array, nil] Parsed JSON response
      # @raise [LiuHaoError]
      def get(path, params: {})
        request(:get, path, params: params)
      end

      # Sends a POST request.
      #
      # @param path [String] API endpoint path
      # @param body [Hash] Request body
      # @return [Hash, Array, nil] Parsed JSON response
      # @raise [LiuHaoError]
      def post(path, body: nil)
        request(:post, path, body: body)
      end

      # Sends a PUT request.
      #
      # @param path [String] API endpoint path
      # @param body [Hash] Request body
      # @return [Hash, Array, nil] Parsed JSON response
      # @raise [LiuHaoError]
      def put(path, body: nil)
        request(:put, path, body: body)
      end

      # Sends a PATCH request.
      #
      # @param path [String] API endpoint path
      # @param body [Hash] Request body
      # @return [Hash, Array, nil] Parsed JSON response
      # @raise [LiuHaoError]
      def patch(path, body: nil)
        request(:patch, path, body: body)
      end

      # Sends a DELETE request.
      #
      # @param path [String] API endpoint path
      # @param body [Hash, nil] Request body
      # @return [Hash, Array, nil] Parsed JSON response
      # @raise [LiuHaoError]
      def delete(path, body: nil)
        request(:delete, path, body: body)
      end

      # Returns the Faraday connection (for advanced usage).
      #
      # @return [Faraday::Connection]
      def connection
        @connection ||= build_connection
      end

      private

      def validate_configuration!
        raise ConfigurationError, "api_key is required" if api_key.nil? || api_key.empty?
        raise ConfigurationError, "base_url is required" if base_url.nil? || base_url.empty?
        raise ConfigurationError, "timeout must be positive" unless timeout > 0
        raise ConfigurationError, "max_retries must be non-negative" unless max_retries >= 0
      end

      def build_connection
        Faraday.new(url: base_url) do |conn|
          conn.options.timeout = timeout
          conn.options.open_timeout = timeout

          conn.headers["User-Agent"] = user_agent
          conn.headers["Accept"] = "application/json"
          conn.headers["Authorization"] = "Bearer #{api_key}"

          conn.request :json
          conn.response :json, content_type: /\bjson$/

          conn.adapter Faraday.default_adapter
        end
      end

      def request(method, path, params: nil, body: nil)
        attempts = 0
        delay = initial_retry_delay
        last_error = nil

        while attempts <= max_retries
          begin
            return execute_request(method, path, params: params, body: body)
          rescue LiuHaoError => e
            last_error = e
            if e.retryable? && attempts < max_retries
              logger.warn("LiuHao SDK: Request failed (attempt #{attempts + 1}): #{e.message}")
            else
              raise e
            end
          rescue Faraday::TimeoutError, Faraday::ConnectionFailed => e
            last_error = e
            if attempts >= max_retries
              raise TimeoutError.new("Request timed out: #{e.message}")
            end
            logger.warn("LiuHao SDK: Network error (attempt #{attempts + 1}): #{e.message}")
          rescue Faraday::Error => e
            last_error = e
            if attempts >= max_retries
              raise NetworkError.new("Network error: #{e.message}")
            end
          end

          attempts += 1
          if attempts <= max_retries
            sleep(delay)
            delay *= backoff_multiplier
          end
        end

        raise last_error || LiuHaoError.new("Max retries exceeded")
      end

      def execute_request(method, path, params: nil, body: nil)
        response = connection.run_request(method, path, nil, nil) do |req|
          req.params.update(params) if params && !params.empty?
          req.body = body if body
        end

        handle_response(response)
      end

      def handle_response(response)
        status = response.status
        request_id = response.headers["x-request-id"]

        if status >= 200 && status < 300
          return nil if response.body.nil? || response.body.empty?
          parse_body(response.body)
        else
          raise_error_from_response(response, status, request_id)
        end
      end

      def raise_error_from_response(response, status, request_id)
        body = parse_body(response.body) rescue {}
        message = extract_error_message(body)

        case status
        when 400
          field_errors = body["details"] || body["errors"] || []
          field_errors = Array(field_errors).map(&:to_s)
          raise ValidationError.new(
            message,
            field_errors: field_errors,
            request_id: request_id
          )
        when 401
          raise AuthenticationError.new(message, request_id: request_id)
        when 404
          raise NotFoundError.new(message, request_id: request_id)
        when 429
          retry_after = (response.headers["retry-after"] || 0).to_i
          raise RateLimitError.new(
            message,
            retry_after_seconds: retry_after,
            request_id: request_id
          )
        else
          if status >= 500
            raise APIError.new(
              message,
              status_code: status,
              request_id: request_id
            )
          else
            raise LiuHaoError.new(
              message,
              status_code: status,
              request_id: request_id
            )
          end
        end
      end

      def extract_error_message(body)
        return "Unknown error" unless body.is_a?(Hash)
        body["message"] || body["error"] || body["detail"] || "Unknown error"
      end

      def parse_body(body)
        return nil if body.nil? || body.to_s.strip.empty?
        JSON.parse(body.to_s)
      rescue JSON::ParserError => e
        logger.warn("LiuHao SDK: Failed to parse response body: #{e.message}")
        body
      end

      def default_logger
        logger = Logger.new($stderr)
        logger.level = Logger::WARN
        logger.progname = "LiuHao::SDK"
        logger
      end
    end
  end
end
