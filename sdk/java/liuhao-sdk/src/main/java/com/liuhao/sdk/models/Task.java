package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;
import java.time.Instant;
import java.util.Map;

/**
 * Represents an asynchronous task in the LiuHao platform.
 */
public class Task {

    public enum Status {
        @SerializedName("pending")
        PENDING,
        @SerializedName("queued")
        QUEUED,
        @SerializedName("running")
        RUNNING,
        @SerializedName("completed")
        COMPLETED,
        @SerializedName("failed")
        FAILED,
        @SerializedName("cancelled")
        CANCELLED,
        @SerializedName("timeout")
        TIMEOUT
    }

    private String id;

    @SerializedName("task_type")
    private String taskType;

    private Status status;
    private int progress;
    private String message;

    @SerializedName("agent_id")
    private String agentId;

    @SerializedName("result_data")
    private Map<String, Object> resultData;

    private Map<String, Object> metadata;

    @SerializedName("created_at")
    private Instant createdAt;

    @SerializedName("started_at")
    private Instant startedAt;

    @SerializedName("completed_at")
    private Instant completedAt;

    public Task() {
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getTaskType() { return taskType; }
    public void setTaskType(String taskType) { this.taskType = taskType; }

    public Status getStatus() { return status; }
    public void setStatus(Status status) { this.status = status; }

    public int getProgress() { return progress; }
    public void setProgress(int progress) { this.progress = progress; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public String getAgentId() { return agentId; }
    public void setAgentId(String agentId) { this.agentId = agentId; }

    public Map<String, Object> getResultData() { return resultData; }
    public void setResultData(Map<String, Object> resultData) { this.resultData = resultData; }

    public Map<String, Object> getMetadata() { return metadata; }
    public void setMetadata(Map<String, Object> metadata) { this.metadata = metadata; }

    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }

    public Instant getStartedAt() { return startedAt; }
    public void setStartedAt(Instant startedAt) { this.startedAt = startedAt; }

    public Instant getCompletedAt() { return completedAt; }
    public void setCompletedAt(Instant completedAt) { this.completedAt = completedAt; }

    public boolean isDone() {
        return status == Status.COMPLETED || status == Status.FAILED
                || status == Status.CANCELLED || status == Status.TIMEOUT;
    }

    public boolean isSuccess() {
        return status == Status.COMPLETED;
    }
}
