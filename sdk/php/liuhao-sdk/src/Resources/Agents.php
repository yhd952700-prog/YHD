<?php

namespace LiuHao\SDK\Resources;

use LiuHao\SDK\Client;
use LiuHao\SDK\Exceptions\LiuHaoException;

/**
 * Resource class for managing AI Agents.
 */
class Agents
{
    /** @var Client */
    private $client;

    /** @var string */
    private $basePath = 'agents';

    /**
     * @param Client $client
     */
    public function __construct(Client $client)
    {
        $this->client = $client;
    }

    /**
     * Lists all agents.
     *
     * @return array
     * @throws LiuHaoException
     */
    public function list()
    {
        return $this->client->get($this->basePath);
    }

    /**
     * Lists agents with pagination options.
     *
     * @param int|null $limit
     * @param string|null $cursor
     * @return array
     * @throws LiuHaoException
     */
    public function listWithOptions($limit = null, $cursor = null)
    {
        $query = [];
        if ($limit !== null) {
            $query['limit'] = $limit;
        }
        if ($cursor !== null) {
            $query['cursor'] = $cursor;
        }
        return $this->client->get($this->basePath, $query);
    }

    /**
     * Gets an agent by ID.
     *
     * @param string $agentId
     * @return array
     * @throws LiuHaoException
     */
    public function get($agentId)
    {
        return $this->client->get($this->basePath . '/' . $agentId);
    }

    /**
     * Creates a new agent.
     *
     * @param array $agentData
     * @return array
     * @throws LiuHaoException
     */
    public function create(array $agentData)
    {
        return $this->client->post($this->basePath, $agentData);
    }

    /**
     * Creates an agent with just a name and model.
     *
     * @param string $name
     * @param string $modelName
     * @return array
     * @throws LiuHaoException
     */
    public function createSimple($name, $modelName)
    {
        return $this->client->post($this->basePath, [
            'name' => $name,
            'model_name' => $modelName,
        ]);
    }

    /**
     * Updates an agent.
     *
     * @param string $agentId
     * @param array $agentData
     * @return array
     * @throws LiuHaoException
     */
    public function update($agentId, array $agentData)
    {
        return $this->client->put($this->basePath . '/' . $agentId, $agentData);
    }

    /**
     * Partially updates an agent.
     *
     * @param string $agentId
     * @param array $updates
     * @return array
     * @throws LiuHaoException
     */
    public function patch($agentId, array $updates)
    {
        return $this->client->patch($this->basePath . '/' . $agentId, $updates);
    }

    /**
     * Deletes an agent.
     *
     * @param string $agentId
     * @return void
     * @throws LiuHaoException
     */
    public function delete($agentId)
    {
        $this->client->delete($this->basePath . '/' . $agentId);
    }

    /**
     * Executes an agent with the given input.
     *
     * @param string $agentId
     * @param string $input
     * @return string
     * @throws LiuHaoException
     */
    public function execute($agentId, $input)
    {
        $result = $this->client->post($this->basePath . '/' . $agentId . '/execute', [
            'input' => $input,
        ]);
        return isset($result['output']) ? $result['output'] : '';
    }

    /**
     * Executes an agent with full parameters.
     *
     * @param string $agentId
     * @param array $params
     * @return array
     * @throws LiuHaoException
     */
    public function executeWithParams($agentId, array $params)
    {
        return $this->client->post($this->basePath . '/' . $agentId . '/execute', $params);
    }

    /**
     * Gets the current status of an agent.
     *
     * @param string $agentId
     * @return string
     * @throws LiuHaoException
     */
    public function getStatus($agentId)
    {
        $agent = $this->client->get($this->basePath . '/' . $agentId . '/status');
        return isset($agent['status']) ? $agent['status'] : 'unknown';
    }

    /**
     * Cancels a running agent execution.
     *
     * @param string $agentId
     * @return void
     * @throws LiuHaoException
     */
    public function cancel($agentId)
    {
        $this->client->post($this->basePath . '/' . $agentId . '/cancel');
    }

    /**
     * Gets metrics for an agent.
     *
     * @param string $agentId
     * @return array
     * @throws LiuHaoException
     */
    public function metrics($agentId)
    {
        return $this->client->get($this->basePath . '/' . $agentId . '/metrics');
    }

    /**
     * Pauses an agent.
     *
     * @param string $agentId
     * @return array
     * @throws LiuHaoException
     */
    public function pause($agentId)
    {
        return $this->client->post($this->basePath . '/' . $agentId . '/pause');
    }

    /**
     * Resumes a paused agent.
     *
     * @param string $agentId
     * @return array
     * @throws LiuHaoException
     */
    public function resume($agentId)
    {
        return $this->client->post($this->basePath . '/' . $agentId . '/resume');
    }

    /**
     * Lists tools available to an agent.
     *
     * @param string $agentId
     * @return array
     * @throws LiuHaoException
     */
    public function listTools($agentId)
    {
        return $this->client->get($this->basePath . '/' . $agentId . '/tools');
    }
}
