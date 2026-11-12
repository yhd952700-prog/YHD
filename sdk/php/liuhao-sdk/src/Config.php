<?php

namespace LiuHao\SDK;

/**
 * Configuration class for the LiuHao SDK.
 *
 * Usage:
 * <code>
 * $config = (new Config())
 *     ->setApiKey('your-api-key')
 *     ->setBaseUrl('https://api.liuhao.ai/v1')
 *     ->setTimeout(30)
 *     ->setMaxRetries(3);
 * </code>
 */
class Config
{
    /** @var string Default API base URL */
    const DEFAULT_BASE_URL = 'https://api.liuhao.ai/v1';

    /** @var int Default timeout in seconds */
    const DEFAULT_TIMEOUT = 30;

    /** @var int Default max retries */
    const DEFAULT_MAX_RETRIES = 3;

    /** @var int Default initial retry delay in milliseconds */
    const DEFAULT_INITIAL_RETRY_DELAY_MS = 1000;

    /** @var float Default retry backoff multiplier */
    const DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0;

    /** @var string Default user agent */
    const DEFAULT_USER_AGENT = 'LiuHao-PHP-SDK/1.0.0';

    /** @var string */
    private $baseUrl;

    /** @var string */
    private $apiKey;

    /** @var int Timeout in seconds */
    private $timeout;

    /** @var int */
    private $maxRetries;

    /** @var int Initial retry delay in milliseconds */
    private $initialRetryDelayMs;

    /** @var float */
    private $retryBackoffMultiplier;

    /** @var string */
    private $userAgent;

    public function __construct()
    {
        $this->baseUrl = self::DEFAULT_BASE_URL;
        $this->timeout = self::DEFAULT_TIMEOUT;
        $this->maxRetries = self::DEFAULT_MAX_RETRIES;
        $this->initialRetryDelayMs = self::DEFAULT_INITIAL_RETRY_DELAY_MS;
        $this->retryBackoffMultiplier = self::DEFAULT_RETRY_BACKOFF_MULTIPLIER;
        $this->userAgent = self::DEFAULT_USER_AGENT;
    }

    /**
     * @return string
     */
    public function getBaseUrl()
    {
        return $this->baseUrl;
    }

    /**
     * @param string $baseUrl
     * @return $this
     */
    public function setBaseUrl($baseUrl)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        return $this;
    }

    /**
     * @return string
     */
    public function getApiKey()
    {
        return $this->apiKey;
    }

    /**
     * @param string $apiKey
     * @return $this
     */
    public function setApiKey($apiKey)
    {
        $this->apiKey = $apiKey;
        return $this;
    }

    /**
     * @return int
     */
    public function getTimeout()
    {
        return $this->timeout;
    }

    /**
     * @param int $timeout Timeout in seconds
     * @return $this
     */
    public function setTimeout($timeout)
    {
        if ($timeout <= 0) {
            throw new \InvalidArgumentException('Timeout must be positive');
        }
        $this->timeout = $timeout;
        return $this;
    }

    /**
     * @return int
     */
    public function getMaxRetries()
    {
        return $this->maxRetries;
    }

    /**
     * @param int $maxRetries
     * @return $this
     */
    public function setMaxRetries($maxRetries)
    {
        if ($maxRetries < 0) {
            throw new \InvalidArgumentException('maxRetries must be non-negative');
        }
        $this->maxRetries = $maxRetries;
        return $this;
    }

    /**
     * @return int
     */
    public function getInitialRetryDelayMs()
    {
        return $this->initialRetryDelayMs;
    }

    /**
     * @param int $delayMs
     * @return $this
     */
    public function setInitialRetryDelayMs($delayMs)
    {
        if ($delayMs <= 0) {
            throw new \InvalidArgumentException('initialRetryDelayMs must be positive');
        }
        $this->initialRetryDelayMs = $delayMs;
        return $this;
    }

    /**
     * @return float
     */
    public function getRetryBackoffMultiplier()
    {
        return $this->retryBackoffMultiplier;
    }

    /**
     * @param float $multiplier
     * @return $this
     */
    public function setRetryBackoffMultiplier($multiplier)
    {
        if ($multiplier < 1.0) {
            throw new \InvalidArgumentException('retryBackoffMultiplier must be >= 1.0');
        }
        $this->retryBackoffMultiplier = $multiplier;
        return $this;
    }

    /**
     * @return string
     */
    public function getUserAgent()
    {
        return $this->userAgent;
    }

    /**
     * @param string $userAgent
     * @return $this
     */
    public function setUserAgent($userAgent)
    {
        $this->userAgent = $userAgent;
        return $this;
    }

    /**
     * Validates the configuration.
     *
     * @throws \InvalidArgumentException
     */
    public function validate()
    {
        if (empty($this->apiKey)) {
            throw new \InvalidArgumentException('apiKey is required');
        }
        if (empty($this->baseUrl)) {
            throw new \InvalidArgumentException('baseUrl is required');
        }
    }
}
