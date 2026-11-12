<?php

namespace LiuHao\SDK\Exceptions;

/**
 * Thrown when request validation fails (HTTP 400).
 */
class ValidationException extends LiuHaoException
{
    /** @var array */
    protected $fieldErrors;

    public function __construct($message = '', array $fieldErrors = [], $requestId = null, $previous = null)
    {
        parent::__construct($message, 400, $requestId, $previous);
        $this->fieldErrors = $fieldErrors;
    }

    /**
     * @return array
     */
    public function getFieldErrors()
    {
        return $this->fieldErrors;
    }
}
