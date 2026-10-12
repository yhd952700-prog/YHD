package com.liuhao.sdk.resources;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.KnowledgeBase;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Resource class for managing knowledge bases.
 */
public class KnowledgeResource {

    private final LiuHaoClient client;
    private static final String BASE_PATH = "knowledge";

    public KnowledgeResource(LiuHaoClient client) {
        this.client = client;
    }

    /**
     * Lists all knowledge bases.
     */
    public List<KnowledgeBase> listBases() {
        KnowledgeBase[] bases = client.get(BASE_PATH + "/bases", KnowledgeBase[].class);
        return Arrays.asList(bases);
    }

    /**
     * Lists knowledge bases with pagination.
     */
    public List<KnowledgeBase> listBases(int limit, String cursor) {
        StringBuilder path = new StringBuilder(BASE_PATH + "/bases?limit=" + limit);
        if (cursor != null) path.append("&cursor=").append(cursor);
        KnowledgeBase[] bases = client.get(path.toString(), KnowledgeBase[].class);
        return Arrays.asList(bases);
    }

    /**
     * Creates a new knowledge base.
     */
    public KnowledgeBase createBase(String name, String description) {
        Map<String, Object> body = new HashMap<>();
        body.put("name", name);
        body.put("description", description);
        return client.post(BASE_PATH + "/bases", body, KnowledgeBase.class);
    }

    /**
     * Creates a knowledge base with full options.
     */
    public KnowledgeBase createBase(String name, String description,
                                    String embeddingModel, List<String> tags) {
        Map<String, Object> body = new HashMap<>();
        body.put("name", name);
        body.put("description", description);
        if (embeddingModel != null) body.put("embedding_model", embeddingModel);
        if (tags != null) body.put("tags", tags);
        return client.post(BASE_PATH + "/bases", body, KnowledgeBase.class);
    }

    /**
     * Gets a knowledge base by ID.
     */
    public KnowledgeBase getBase(String baseId) {
        return client.get(BASE_PATH + "/bases/" + baseId, KnowledgeBase.class);
    }

    /**
     * Updates a knowledge base.
     */
    public KnowledgeBase updateBase(String baseId, Map<String, Object> updates) {
        return client.patch(BASE_PATH + "/bases/" + baseId, updates, KnowledgeBase.class);
    }

    /**
     * Deletes a knowledge base.
     */
    public void deleteBase(String baseId) {
        client.delete(BASE_PATH + "/bases/" + baseId, Void.class);
    }

    /**
     * Uploads a document to a knowledge base.
     */
    public Map<String, Object> uploadDocument(String baseId, String title, String content) {
        Map<String, Object> body = new HashMap<>();
        body.put("title", title);
        body.put("content", content);
        JsonObject result = client.post(BASE_PATH + "/bases/" + baseId + "/documents", body, JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Uploads a document with metadata.
     */
    public Map<String, Object> uploadDocument(String baseId, String title, String content,
                                              String source, Map<String, Object> metadata) {
        Map<String, Object> body = new HashMap<>();
        body.put("title", title);
        body.put("content", content);
        if (source != null) body.put("source", source);
        if (metadata != null) body.put("metadata", metadata);
        JsonObject result = client.post(BASE_PATH + "/bases/" + baseId + "/documents", body, JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Searches within a knowledge base.
     */
    public List<Map<String, Object>> search(String baseId, String query) {
        Map<String, Object> body = new HashMap<>();
        body.put("query", query);
        JsonArray result = client.post(BASE_PATH + "/bases/" + baseId + "/search", body, JsonArray.class);
        return Arrays.asList(client.getGson().fromJson(result, Map[].class));
    }

    /**
     * Searches with additional options.
     */
    public List<Map<String, Object>> search(String baseId, String query, int topK, double minScore) {
        Map<String, Object> body = new HashMap<>();
        body.put("query", query);
        body.put("top_k", topK);
        body.put("min_score", minScore);
        JsonArray result = client.post(BASE_PATH + "/bases/" + baseId + "/search", body, JsonArray.class);
        return Arrays.asList(client.getGson().fromJson(result, Map[].class));
    }

    /**
     * Lists documents in a knowledge base.
     */
    public List<Map<String, Object>> listDocuments(String baseId) {
        JsonObject result = client.get(BASE_PATH + "/bases/" + baseId + "/documents", JsonObject.class);
        if (result != null && result.has("documents")) {
            JsonArray docs = result.getAsJsonArray("documents");
            return Arrays.asList(client.getGson().fromJson(docs, Map[].class));
        }
        return java.util.Collections.emptyList();
    }

    /**
     * Lists documents with pagination.
     */
    public List<Map<String, Object>> listDocuments(String baseId, int limit, String cursor) {
        StringBuilder path = new StringBuilder(BASE_PATH + "/bases/" + baseId + "/documents?limit=" + limit);
        if (cursor != null) path.append("&cursor=").append(cursor);
        JsonObject result = client.get(path.toString(), JsonObject.class);
        if (result != null && result.has("documents")) {
            JsonArray docs = result.getAsJsonArray("documents");
            return Arrays.asList(client.getGson().fromJson(docs, Map[].class));
        }
        return java.util.Collections.emptyList();
    }

    /**
     * Deletes a document from a knowledge base.
     */
    public void deleteDocument(String baseId, String documentId) {
        client.delete(BASE_PATH + "/bases/" + baseId + "/documents/" + documentId, Void.class);
    }

    /**
     * Gets statistics for a knowledge base.
     */
    public Map<String, Object> baseStats(String baseId) {
        JsonObject result = client.get(BASE_PATH + "/bases/" + baseId + "/stats", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }
}
