package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;
import java.time.Instant;
import java.util.Map;

/**
 * Represents the health status of the LiuHao platform.
 */
public class HealthStatus {

    public enum OverallStatus {
        @SerializedName("healthy")
        HEALTHY,
        @SerializedName("degraded")
        DEGRADED,
        @SerializedName("unhealthy")
        UNHEALTHY,
        @SerializedName("maintenance")
        MAINTENANCE
    }

    private OverallStatus status;
    private String version;

    @SerializedName("build_time")
    private String buildTime;

    private String uptime;

    @SerializedName("service_count")
    private int serviceCount;

    @SerializedName("active_connections")
    private int activeConnections;

    private Map<String, ServiceHealth> services;

    @SerializedName("checked_at")
    private Instant checkedAt;

    public HealthStatus() {
    }

    public OverallStatus getStatus() { return status; }
    public void setStatus(OverallStatus status) { this.status = status; }

    public String getVersion() { return version; }
    public void setVersion(String version) { this.version = version; }

    public String getBuildTime() { return buildTime; }
    public void setBuildTime(String buildTime) { this.buildTime = buildTime; }

    public String getUptime() { return uptime; }
    public void setUptime(String uptime) { this.uptime = uptime; }

    public int getServiceCount() { return serviceCount; }
    public void setServiceCount(int serviceCount) { this.serviceCount = serviceCount; }

    public int getActiveConnections() { return activeConnections; }
    public void setActiveConnections(int activeConnections) { this.activeConnections = activeConnections; }

    public Map<String, ServiceHealth> getServices() { return services; }
    public void setServices(Map<String, ServiceHealth> services) { this.services = services; }

    public Instant getCheckedAt() { return checkedAt; }
    public void setCheckedAt(Instant checkedAt) { this.checkedAt = checkedAt; }

    public boolean isHealthy() {
        return status == OverallStatus.HEALTHY;
    }

    /**
     * Health status of an individual service.
     */
    public static class ServiceHealth {
        private OverallStatus status;
        private String message;
        private int latencyMs;

        @SerializedName("last_check")
        private Instant lastCheck;

        public ServiceHealth() {
        }

        public OverallStatus getStatus() { return status; }
        public void setStatus(OverallStatus status) { this.status = status; }

        public String getMessage() { return message; }
        public void setMessage(String message) { this.message = message; }

        public int getLatencyMs() { return latencyMs; }
        public void setLatencyMs(int latencyMs) { this.latencyMs = latencyMs; }

        public Instant getLastCheck() { return lastCheck; }
        public void setLastCheck(Instant lastCheck) { this.lastCheck = lastCheck; }

        public boolean isHealthy() {
            return status == OverallStatus.HEALTHY;
        }
    }
}
