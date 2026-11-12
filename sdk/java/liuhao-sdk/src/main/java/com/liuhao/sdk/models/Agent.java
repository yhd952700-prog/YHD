package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * Represents an AI Agent in the LiuHao platform.
 */
public class Agent {

    private String id;
    private String name;
    private String description;
    private AgentStatus status;

    @SerializedName("model_name")
    private String modelName;

    @SerializedName("system_prompt")
    private String systemPrompt;

    private List<String> tools;

    @SerializedName("knowledge_bases")
    private List<String> knowledgeBases;

    private Map<String, Object> metadata;

    @SerializedName("created_at")
    private Instant createdAt;

    @SerializedName("updated_at")
    private Instant updatedAt;

    public Agent() {
    }

    private Agent(Builder builder) {
        this.id = builder.id;
        this.name = builder.name;
        this.description = builder.description;
        this.status = builder.status;
        this.modelName = builder.modelName;
        this.systemPrompt = builder.systemPrompt;
        this.tools = builder.tools;
        this.knowledgeBases = builder.knowledgeBases;
        this.metadata = builder.metadata;
        this.createdAt = builder.createdAt;
        this.updatedAt = builder.updatedAt;
    }

    public static Builder builder() {
        return new Builder();
    }

    public String getId() { return id; }
    public void setId(String id) { this.id = id; }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    public String getDescription() { return description; }
    public void setDescription(String description) { this.description = description; }

    public AgentStatus getStatus() { return status; }
    public void setStatus(AgentStatus status) { this.status = status; }

    public String getModelName() { return modelName; }
    public void setModelName(String modelName) { this.modelName = modelName; }

    public String getSystemPrompt() { return systemPrompt; }
    public void setSystemPrompt(String systemPrompt) { this.systemPrompt = systemPrompt; }

    public List<String> getTools() { return tools; }
    public void setTools(List<String> tools) { this.tools = tools; }

    public List<String> getKnowledgeBases() { return knowledgeBases; }
    public void setKnowledgeBases(List<String> knowledgeBases) { this.knowledgeBases = knowledgeBases; }

    public Map<String, Object> getMetadata() { return metadata; }
    public void setMetadata(Map<String, Object> metadata) { this.metadata = metadata; }

    public Instant getCreatedAt() { return createdAt; }
    public void setCreatedAt(Instant createdAt) { this.createdAt = createdAt; }

    public Instant getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(Instant updatedAt) { this.updatedAt = updatedAt; }

    public static class Builder {
        private String id;
        private String name;
        private String description;
        private AgentStatus status;
        private String modelName;
        private String systemPrompt;
        private List<String> tools;
        private List<String> knowledgeBases;
        private Map<String, Object> metadata;
        private Instant createdAt;
        private Instant updatedAt;

        public Builder id(String id) { this.id = id; return this; }
        public Builder name(String name) { this.name = name; return this; }
        public Builder description(String description) { this.description = description; return this; }
        public Builder status(AgentStatus status) { this.status = status; return this; }
        public Builder modelName(String modelName) { this.modelName = modelName; return this; }
        public Builder systemPrompt(String systemPrompt) { this.systemPrompt = systemPrompt; return this; }
        public Builder tools(List<String> tools) { this.tools = tools; return this; }
        public Builder knowledgeBases(List<String> knowledgeBases) { this.knowledgeBases = knowledgeBases; return this; }
        public Builder metadata(Map<String, Object> metadata) { this.metadata = metadata; return this; }
        public Builder createdAt(Instant createdAt) { this.createdAt = createdAt; return this; }
        public Builder updatedAt(Instant updatedAt) { this.updatedAt = updatedAt; return this; }

        public Agent build() {
            return new Agent(this);
        }
    }
}
