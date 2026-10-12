<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when a request times out.
 */
class TimeoutException extends LiuHaoException
{
    public function __construct($message = '', $previous = null)
    {
        parent::__construct($message, null, null, $previous);
    }
}
