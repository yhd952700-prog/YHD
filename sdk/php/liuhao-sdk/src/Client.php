<?php

namespace LiuHao\SDK;

use GuzzleHttp\Client as GuzzleClient;
use GuzzleHttp\Exception\GuzzleException;
use GuzzleHttp\Exception\ConnectException;
use GuzzleHttp\Exception\RequestException;
use GuzzleHttp\HandlerStack;
use LiuHao\SDK\Exceptions\APIException;
use LiuHao\SDK\Exceptions\AuthenticationException;
use LiuHao\SDK\Exceptions\LiuHaoException;
use LiuHao\SDK\Exceptions\NotFoundException;
use LiuHao\SDK\Exceptions\RateLimitException;
use LiuHao\SDK\Exceptions\TimeoutException;
use LiuHao\SDK\Exceptions\ValidationException;
use LiuHao\SDK\Resources\Agents;
use LiuHao\SDK\Resources\Knowledge;
use LiuHao\SDK\Resources\Memory;

/**
 * Main client for the LiuHao AI OS API.
 *
 * Usage:
 * <code>
 * $client = new LiuHao\SDK\Client('your-api-key');
 * $agents = $client->agents()->list();
 * </code>
 */
class Client
{
    /** @var Config */
    private $config;

    /** @var GuzzleClient */
    private $httpClient;

    /** @var Agents */
    private $agents;

    /** @var Memory */
    private $memory;

    /** @var Knowledge */
    private $knowledge;

    /**
     * @param string|Config $apiKeyOrConfig API key string or Config instance
     * @throws \InvalidArgumentException
     */
    public function __construct($apiKeyOrConfig)
    {
        if (is_string($apiKeyOrConfig)) {
            $this->config = (new Config())->setApiKey($apiKeyOrConfig);
        } elseif ($apiKeyOrConfig instanceof Config) {
            $this->config = $apiKeyOrConfig;
        } else {
            throw new \InvalidArgumentException(
                'First argument must be an API key string or a Config instance'
            );
        }

        $this->config->validate();
        $this->httpClient = $this->createHttpClient();
        $this->agents = new Agents($this);
        $this->memory = new Memory($this);
        $this->knowledge = new Knowledge($this);
    }

    /**
     * Creates the Guzzle HTTP client.
     *
     * @return GuzzleClient
     */
    private function createHttpClient()
    {
        return new GuzzleClient([
            'base_uri' => $this->config->getBaseUrl() . '/',
            'timeout' => $this->config->getTimeout(),
            'connect_timeout' => $this->config->getTimeout(),
            'headers' => [
                'User-Agent' => $this->config->getUserAgent(),
                'Accept' => 'application/json',
                'Authorization' => 'Bearer ' . $this->config->getApiKey(),
            ],
        ]);
    }

    /**
     * @return Config
     */
    public function getConfig()
    {
        return $this->config;
    }

    /**
     * @return GuzzleClient
     */
    public function getHttpClient()
    {
        return $this->httpClient;
    }

    /**
     * Access the Agents API.
     *
     * @return Agents
     */
    public function agents()
    {
        return $this->agents;
    }

    /**
     * Access the Memory API.
     *
     * @return Memory
     */
    public function memory()
    {
        return $this->memory;
    }

    /**
     * Access the Knowledge API.
     *
     * @return Knowledge
     */
    public function knowledge()
    {
        return $this->knowledge;
    }

    /**
     * Sends a GET request with retry logic.
     *
     * @param string $path
     * @param array $query
     * @return mixed
     * @throws LiuHaoException
     */
    public function get($path, array $query = [])
    {
        return $this->request('GET', $path, ['query' => $query]);
    }

    /**
     * Sends a POST request with retry logic.
     *
     * @param string $path
     * @param array|object|null $body
     * @return mixed
     * @throws LiuHaoException
     */
    public function post($path, $body = null)
    {
        $options = [];
        if ($body !== null) {
            $options['json'] = $body;
        }
        return $this->request('POST', $path, $options);
    }

    /**
     * Sends a PUT request with retry logic.
     *
     * @param string $path
     * @param array|object $body
     * @return mixed
     * @throws LiuHaoException
     */
    public function put($path, $body)
    {
        return $this->request('PUT', $path, ['json' => $body]);
    }

    /**
     * Sends a PATCH request with retry logic.
     *
     * @param string $path
     * @param array|object $body
     * @return mixed
     * @throws LiuHaoException
     */
    public function patch($path, $body)
    {
        return $this->request('PATCH', $path, ['json' => $body]);
    }

    /**
     * Sends a DELETE request with retry logic.
     *
     * @param string $path
     * @param array|object|null $body
     * @return mixed
     * @throws LiuHaoException
     */
    public function delete($path, $body = null)
    {
        $options = [];
        if ($body !== null) {
            $options['json'] = $body;
        }
        return $this->request('DELETE', $path, $options);
    }

