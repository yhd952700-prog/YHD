# frozen_string_literal: true

require_relative "sdk/version"
require_relative "sdk/client"
require_relative "sdk/errors"
require_relative "sdk/resources/agents"
require_relative "sdk/resources/memory"
require_relative "sdk/resources/knowledge"

module LiuHao
  module SDK
    class Error < StandardError; end

    # Creates a new LiuHao SDK client.
    #
    # @param api_key [String] Your LiuHao API key
    # @param options [Hash] Additional configuration options
    # @option options [String] :base_url Base URL for the API
    # @option options [Integer] :timeout Request timeout in seconds
    # @option options [Integer] :max_retries Maximum number of retries
    # @return [LiuHao::SDK::Client]
    def self.new(api_key, **options)
      Client.new(api_key: api_key, **options)
    end
  end
end
