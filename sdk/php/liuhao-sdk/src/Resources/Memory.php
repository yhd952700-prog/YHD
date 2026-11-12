<?php

namespace LiuHao\SDK\Resources;

use LiuHao\SDK\Client;
use LiuHao\SDK\Exceptions\LiuHaoException;

/**
 * Resource class for managing the memory system.
 */
class Memory
{
    /** @var Client */
    private $client;

    /** @var string */
    private $basePath = 'memory';

    /**
     * @param Client $client
     */
    public function __construct(Client $client)
    {
        $this->client = $client;
    }

    /**
     * Stores a new memory item.
     *
     * @param array $memoryData
     * @return array
     * @throws LiuHaoException
     */
    public function store(array $memoryData)
    {
        return $this->client->post($this->basePath . '/items', $memoryData);
    }

    /**
     * Stores a simple memory with content and type.
     *
     * @param string $content
     * @param string $memoryType
     * @return array
     * @throws LiuHaoException
     */
    public function storeSimple($content, $memoryType)
    {
        return $this->client->post($this->basePath . '/items', [
            'content' => $content,
            'memory_type' => $memoryType,
        ]);
    }

    /**
     * Stores a memory with full parameters.
     *
     * @param string $content
     * @param string $memoryType
     * @param string|null $category
     * @param float $importance
     * @param array|null $metadata
     * @return array
     * @throws LiuHaoException
     */
    public function storeWithOptions($content, $memoryType, $category = null, $importance = 0.5, $metadata = null)
    {
        $body = [
            'content' => $content,
            'memory_type' => $memoryType,
            'importance' => $importance,
        ];
        if ($category !== null) {
            $body['category'] = $category;
        }
        if ($metadata !== null) {
            $body['metadata'] = $metadata;
        }
        return $this->client->post($this->basePath . '/items', $body);
    }

    /**
     * Retrieves a memory item by ID.
     *
     * @param string $memoryId
     * @return array
     * @throws LiuHaoException
     */
    public function retrieve($memoryId)
    {
        return $this->client->get($this->basePath . '/items/' . $memoryId);
    }

    /**
     * Updates a memory item.
     *
     * @param string $memoryId
     * @param array $memoryData
     * @return array
     * @throws LiuHaoException
     */
    public function update($memoryId, array $memoryData)
    {
        return $this->client->put($this->basePath . '/items/' . $memoryId, $memoryData);
    }

    /**
     * Partially updates a memory item.
     *
     * @param string $memoryId
     * @param array $updates
     * @return array
     * @throws LiuHaoException
     */
    public function patch($memoryId, array $updates)
    {
        return $this->client->patch($this->basePath . '/items/' . $memoryId, $updates);
    }

    /**
     * Deletes a memory item.
     *
     * @param string $memoryId
     * @return void
     * @throws LiuHaoException
     */
    public function delete($memoryId)
    {
        $this->client->delete($this->basePath . '/items/' . $memoryId);
    }

    /**
     * Searches memory by query.
     *
     * @param string $query
     * @return array
     * @throws LiuHaoException
     */
    public function search($query)
    {
        return $this->client->post($this->basePath . '/search', [
            'query' => $query,
        ]);
    }

    /**
     * Searches memory with filters.
     *
     * @param string $query
     * @param string|null $memoryType
     * @param string|null $category
     * @param int $limit
     * @return array
     * @throws LiuHaoException
     */
    public function searchWithOptions($query, $memoryType = null, $category = null, $limit = 10)
    {
        $body = ['query' => $query, 'limit' => $limit];
        if ($memoryType !== null) {
            $body['memory_type'] = $memoryType;
        }
        if ($category !== null) {
            $body['category'] = $category;
        }
        return $this->client->post($this->basePath . '/search', $body);
    }

    /**
     * Lists memory items.
     *
     * @return array
     * @throws LiuHaoException
     */
    public function list()
    {
        return $this->client->get($this->basePath . '/items');
    }

    /**
     * Lists memory items with filters.
     *
     * @param string|null $memoryType
     * @param string|null $category
     * @param int|null $limit
     * @param string|null $cursor
     * @return array
     * @throws LiuHaoException
     */
    public function listWithOptions($memoryType = null, $category = null, $limit = null, $cursor = null)
    {
        $query = [];
        if ($memoryType !== null) {
            $query['memory_type'] = $memoryType;
        }
        if ($category !== null) {
            $query['category'] = $category;
        }
        if ($limit !== null) {
            $query['limit'] = $limit;
        }
        if ($cursor !== null) {
            $query['cursor'] = $cursor;
        }
        return $this->client->get($this->basePath . '/items', $query);
    }

    /**
     * Clears all memory.
     *
     * @return void
     * @throws LiuHaoException
     */
    public function clear()
    {
        $this->client->delete($this->basePath . '/items');
    }

    /**
     * Clears memory by type.
     *
     * @param string $memoryType
     * @return void
     * @throws LiuHaoException
     */
    public function clearByType($memoryType)
    {
        $this->client->post($this->basePath . '/clear', [
            'memory_type' => $memoryType,
        ]);
    }

    /**
     * Gets all memory categories.
     *
     * @return array
     * @throws LiuHaoException
     */
    public function categories()
    {
        $result = $this->client->get($this->basePath . '/categories');
        return isset($result['categories']) ? $result['categories'] : [];
    }

    /**
     * Gets memory statistics.
     *
     * @return array
     * @throws LiuHaoException
     */
    public function stats()
    {
        return $this->client->get($this->basePath . '/stats');
    }
}
