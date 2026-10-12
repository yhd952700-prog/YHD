package com.liuhao.sdk.exceptions;

/**
 * Thrown when a request times out.
 */
public class TimeoutException extends LiuHaoException {

    public TimeoutException(String message) {
        super(message);
    }

    public TimeoutException(String message, Throwable cause) {
        super(message, cause);
    }
}
