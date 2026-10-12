package com.liuhao.sdk.exceptions;

/**
 * Thrown when the API returns an unexpected error (HTTP 5xx).
 */
public class APIException extends LiuHaoException {

    public APIException(String message, int statusCode) {
        super(message, statusCode, null);
    }

    public APIException(String message, int statusCode, String requestId) {
        super(message, statusCode, requestId);
    }

    public APIException(String message, int statusCode, String requestId, Throwable cause) {
        super(message, statusCode, requestId, cause);
    }
}
