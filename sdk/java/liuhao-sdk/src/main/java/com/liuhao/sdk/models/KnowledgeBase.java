package com.liuhao.sdk.models;

import com.google.gson.annotations.SerializedName;
import java.time.Instant;
import java.util.List;
import java.util.Map;

/**
 * Represents a knowledge base in the LiuHao platform.
 */
public class KnowledgeBase {

    private String id;
    private String name;
    private String description;

    @SerializedName("vector_dimension")
    private int vectorDimension;

    @SerializedName("embedding_model")
    private String embeddingModel;

    @SerializedName("document_count")
    private int documentCount;

    @SerializedName("chunk_count")
    private int chunkCount;

    private List<String> tags;
    private Map<String, Object> metadata;

    @SerializedName("created_at")
    private Instant createdAt;

    @SerializedName("updated_at")
    private Instant updatedAt;

    public KnowledgeBase() {
    }

    private KnowledgeBase(Builder builder) {
        this.id = builder.id;
        this.name = builder.name;
        this.description = builder.description;
        this.vectorDimension = builder.vectorDimension;
        this.embeddingModel = builder.embeddingModel;
        this.documentCount = builder.documentCount;
        this.chunkCount = builder.chunkCount;
        this.tags = builder.tags;
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

    public int getVectorDimension() { return vectorDimension; }
    public void setVectorDimension(int vectorDimension) { this.vectorDimension = vectorDimension; }

    public String getEmbeddingModel() { return embeddingModel; }
    public void setEmbeddingModel(String embeddingModel) { this.embeddingModel = embeddingModel; }

    public int getDocumentCount() { return documentCount; }
    public void setDocumentCount(int documentCount) { this.documentCount = documentCount; }

    public int getChunkCount() { return chunkCount; }
    public void setChunkCount(int chunkCount) { this.chunkCount = chunkCount; }

    public List<String> getTags() { return tags; }
    public void setTags(List<String> tags) { this.tags = tags; }

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
        private int vectorDimension;
        private String embeddingModel;
        private int documentCount;
        private int chunkCount;
        private List<String> tags;
        private Map<String, Object> metadata;
        private Instant createdAt;
        private Instant updatedAt;

        public Builder id(String id) { this.id = id; return this; }
        public Builder name(String name) { this.name = name; return this; }
        public Builder description(String description) { this.description = description; return this; }
        public Builder vectorDimension(int vectorDimension) { this.vectorDimension = vectorDimension; return this; }
        public Builder embeddingModel(String embeddingModel) { this.embeddingModel = embeddingModel; return this; }
        public Builder documentCount(int documentCount) { this.documentCount = documentCount; return this; }
        public Builder chunkCount(int chunkCount) { this.chunkCount = chunkCount; return this; }
        public Builder tags(List<String> tags) { this.tags = tags; return this; }
        public Builder metadata(Map<String, Object> metadata) { this.metadata = metadata; return this; }
        public Builder createdAt(Instant createdAt) { this.createdAt = createdAt; return this; }
        public Builder updatedAt(Instant updatedAt) { this.updatedAt = updatedAt; return this; }

        public KnowledgeBase build() {
            return new KnowledgeBase(this);
        }
    }
}
