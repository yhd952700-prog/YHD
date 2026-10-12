<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when rate limits are exceeded (HTTP 429).
 */
class RateLimitException extends LiuHaoException
{
    /** @var int */
    protected $retryAfterSeconds;

    public function __construct($message = '', $retryAfterSeconds = 0, $requestId = null, $previous = null)
    {
        parent::__construct($message, 429, $requestId, $previous);
        $this->retryAfterSeconds = $retryAfterSeconds;
    }

    /**
     * @return int
     */
    public function getRetryAfterSeconds()
    {
        return $this->retryAfterSeconds;
    }
}
