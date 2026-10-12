# frozen_string_literal: true

require_relative "base"

module LiuHao
  module SDK
    module Resources
      # Resource class for managing AI Agents.
      class Agents < Base
        # Lists all agents.
        #
        # @return [Array<Hash>] List of agents
        def list
          client.get("agents")
        end

        # Lists agents with pagination.
        #
        # @param limit [Integer, nil] Maximum number of results
        # @param cursor [String, nil] Pagination cursor
        # @return [Array<Hash>] List of agents
        def list_with(limit: nil, cursor: nil)
          params = {}
          params[:limit] = limit if limit
          params[:cursor] = cursor if cursor
          client.get("agents", params: params)
        end

        # Gets an agent by ID.
        #
        # @param agent_id [String] Agent ID
        # @return [Hash] Agent data
        def get(agent_id)
          client.get("agents/#{agent_id}")
        end

        # Creates a new agent.
        #
        # @param name [String] Agent name
        # @param model_name [String] Model name
        # @param options [Hash] Additional agent options
        # @yield [Hash] Yields agent attributes for customization
        # @return [Hash] Created agent
        def create(name:, model_name:, **options)
          body = { name: name, model_name: model_name }.merge(options)
          client.post("agents", body: body)
        end

        # Creates an agent with a block for configuration.
        #
        # @yield [Hash] Agent attributes hash to modify
        # @return [Hash] Created agent
        def create_with(&block)
          attrs = {}
          yield attrs if block_given?
          client.post("agents", body: attrs)
        end

        # Updates an agent.
        #
        # @param agent_id [String] Agent ID
        # @param attrs [Hash] Attributes to update
        # @return [Hash] Updated agent
        def update(agent_id, **attrs)
          client.put("agents/#{agent_id}", body: attrs)
        end

        # Partially updates an agent.
        #
        # @param agent_id [String] Agent ID
        # @param attrs [Hash] Attributes to patch
        # @return [Hash] Updated agent
        def patch(agent_id, **attrs)
          client.patch("agents/#{agent_id}", body: attrs)
        end

        # Deletes an agent.
        #
        # @param agent_id [String] Agent ID
        # @return [void]
        def delete(agent_id)
          client.delete("agents/#{agent_id}")
          nil
        end

        # Executes an agent with the given input.
        #
        # @param agent_id [String] Agent ID
        # @param input [String] Input text
        # @return [String] Agent output
        def execute(agent_id, input)
          result = client.post("agents/#{agent_id}/execute", body: { input: input })
          result.is_a?(Hash) ? result["output"] : result
        end

        # Executes an agent with full parameters.
        #
        # @param agent_id [String] Agent ID
        # @param params [Hash] Execution parameters
        # @return [Hash] Full execution result
        def execute_with(agent_id, **params)
          client.post("agents/#{agent_id}/execute", body: params)
        end

        # Gets the status of an agent.
        #
        # @param agent_id [String] Agent ID
        # @return [String] Agent status
        def status(agent_id)
          agent = client.get("agents/#{agent_id}/status")
          agent.is_a?(Hash) ? agent["status"] : "unknown"
        end

        # Cancels a running agent.
        #
        # @param agent_id [String] Agent ID
        # @return [void]
        def cancel(agent_id)
          client.post("agents/#{agent_id}/cancel")
          nil
        end

        # Gets metrics for an agent.
        #
        # @param agent_id [String] Agent ID
        # @return [Hash] Agent metrics
        def metrics(agent_id)
          client.get("agents/#{agent_id}/metrics")
        end

        # Pauses an agent.
        #
        # @param agent_id [String] Agent ID
        # @return [Hash] Updated agent
        def pause(agent_id)
          client.post("agents/#{agent_id}/pause")
        end

        # Resumes a paused agent.
        #
        # @param agent_id [String] Agent ID
        # @return [Hash] Updated agent
        def resume(agent_id)
          client.post("agents/#{agent_id}/resume")
        end

        # Lists tools available to an agent.
        #
        # @param agent_id [String] Agent ID
        # @return [Array<String>] List of tool names
        def tools(agent_id)
          client.get("agents/#{agent_id}/tools")
        end
      end
    end
  end
end
