<?php

namespace LiuHao\SDK\Resources;

use LiuHao\SDK\Client;
use LiuHao\SDK\Exceptions\LiuHaoException;

/**
 * Resource class for managing knowledge bases.
 */
class Knowledge
{
    /** @var Client */
    private $client;

    /** @var string */
    private $basePath = 'knowledge';

    /**
     * @param Client $client
     */
    public function __construct(Client $client)
    {
        $this->client = $client;
    }

    /**
     * Lists all knowledge bases.
     *
     * @return array
     * @throws LiuHaoException
     */
    public function listBases()
    {
        return $this->client->get($this->basePath . '/bases');
    }

    /**
     * Lists knowledge bases with pagination.
     *
     * @param int|null $limit
     * @param string|null $cursor
     * @return array
     * @throws LiuHaoException
     */
    public function listBasesWithOptions($limit = null, $cursor = null)
    {
        $query = [];
        if ($limit !== null) {
            $query['limit'] = $limit;
        }
        if ($cursor !== null) {
            $query['cursor'] = $cursor;
        }
        return $this->client->get($this->basePath . '/bases', $query);
    }

    /**
     * Creates a new knowledge base.
     *
     * @param string $name
     * @param string $description
     * @return array
     * @throws LiuHaoException
     */
    public function createBase($name, $description = '')
    {
        return $this->client->post($this->basePath . '/bases', [
            'name' => $name,
            'description' => $description,
        ]);
    }

    /**
     * Creates a knowledge base with full options.
     *
     * @param string $name
     * @param string $description
     * @param string|null $embeddingModel
     * @param array $tags
     * @return array
     * @throws LiuHaoException
     */
    public function createBaseWithOptions($name, $description = '', $embeddingModel = null, array $tags = [])
    {
        $body = [
            'name' => $name,
            'description' => $description,
        ];
        if ($embeddingModel !== null) {
            $body['embedding_model'] = $embeddingModel;
        }
        if (!empty($tags)) {
            $body['tags'] = $tags;
        }
        return $this->client->post($this->basePath . '/bases', $body);
    }

    /**
     * Gets a knowledge base by ID.
     *
     * @param string $baseId
     * @return array
     * @throws LiuHaoException
     */
    public function getBase($baseId)
    {
        return $this->client->get($this->basePath . '/bases/' . $baseId);
    }

    /**
     * Updates a knowledge base.
     *
     * @param string $baseId
     * @param array $updates
     * @return array
     * @throws LiuHaoException
     */
    public function updateBase($baseId, array $updates)
    {
        return $this->client->patch($this->basePath . '/bases/' . $baseId, $updates);
    }

    /**
     * Deletes a knowledge base.
     *
     * @param string $baseId
     * @return void
     * @throws LiuHaoException
     */
    public function deleteBase($baseId)
    {
        $this->client->delete($this->basePath . '/bases/' . $baseId);
    }

    /**
     * Uploads a document to a knowledge base.
     *
     * @param string $baseId
     * @param string $title
     * @param string $content
     * @return array
     * @throws LiuHaoException
     */
    public function uploadDocument($baseId, $title, $content)
    {
        return $this->client->post($this->basePath . '/bases/' . $baseId . '/documents', [
            'title' => $title,
            'content' => $content,
        ]);
    }

    /**
     * Uploads a document with metadata.
     *
     * @param string $baseId
     * @param string $title
     * @param string $content
     * @param string|null $source
     * @param array|null $metadata
     * @return array
     * @throws LiuHaoException
     */
    public function uploadDocumentWithMetadata($baseId, $title, $content, $source = null, $metadata = null)
    {
        $body = [
            'title' => $title,
            'content' => $content,
        ];
        if ($source !== null) {
            $body['source'] = $source;
        }
        if ($metadata !== null) {
            $body['metadata'] = $metadata;
        }
        return $this->client->post($this->basePath . '/bases/' . $baseId . '/documents', $body);
    }

    /**
     * Searches within a knowledge base.
     *
     * @param string $baseId
     * @param string $query
     * @return array
     * @throws LiuHaoException
     */
    public function search($baseId, $query)
    {
        return $this->client->post($this->basePath . '/bases/' . $baseId . '/search', [
            'query' => $query,
        ]);
    }

    /**
     * Searches with additional options.
     *
     * @param string $baseId
     * @param string $query
     * @param int $topK
     * @param float $minScore
     * @return array
     * @throws LiuHaoException
     */
    public function searchWithOptions($baseId, $query, $topK = 5, $minScore = 0.0)
    {
        return $this->client->post($this->basePath . '/bases/' . $baseId . '/search', [
            'query' => $query,
            'top_k' => $topK,
            'min_score' => $minScore,
        ]);
    }

    /**
     * Lists documents in a knowledge base.
     *
     * @param string $baseId
     * @return array
     * @throws LiuHaoException
     */
    public function listDocuments($baseId)
    {
        $result = $this->client->get($this->basePath . '/bases/' . $baseId . '/documents');
        return isset($result['documents']) ? $result['documents'] : [];
    }

    /**
     * Lists documents with pagination.
     *
     * @param string $baseId
     * @param int|null $limit
     * @param string|null $cursor
     * @return array
     * @throws LiuHaoException
     */
    public function listDocumentsWithOptions($baseId, $limit = null, $cursor = null)
    {
        $query = [];
        if ($limit !== null) {
            $query['limit'] = $limit;
        }
        if ($cursor !== null) {
            $query['cursor'] = $cursor;
        }
        $result = $this->client->get($this->basePath . '/bases/' . $baseId . '/documents', $query);
        return isset($result['documents']) ? $result['documents'] : [];
    }

    /**
     * Deletes a document from a knowledge base.
     *
     * @param string $baseId
     * @param string $documentId
     * @return void
     * @throws LiuHaoException
     */
    public function deleteDocument($baseId, $documentId)
    {
        $this->client->delete($this->basePath . '/bases/' . $baseId . '/documents/' . $documentId);
    }

    /**
     * Gets statistics for a knowledge base.
     *
     * @param string $baseId
     * @return array
     * @throws LiuHaoException
     */
    public function baseStats($baseId)
    {
        return $this->client->get($this->basePath . '/bases/' . $baseId . '/stats');
    }
}
