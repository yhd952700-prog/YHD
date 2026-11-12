# frozen_string_literal: true

require_relative "base"

module LiuHao
  module SDK
    module Resources
      # Resource class for managing the memory system.
      class Memory < Base
        # Stores a new memory item.
        #
        # @param content [String] Memory content
        # @param memory_type [String] Type of memory
        # @param options [Hash] Additional options
        # @return [Hash] Created memory item
        def store(content:, memory_type:, **options)
          body = { content: content, memory_type: memory_type }.merge(options)
          client.post("memory/items", body: body)
        end

        # Stores a memory using a block.
        #
        # @yield [Hash] Memory attributes to build
        # @return [Hash] Created memory item
        def store_with(&block)
          attrs = {}
          yield attrs if block_given?
          client.post("memory/items", body: attrs)
        end

        # Retrieves a memory item by ID.
        #
        # @param memory_id [String] Memory ID
        # @return [Hash] Memory item
        def retrieve(memory_id)
          client.get("memory/items/#{memory_id}")
        end

        # Updates a memory item.
        #
        # @param memory_id [String] Memory ID
        # @param attrs [Hash] Attributes to update
        # @return [Hash] Updated memory item
        def update(memory_id, **attrs)
          client.put("memory/items/#{memory_id}", body: attrs)
        end

        # Partially updates a memory item.
        #
        # @param memory_id [String] Memory ID
        # @param attrs [Hash] Attributes to patch
        # @return [Hash] Updated memory item
        def patch(memory_id, **attrs)
          client.patch("memory/items/#{memory_id}", body: attrs)
        end

        # Deletes a memory item.
        #
        # @param memory_id [String] Memory ID
        # @return [void]
        def delete(memory_id)
          client.delete("memory/items/#{memory_id}")
          nil
        end

        # Searches memory by query.
        #
        # @param query [String] Search query
        # @param memory_type [String, nil] Filter by memory type
        # @param category [String, nil] Filter by category
        # @param limit [Integer] Maximum results
        # @return [Array<Hash>] Search results
        def search(query, memory_type: nil, category: nil, limit: 10)
          body = { query: query, limit: limit }
          body[:memory_type] = memory_type if memory_type
          body[:category] = category if category
          client.post("memory/search", body: body)
        end

        # Lists memory items.
        #
        # @return [Array<Hash>] List of memory items
        def list
          client.get("memory/items")
        end

        # Lists memory items with filters.
        #
        # @param memory_type [String, nil] Filter by memory type
        # @param category [String, nil] Filter by category
        # @param limit [Integer, nil] Maximum results
        # @param cursor [String, nil] Pagination cursor
        # @return [Array<Hash>] List of memory items
        def list_with(memory_type: nil, category: nil, limit: nil, cursor: nil)
          params = {}
          params[:memory_type] = memory_type if memory_type
          params[:category] = category if category
          params[:limit] = limit if limit
          params[:cursor] = cursor if cursor
          client.get("memory/items", params: params)
        end

        # Clears all memory.
        #
        # @return [void]
        def clear
          client.delete("memory/items")
          nil
        end

        # Clears memory by type.
        #
        # @param memory_type [String] Memory type to clear
        # @return [void]
        def clear_by_type(memory_type)
          client.post("memory/clear", body: { memory_type: memory_type })
          nil
        end

        # Gets all memory categories.
        #
        # @return [Array<String>] List of categories
        def categories
          result = client.get("memory/categories")
          result.is_a?(Hash) ? (result["categories"] || []) : []
        end

        # Gets memory statistics.
        #
        # @return [Hash] Memory stats
        def stats
          client.get("memory/stats")
        end
      end
    end
  end
end
