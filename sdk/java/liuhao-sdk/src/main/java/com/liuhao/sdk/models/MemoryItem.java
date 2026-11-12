package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;
import java.time.Instant;
import java.util.Map;

/**
 * Represents a memory item stored in the LiuHao memory system.
 */
public class MemoryItem {

    private String id;
    private String content;

    @SerializedName("memory_type")
    private String memoryType;

    private String category;
    private double importance;
    private Map<String, Object> metadata;

    @SerializedName("user_id")
    private String userId;

    @SerializedName("agent_id")
    private String agentId;

    @SerializedName("created_at")
    private Instant createdAt;

    @SerializedName("updated_at")
    private Instant updatedAt;

    @SerializedName("last_accessed_at")
    private Instant lastAccessedAt;

    private int accessCount;

    public MemoryItem() {
    }

    private MemoryItem(Builder builder) {
        this.id = builder.id;
        this.content = builder.content;
        this.memoryType = builder.memoryType;
        this.category = builder.category;
        this.importance = builder.importance;
        this.metadata = builder.metadata;
        this.userId = builder.userId;
        this.agentId = builder.agentId;
        this.createdAt = builder.createdAt;
        this.updatedAt = builder.updatedAt;
        this.lastAccessedAt = builder.lastAccessedAt;
        this.accessCount = builder.accessCount;
    }

    public static Builder builder() {
        return new Builder();
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }

    public String getMemoryType() { return memoryType; }
    public void setMemoryType(String memoryType) { this.memoryType = memoryType; }

    public String getCategory() { return category; }
    public void setCategory(String category) { this.category = category; }

    public double getImportance() { return importance; }
    public void setImportance(double importance) { this.importance = importance; }

    public Map<String, Object> getMetadata() { return metadata; }
    public void setMetadata(Map<String, Object> metadata) { this.metadata = metadata; }

    public String getUserId() { return userId; }
    public void setUserId(String userId) { this.userId = userId; }

    public String getAgentId() { return agentId; }
    public void setAgentId(String agentId) { this.agentId = agentId; }

    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }

    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }

    public Instant getLastAccessedAt() { return lastAccessedAt; }
    public void setLastAccessedAt(Instant lastAccessedAt) { this.lastAccessedAt = lastAccessedAt; }

    public int getAccessCount() { return accessCount; }
    public void setAccessCount(int accessCount) { this.accessCount = accessCount; }

    public static class Builder {
        private String id;
        private String content;
        private String memoryType;
        private String category;
        private double importance;
        private Map<String, Object> metadata;
        private String userId;
        private String agentId;
        private Instant createdAt;
        private Instant updatedAt;
        private Instant lastAccessedAt;
        private int accessCount;

        public Builder id(String id) { this.id = id; return this; }
        public Builder content(String content) { this.content = content; return this; }
        public Builder memoryType(String memoryType) { this.memoryType = memoryType; return this; }
        public Builder category(String category) { this.category = category; return this; }
        public Builder importance(double importance) { this.importance = importance; return this; }
        public Builder metadata(Map<String, Object> metadata) { this.metadata = metadata; return this; }
        public Builder userId(String userId) { this.userId = userId; return this; }
        public Builder agentId(String agentId) { this.agentId = agentId; return this; }
        public Builder createdAt(Instant createdAt) { this.createdAt = createdAt; return this; }
        public Builder updatedAt(Instant updatedAt) { this.updatedAt = updatedAt; return this; }
        public Builder lastAccessedAt(Instant lastAccessedAt) { this.lastAccessedAt = lastAccessedAt; return this; }
        public Builder accessCount(int accessCount) { this.accessCount = accessCount; return this; }

        public MemoryItem build() {
            return new MemoryItem(this);
        }
    }
}
