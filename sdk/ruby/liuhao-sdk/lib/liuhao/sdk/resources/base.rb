# frozen_string_literal: true

module LiuHao
  module SDK
    module Resources
      # Base class for API resources.
      class Base
        attr_reader :client

        def initialize(client)
          @client = client
        end

        private

        def base_path
          raise NotImplementedError, "Subclasses must define base_path"
        end
      end
    end
  end
end
