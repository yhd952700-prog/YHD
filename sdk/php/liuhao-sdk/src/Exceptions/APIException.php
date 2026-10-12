<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when the API returns an unexpected error (HTTP 5xx).
 */
class APIException extends LiuHaoException
{
    public function __construct($message = '', $statusCode = 500, $requestId = null, $previous = null)
    {
        parent::__construct($message, $statusCode, $requestId, $previous);
    }
}
