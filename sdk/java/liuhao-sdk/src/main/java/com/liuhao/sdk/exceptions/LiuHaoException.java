package com.liuhao.sdk.exceptions;

/**
 * Base exception for all LiuHao SDK errors.
 */
public class LiuHaoException extends RuntimeException {

    private final int statusCode;
    private final String requestId;

    public LiuHaoException(String message) {
        super(message);
        this.statusCode = 0;
        this.requestId = null;
    }

    public LiuHaoException(String message, Throwable cause) {
        super(message, cause);
        this.statusCode = 0;
        this.requestId = null;
    }

    public LiuHaoException(String message, int statusCode, String requestId) {
        super(message);
        this.statusCode = statusCode;
        this.requestId = requestId;
    }

    public LiuHaoException(String message, int statusCode, String requestId, Throwable cause) {
        super(message, cause);
        this.statusCode = statusCode;
        this.requestId = requestId;
    }

    public int getStatusCode() {
        return statusCode;
    }

    public String getRequestId() {
        return requestId;
    }
}
