# frozen_string_literal: true

require_relative "base"

module LiuHao
  module SDK
    module Resources
      # Resource class for managing knowledge bases.
      class Knowledge < Base
        # Lists all knowledge bases.
        #
        # @return [Array<Hash>] List of knowledge bases
        def list_bases
          client.get("knowledge/bases")
        end

        # Lists knowledge bases with pagination.
        #
        # @param limit [Integer, nil] Maximum results
        # @param cursor [String, nil] Pagination cursor
        # @return [Array<Hash>] List of knowledge bases
        def list_bases_with(limit: nil, cursor: nil)
          params = {}
          params[:limit] = limit if limit
          params[:cursor] = cursor if cursor
          client.get("knowledge/bases", params: params)
        end

        # Creates a new knowledge base.
        #
        # @param name [String] Knowledge base name
        # @param description [String] Description
        # @param options [Hash] Additional options
        # @return [Hash] Created knowledge base
        def create_base(name:, description: "", **options)
          body = { name: name, description: description }.merge(options)
          client.post("knowledge/bases", body: body)
        end

        # Creates a knowledge base using a block.
        #
        # @yield [Hash] Knowledge base attributes
        # @return [Hash] Created knowledge base
        def create_base_with(&block)
          attrs = {}
          yield attrs if block_given?
          client.post("knowledge/bases", body: attrs)
        end

        # Gets a knowledge base by ID.
        #
        # @param base_id [String] Knowledge base ID
        # @return [Hash] Knowledge base data
        def get_base(base_id)
          client.get("knowledge/bases/#{base_id}")
        end

        # Updates a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @param attrs [Hash] Attributes to update
        # @return [Hash] Updated knowledge base
        def update_base(base_id, **attrs)
          client.patch("knowledge/bases/#{base_id}", body: attrs)
        end

        # Deletes a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @return [void]
        def delete_base(base_id)
          client.delete("knowledge/bases/#{base_id}")
          nil
        end

        # Uploads a document to a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @param title [String] Document title
        # @param content [String] Document content
        # @param options [Hash] Additional options
        # @return [Hash] Created document
        def upload_document(base_id, title:, content:, **options)
          body = { title: title, content: content }.merge(options)
          client.post("knowledge/bases/#{base_id}/documents", body: body)
        end

        # Searches within a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @param query [String] Search query
        # @param top_k [Integer] Number of results
        # @param min_score [Float] Minimum score threshold
        # @return [Array<Hash>] Search results
        def search(base_id, query, top_k: 5, min_score: 0.0)
          body = { query: query, top_k: top_k, min_score: min_score }
          client.post("knowledge/bases/#{base_id}/search", body: body)
        end

        # Lists documents in a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @return [Array<Hash>] List of documents
        def list_documents(base_id)
          result = client.get("knowledge/bases/#{base_id}/documents")
          result.is_a?(Hash) ? (result["documents"] || []) : []
        end

        # Lists documents with pagination.
        #
        # @param base_id [String] Knowledge base ID
        # @param limit [Integer, nil] Maximum results
        # @param cursor [String, nil] Pagination cursor
        # @return [Array<Hash>] List of documents
        def list_documents_with(base_id, limit: nil, cursor: nil)
          params = {}
          params[:limit] = limit if limit
          params[:cursor] = cursor if cursor
          result = client.get("knowledge/bases/#{base_id}/documents", params: params)
          result.is_a?(Hash) ? (result["documents"] || []) : []
        end

        # Deletes a document from a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @param document_id [String] Document ID
        # @return [void]
        def delete_document(base_id, document_id)
          client.delete("knowledge/bases/#{base_id}/documents/#{document_id}")
          nil
        end

        # Gets statistics for a knowledge base.
        #
        # @param base_id [String] Knowledge base ID
        # @return [Hash] Knowledge base stats
        def base_stats(base_id)
          client.get("knowledge/bases/#{base_id}/stats")
        end
      end
    end
  end
end
