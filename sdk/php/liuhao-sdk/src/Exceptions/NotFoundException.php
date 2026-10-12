<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when a requested resource is not found (HTTP 404).
 */
class NotFoundException extends LiuHaoException
{
    public function __construct($message = '', $requestId = null, $previous = null)
    {
        parent::__construct($message, 404, $requestId, $previous);
    }
}
