<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when authentication fails (HTTP 401).
 */
class AuthenticationException extends LiuHaoException
{
    public function __construct($message = '', $requestId = null, $previous = null)
    {
        parent::__construct($message, 401, $requestId, $previous);
    }
}
