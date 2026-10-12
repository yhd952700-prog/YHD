package com.liuhao.sdk;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.liuhao.sdk.exceptions.APIException;
import com.liuhao.sdk.exceptions.AuthenticationException;
import com.liuhao.sdk.exceptions.LiuHaoException;
import com.liuhao.sdk.exceptions.NotFoundException;
import com.liuhao.sdk.exceptions.RateLimitException;
import com.liuhao.sdk.exceptions.TimeoutException;
import com.liuhao.sdk.exceptions.ValidationException;
import com.liuhao.sdk.resources.AgentsResource;
import com.liuhao.sdk.resources.HealthResource;
import com.liuhao.sdk.resources.KnowledgeResource;
import com.liuhao.sdk.resources.MemoryResource;
import com.liuhao.sdk.resources.TasksResource;
import okhttp3.HttpUrl;
import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.ResponseBody;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * Main client for the LiuHao AI OS API.
 *
 * <p>Create an instance using the builder pattern:
 * <pre>{@code
 * LiuHaoClient client = LiuHaoClient.builder()
 *     .apiKey("your-api-key")
 *     .build();
 *
 * List<Agent> agents = client.agents().list();
 * }</pre>
 */
public class LiuHaoClient {

    private static final Logger logger = LoggerFactory.getLogger(LiuHaoClient.class);
    private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");

    private final LiuHaoConfig config;
    private final OkHttpClient httpClient;
    private final Gson gson;

    private final AgentsResource agentsResource;
    private final MemoryResource memoryResource;
    private final KnowledgeResource knowledgeResource;
    private final TasksResource tasksResource;
    private final HealthResource healthResource;

    private LiuHaoClient(Builder builder) {
        this.config = builder.configBuilder.build();
        this.gson = new GsonBuilder()
                .setDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'")
                .create();

        this.httpClient = new OkHttpClient.Builder()
                .connectTimeout(Duration.ofMillis(config.getTimeoutMs()))
                .readTimeout(Duration.ofMillis(config.getTimeoutMs()))
                .writeTimeout(Duration.ofMillis(config.getTimeoutMs()))
                .build();

        this.agentsResource = new AgentsResource(this);
        this.memoryResource = new MemoryResource(this);
        this.knowledgeResource = new KnowledgeResource(this);
        this.tasksResource = new TasksResource(this);
        this.healthResource = new HealthResource(this);
    }

    public static Builder builder() {
        return new Builder();
    }

    public AgentsResource agents() {
        return agentsResource;
    }

    public MemoryResource memory() {
        return memoryResource;
    }

    public KnowledgeResource knowledge() {
        return knowledgeResource;
    }

    public TasksResource tasks() {
        return tasksResource;
    }

    public HealthResource health() {
        return healthResource;
    }

    public LiuHaoConfig getConfig() {
        return config;
    }

    public Gson getGson() {
        return gson;
    }

    /**
     * Executes a GET request with retry logic.
     */
    public <T> T get(String path, Class<T> responseType) {
        return request("GET", path, null, responseType);
    }

    /**
     * Executes a POST request with retry logic.
     */
    public <T> T post(String path, Object body, Class<T> responseType) {
        return request("POST", path, body, responseType);
    }

    /**
     * Executes a PUT request with retry logic.
     */
    public <T> T put(String path, Object body, Class<T> responseType) {
        return request("PUT", path, body, responseType);
    }

    /**
     * Executes a DELETE request with retry logic.
     */
    public <T> T delete(String path, Class<T> responseType) {
        return request("DELETE", path, null, responseType);
    }

    /**
     * Executes a PATCH request with retry logic.
     */
    public <T> T patch(String path, Object body, Class<T> responseType) {
        return request("PATCH", path, body, responseType);
    }

    /**
     * Executes an HTTP request with retry logic and error handling.
     */
    private <T> T request(String method, String path, Object body, Class<T> responseType) {
        String url = buildUrl(path);
        RequestBody requestBody = body != null ?
                RequestBody.create(gson.toJson(body), JSON) : null;

        Request.Builder requestBuilder = new Request.Builder()
                .url(url)
                .header("Authorization", "Bearer " + config.getApiKey())
                .header("User-Agent", config.getUserAgent())
                .header("Accept", "application/json");

        if ("GET".equals(method)) {
            requestBuilder.get();
        } else if ("DELETE".equals(method)) {
            requestBuilder.delete(requestBody);
        } else if ("POST".equals(method)) {
            requestBuilder.post(requestBody != null ? requestBody : RequestBody.create("", JSON));
        } else if ("PUT".equals(method)) {
            requestBuilder.put(requestBody);
        } else if ("PATCH".equals(method)) {
            requestBuilder.patch(requestBody);
        }

        Request request = requestBuilder.build();
        return executeWithRetry(request, responseType);
    }

