package com.liuhao.sdk.exceptions;

/**
 * Thrown when authentication fails (HTTP 401).
 */
public class AuthenticationException extends LiuHaoException {

    public AuthenticationException(String message) {
        super(message, 401, null);
    }

    public AuthenticationException(String message, String requestId) {
        super(message, 401, requestId);
    }

    public AuthenticationException(String message, String requestId, Throwable cause) {
        super(message, 401, requestId, cause);
    }
}
