package com.liuhao.sdk.exceptions;

import java.util.Collections;
import java.util.List;

/**
 * Thrown when request validation fails (HTTP 400).
 */
public class ValidationException extends LiuHaoException {

    private final List<String> fieldErrors;

    public ValidationException(String message) {
        super(message, 400, null);
        this.fieldErrors = Collections.emptyList();
    }

    public ValidationException(String message, List<String> fieldErrors) {
        super(message, 400, null);
        this.fieldErrors = fieldErrors != null ? fieldErrors : Collections.emptyList();
    }

    public ValidationException(String message, String requestId, List<String> fieldErrors) {
        super(message, 400, requestId);
        this.fieldErrors = fieldErrors != null ? fieldErrors : Collections.emptyList();
    }

    public List<String> getFieldErrors() {
        return fieldErrors;
    }
}
