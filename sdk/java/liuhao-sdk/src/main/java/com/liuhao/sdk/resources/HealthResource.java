package com.liuhao.sdk.resources;

import com.google.gson.JsonObject;
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.HealthStatus;

import java.util.Map;

/**
 * Resource class for health checks.
 */
public class HealthResource {

    private final LiuHaoClient client;
    private static final String BASE_PATH = "health";

    public HealthResource(LiuHaoClient client) {
        this.client = client;
    }

    /**
     * Performs a basic health check.
     */
    public HealthStatus check() {
        return client.get(BASE_PATH, HealthStatus.class);
    }

    /**
     * Checks if the service is ready to serve requests.
     */
    public boolean ready() {
        try {
            HealthStatus status = client.get(BASE_PATH + "/ready", HealthStatus.class);
            return status.isHealthy();
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Checks if the service is alive (liveness probe).
     */
    public boolean liveness() {
        try {
            HealthStatus status = client.get(BASE_PATH + "/live", HealthStatus.class);
            return status.isHealthy();
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * Gets detailed health information including all services.
     */
    public HealthStatus detailed() {
        return client.get(BASE_PATH + "/detailed", HealthStatus.class);
    }

    /**
     * Gets version information.
     */
    public Map<String, Object> version() {
        JsonObject result = client.get(BASE_PATH + "/version", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Gets system metrics (CPU, memory, etc.).
     */
    public Map<String, Object> metrics() {
        JsonObject result = client.get(BASE_PATH + "/metrics", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }
}
