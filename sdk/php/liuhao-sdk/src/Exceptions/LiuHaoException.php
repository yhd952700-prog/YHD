<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Base exception for all LiuHao SDK errors.
 */
class LiuHaoException extends \RuntimeException
{
    /** @var int|null */
    protected $statusCode;

    /** @var string|null */
    protected $requestId;

    /**
     * @param string $message
     * @param int|null $statusCode
     * @param string|null $requestId
     * @param \Throwable|null $previous
     */
    public function __construct($message = '', $statusCode = null, $requestId = null, $previous = null)
    {
        parent::__construct($message, 0, $previous);
        $this->statusCode = $statusCode;
        $this->requestId = $requestId;
    }

    /**
     * @return int|null
     */
    public function getStatusCode()
    {
        return $this->statusCode;
    }

    /**
     * @return string|null
     */
    public function getRequestId()
    {
        return $this->requestId;
    }
}