    private <T> T executeWithRetry(Request request, Class<T> responseType) {
        int attempts = 0;
        long delayMs = config.getInitialRetryDelayMs();
        Exception lastException = null;

        while (attempts <= config.getMaxRetries()) {
            try {
                Response response = httpClient.newCall(request).execute();
                return handleResponse(response, responseType);
            } catch (IOException e) {
                lastException = e;
                logger.warn("Request failed (attempt {}): {}", attempts + 1, e.getMessage());
            } catch (LiuHaoException e) {
                if (isRetryable(e) && attempts < config.getMaxRetries()) {
                    lastException = e;
                    logger.warn("Request failed with retryable error (attempt {}): {}",
                            attempts + 1, e.getMessage());
                } else {
                    throw e;
                }
            }

            attempts++;
            if (attempts <= config.getMaxRetries()) {
                try {
                    Thread.sleep(delayMs);
                } catch (InterruptedException ie) {
                    Thread.currentThread().interrupt();
                    throw new LiuHaoException("Request interrupted", ie);
                }
                delayMs = (long) (delayMs * config.getRetryBackoffMultiplier());
            }
        }

        if (lastException instanceof java.net.SocketTimeoutException
                || lastException instanceof java.net.SocketException) {
            throw new TimeoutException("Request timed out after " + (config.getMaxRetries() + 1) + " attempts", lastException);
        }
        throw new LiuHaoException("Request failed after " + (config.getMaxRetries() + 1) + " attempts", lastException);
    }

    private boolean isRetryable(LiuHaoException e) {
        if (e instanceof RateLimitException) {
            return true;
        }
        if (e instanceof APIException) {
            int status = e.getStatusCode();
            return status == 502 || status == 503 || status == 504;
        }
        return false;
    }

    private <T> T handleResponse(Response response, Class<T> responseType) throws IOException {
        int statusCode = response.code();
        String requestId = response.header("X-Request-ID");
        ResponseBody responseBody = response.body();
        String body = responseBody != null ? responseBody.string() : "";

        if (statusCode >= 200 && statusCode < 300) {
            if (responseType == Void.class || body.isEmpty()) {
                return null;
            }
            return gson.fromJson(body, responseType);
        }

        String errorMessage = extractErrorMessage(body);

        switch (statusCode) {
            case 400:
                List<String> fieldErrors = extractFieldErrors(body);
                throw new ValidationException(errorMessage, requestId, fieldErrors);
            case 401:
                throw new AuthenticationException(errorMessage, requestId);
            case 404:
                throw new NotFoundException(errorMessage, requestId);
            case 429:
                int retryAfter = 0;
                String retryHeader = response.header("Retry-After");
                if (retryHeader != null) {
                    try {
                        retryAfter = Integer.parseInt(retryHeader);
                    } catch (NumberFormatException ignored) {
                    }
                }
                throw new RateLimitException(errorMessage, requestId, retryAfter);
            default:
                if (statusCode >= 500) {
                    throw new APIException(errorMessage, statusCode, requestId);
                }
                throw new LiuHaoException(errorMessage, statusCode, requestId);
        }
    }

    private String extractErrorMessage(String body) {
        try {
            JsonObject json = JsonParser.parseString(body).getAsJsonObject();
            if (json.has("message")) {
                return json.get("message").getAsString();
            }
            if (json.has("error")) {
                return json.get("error").getAsString();
            }
        } catch (Exception ignored) {
        }
        return body.isEmpty() ? "Unknown error" : body;
    }

    private List<String> extractFieldErrors(String body) {
        try {
            JsonObject json = JsonParser.parseString(body).getAsJsonObject();
            if (json.has("details")) {
                List<String> errors = new ArrayList<>();
                json.getAsJsonArray("details").forEach(e -> errors.add(e.getAsString()));
                return errors;
            }
            if (json.has("errors")) {
                List<String> errors = new ArrayList<>();
                json.getAsJsonArray("errors").forEach(e -> errors.add(e.getAsString()));
                return errors;
            }
        } catch (Exception ignored) {
        }
        return Collections.emptyList();
    }

    private String buildUrl(String path) {
        String base = config.getBaseUrl();
        if (path.startsWith("http")) {
            return path;
        }
        if (!base.endsWith("/") && !path.startsWith("/")) {
            return base + "/" + path;
        }
        if (base.endsWith("/") && path.startsWith("/")) {
            return base + path.substring(1);
        }
        return base + path;
    }

    /**
     * Builder for {@link LiuHaoClient}.
     */
    public static class Builder {
        private final LiuHaoConfig.Builder configBuilder = LiuHaoConfig.builder();

        private Builder() {
        }

        public Builder apiKey(String apiKey) {
            configBuilder.apiKey(apiKey);
            return this;
        }

        public Builder baseUrl(String baseUrl) {
            configBuilder.baseUrl(baseUrl);
            return this;
        }

        public Builder timeout(long timeout, java.util.concurrent.TimeUnit unit) {
            configBuilder.timeout(timeout, unit);
            return this;
        }

        public Builder maxRetries(int maxRetries) {
            configBuilder.maxRetries(maxRetries);
            return this;
        }

        public Builder config(LiuHaoConfig config) {
            // Transfer config values to the builder
            configBuilder.baseUrl(config.getBaseUrl());
            configBuilder.apiKey(config.getApiKey());
            configBuilder.timeout(config.getTimeout(), config.getTimeoutUnit());
            configBuilder.maxRetries(config.getMaxRetries());
            configBuilder.initialRetryDelayMs(config.getInitialRetryDelayMs());
            configBuilder.retryBackoffMultiplier(config.getRetryBackoffMultiplier());
            configBuilder.userAgent(config.getUserAgent());
            return this;
        }

        public LiuHaoClient build() {
            return new LiuHaoClient(this);
        }
    }
}