    /**
     * Executes an HTTP request with retry logic.
     *
     * @param string $method
     * @param string $path
     * @param array $options
     * @return mixed
     * @throws LiuHaoException
     */
    private function request($method, $path, array $options = [])
    {
        $attempts = 0;
        $delayMs = $this->config->getInitialRetryDelayMs();
        $lastException = null;

        while ($attempts <= $this->config->getMaxRetries()) {
            try {
                $response = $this->httpClient->request($method, $path, $options);
                return $this->handleResponse($response);
            } catch (RequestException $e) {
                $lastException = $e;
                if ($e->hasResponse()) {
                    $statusCode = $e->getResponse()->getStatusCode();
                    if ($this->isRetryableStatus($statusCode) && $attempts < $this->config->getMaxRetries()) {
                        // Will retry
                    } else {
                        throw $this->createExceptionFromResponse($e->getResponse());
                    }
                } elseif ($e instanceof ConnectException || $this->isTimeoutException($e)) {
                    // Network errors are retryable
                    if ($attempts >= $this->config->getMaxRetries()) {
                        if ($this->isTimeoutException($e)) {
                            throw new TimeoutException('Request timed out', $e);
                        }
                        throw new LiuHaoException('Network error: ' . $e->getMessage(), null, null, $e);
                    }
                } else {
                    throw new LiuHaoException($e->getMessage(), null, null, $e);
                }
            } catch (GuzzleException $e) {
                $lastException = $e;
                if ($attempts >= $this->config->getMaxRetries()) {
                    throw new LiuHaoException('Request failed: ' . $e->getMessage(), null, null, $e);
                }
            }

            $attempts++;
            if ($attempts <= $this->config->getMaxRetries()) {
                usleep($delayMs * 1000);
                $delayMs = (int)($delayMs * $this->config->getRetryBackoffMultiplier());
            }
        }

        if ($lastException) {
            if ($this->isTimeoutException($lastException)) {
                throw new TimeoutException(
                    'Request timed out after ' . ($this->config->getMaxRetries() + 1) . ' attempts',
                    $lastException
                );
            }
            throw new LiuHaoException(
                'Request failed after ' . ($this->config->getMaxRetries() + 1) . ' attempts: ' . $lastException->getMessage(),
                null,
                null,
                $lastException
            );
        }

        throw new LiuHaoException('Max retries exceeded');
    }

    /**
     * Handles a successful response.
     *
     * @param \Psr\Http\Message\ResponseInterface $response
     * @return mixed
     */
    private function handleResponse($response)
    {
        $body = (string)$response->getBody();
        if ($body === '') {
            return null;
        }
        return json_decode($body, true);
    }

    /**
     * Creates the appropriate exception from an error response.
     *
     * @param \Psr\Http\Message\ResponseInterface $response
     * @return LiuHaoException
     */
    private function createExceptionFromResponse($response)
    {
        $statusCode = $response->getStatusCode();
        $body = (string)$response->getBody();
        $requestId = $response->getHeaderLine('X-Request-ID') ?: null;
        $data = json_decode($body, true);
        $message = $this->extractErrorMessage($data, $body);

        switch ($statusCode) {
            case 400:
                $fieldErrors = isset($data['details']) ? $data['details'] : (isset($data['errors']) ? $data['errors'] : []);
                return new ValidationException($message, is_array($fieldErrors) ? $fieldErrors : [], $requestId);
            case 401:
                return new AuthenticationException($message, $requestId);
            case 404:
                return new NotFoundException($message, $requestId);
            case 429:
                $retryAfter = (int)$response->getHeaderLine('Retry-After');
                return new RateLimitException($message, $retryAfter, $requestId);
            default:
                if ($statusCode >= 500) {
                    return new APIException($message, $statusCode, $requestId);
                }
                return new LiuHaoException($message, $statusCode, $requestId);
        }
    }

    /**
     * Extracts error message from response data.
     *
     * @param mixed $data
     * @param string $body
     * @return string
     */
    private function extractErrorMessage($data, $body)
    {
        if (is_array($data)) {
            if (isset($data['message'])) {
                return $data['message'];
            }
            if (isset($data['error'])) {
                return is_string($data['error']) ? $data['error'] : json_encode($data['error']);
            }
        }
        return $body ?: 'Unknown error';
    }

    /**
     * @param int $statusCode
     * @return bool
     */
    private function isRetryableStatus($statusCode)
    {
        return in_array($statusCode, [429, 502, 503, 504], true);
    }

    /**
     * @param \Exception $e
     * @return bool
     */
    private function isTimeoutException($e)
    {
        $msg = strtolower($e->getMessage());
        return strpos($msg, 'timeout') !== false
            || strpos($msg, 'timed out') !== false;
    }
}
