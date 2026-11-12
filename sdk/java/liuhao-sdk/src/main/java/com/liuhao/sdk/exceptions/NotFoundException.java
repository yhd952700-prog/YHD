package com.liuhao.sdk.exceptions;

/**
 * Thrown when a requested resource is not found (HTTP 404).
 */
public class NotFoundException extends LiuHaoException {

    public NotFoundException(String message) {
        super(message, 404, null);
    }

    public NotFoundException(String message, String requestId) {
        super(message, 404, requestId);
    }

    public NotFoundException(String message, String requestId, Throwable cause) {
        super(message, 404, requestId, cause);
    }
}
