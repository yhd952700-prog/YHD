package com.liuhao.sdk.exceptions;

/**
 * Thrown when rate limits are exceeded (HTTP 429).
 */
public class RateLimitException extends LiuHaoException {

    private final int retryAfterSeconds;

    public RateLimitException(String message) {
        super(message, 429, null);
        this.retryAfterSeconds = 0;
    }

    public RateLimitException(String message, String requestId, int retryAfterSeconds) {
        super(message, 429, requestId);
        this.retryAfterSeconds = retryAfterSeconds;
    }

    public int getRetryAfterSeconds() {
        return retryAfterSeconds;
    }
}
