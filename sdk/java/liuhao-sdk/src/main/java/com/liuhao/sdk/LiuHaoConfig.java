package com.liuhao.sdk;

import java.util.Objects;
import java.util.concurrent.TimeUnit;

/**
 * Configuration class for the LiuHao AI OS SDK.
 *
 * <p>Use the {@link Builder} to create instances with custom settings.
 * Example usage:
 * <pre>{@code
 * LiuHaoConfig config = LiuHaoConfig.builder()
 *     .apiKey("your-api-key")
 *     .baseUrl("https://api.liuhao.ai")
 *     .timeout(30, TimeUnit.SECONDS)
 *     .maxRetries(3)
 *     .build();
 * }</pre>
 */
public class LiuHaoConfig {

    private static final String DEFAULT_BASE_URL = "https://api.liuhao.ai/v1";
    private static final long DEFAULT_TIMEOUT_SECONDS = 30;
    private static final int DEFAULT_MAX_RETRIES = 3;
    private static final long DEFAULT_INITIAL_RETRY_DELAY_MS = 1000;
    private static final double DEFAULT_RETRY_BACKOFF_MULTIPLIER = 2.0;

    private final String baseUrl;
    private final String apiKey;
    private final long timeout;
    private final TimeUnit timeoutUnit;
    private final int maxRetries;
    private final long initialRetryDelayMs;
    private final double retryBackoffMultiplier;
    private final String userAgent;

    private LiuHaoConfig(Builder builder) {
        this.baseUrl = Objects.requireNonNull(builder.baseUrl, "baseUrl must not be null");
        this.apiKey = builder.apiKey;
        this.timeout = builder.timeout;
        this.timeoutUnit = builder.timeoutUnit;
        this.maxRetries = builder.maxRetries;
        this.initialRetryDelayMs = builder.initialRetryDelayMs;
        this.retryBackoffMultiplier = builder.retryBackoffMultiplier;
        this.userAgent = builder.userAgent != null ? builder.userAgent : "LiuHao-Java-SDK/1.0.0";
    }

    /**
     * Creates a new builder with default values.
     */
    public static Builder builder() {
        return new Builder();
    }

    public String getBaseUrl() {
        return baseUrl;
    }

    public String getApiKey() {
        return apiKey;
    }

    public long getTimeout() {
        return timeout;
    }

    public TimeUnit getTimeoutUnit() {
        return timeoutUnit;
    }

    public long getTimeoutMs() {
        return timeoutUnit.toMillis(timeout);
    }

    public int getMaxRetries() {
        return maxRetries;
    }

    public long getInitialRetryDelayMs() {
        return initialRetryDelayMs;
    }

    public double getRetryBackoffMultiplier() {
        return retryBackoffMultiplier;
    }

    public String getUserAgent() {
        return userAgent;
    }

    /**
     * Builder for {@link LiuHaoConfig}.
     */
    public static class Builder {
        private String baseUrl = DEFAULT_BASE_URL;
        private String apiKey;
        private long timeout = DEFAULT_TIMEOUT_SECONDS;
        private TimeUnit timeoutUnit = TimeUnit.SECONDS;
        private int maxRetries = DEFAULT_MAX_RETRIES;
        private long initialRetryDelayMs = DEFAULT_INITIAL_RETRY_DELAY_MS;
        private double retryBackoffMultiplier = DEFAULT_RETRY_BACKOFF_MULTIPLIER;
        private String userAgent;

        private Builder() {
        }

        /**
         * Sets the base URL for the LiuHao API.
         * Default: {@code https://api.liuhao.ai/v1}
         */
        public Builder baseUrl(String baseUrl) {
            this.baseUrl = baseUrl;
            return this;
        }

        /**
         * Sets the API key used for authentication.
         */
        public Builder apiKey(String apiKey) {
            this.apiKey = apiKey;
            return this;
        }

        /**
         * Sets the request timeout.
         * Default: 30 seconds.
         */
        public Builder timeout(long timeout, TimeUnit unit) {
            if (timeout <= 0) {
                throw new IllegalArgumentException("timeout must be positive");
            }
            this.timeout = timeout;
            this.timeoutUnit = Objects.requireNonNull(unit, "unit must not be null");
            return this;
        }

        /**
         * Sets the maximum number of retry attempts for failed requests.
         * Default: 3.
         */
        public Builder maxRetries(int maxRetries) {
            if (maxRetries < 0) {
                throw new IllegalArgumentException("maxRetries must be non-negative");
            }
            this.maxRetries = maxRetries;
            return this;
        }

        /**
         * Sets the initial delay in milliseconds before the first retry.
         * Default: 1000 ms.
         */
        public Builder initialRetryDelayMs(long initialRetryDelayMs) {
            if (initialRetryDelayMs <= 0) {
                throw new IllegalArgumentException("initialRetryDelayMs must be positive");
            }
            this.initialRetryDelayMs = initialRetryDelayMs;
            return this;
        }

        /**
         * Sets the backoff multiplier for retry delays.
         * Default: 2.0 (exponential backoff).
         */
        public Builder retryBackoffMultiplier(double retryBackoffMultiplier) {
            if (retryBackoffMultiplier < 1.0) {
                throw new IllegalArgumentException("retryBackoffMultiplier must be >= 1.0");
            }
            this.retryBackoffMultiplier = retryBackoffMultiplier;
            return this;
        }

        /**
         * Sets a custom User-Agent header value.
         */
        public Builder userAgent(String userAgent) {
            this.userAgent = userAgent;
            return this;
        }

        /**
         * Builds the configuration.
         *
         * @throws IllegalStateException if apiKey is not set
         */
        public LiuHaoConfig build() {
            if (apiKey == null || apiKey.isEmpty()) {
                throw new IllegalStateException("apiKey must be set");
            }
            return new LiuHaoConfig(this);
        }
    }
}
