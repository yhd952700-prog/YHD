package com.liuhao.sdk.resources;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.MemoryItem;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Resource class for managing the memory system.
 */
public class MemoryResource {

    private final LiuHaoClient client;
    private static final String BASE_PATH = "memory";

    public MemoryResource(LiuHaoClient client) {
        this.client = client;
    }

    /**
     * Stores a new memory item.
     */
    public MemoryItem store(MemoryItem item) {
        return client.post(BASE_PATH + "/items", item, MemoryItem.class);
    }

    /**
     * Stores a memory with content and type.
     */
    public MemoryItem store(String content, String memoryType) {
        Map<String, Object> body = new HashMap<>();
        body.put("content", content);
        body.put("memory_type", memoryType);
        return client.post(BASE_PATH + "/items", body, MemoryItem.class);
    }

    /**
     * Stores a memory with full parameters.
     */
    public MemoryItem store(String content, String memoryType, String category,
                            double importance, Map<String, Object> metadata) {
        Map<String, Object> body = new HashMap<>();
        body.put("content", content);
        body.put("memory_type", memoryType);
        if (category != null) body.put("category", category);
        body.put("importance", importance);
        if (metadata != null) body.put("metadata", metadata);
        return client.post(BASE_PATH + "/items", body, MemoryItem.class);
    }

    /**
     * Retrieves a memory item by ID.
     */
    public MemoryItem retrieve(String memoryId) {
        return client.get(BASE_PATH + "/items/" + memoryId, MemoryItem.class);
    }

    /**
     * Updates a memory item.
     */
    public MemoryItem update(String memoryId, MemoryItem item) {
        return client.put(BASE_PATH + "/items/" + memoryId, item, MemoryItem.class);
    }

    /**
     * Partially updates a memory item.
     */
    public MemoryItem patch(String memoryId, Map<String, Object> updates) {
        return client.patch(BASE_PATH + "/items/" + memoryId, updates, MemoryItem.class);
    }

    /**
     * Deletes a memory item.
     */
    public void delete(String memoryId) {
        client.delete(BASE_PATH + "/items/" + memoryId, Void.class);
    }

    /**
     * Searches memory by query.
     */
    public List<MemoryItem> search(String query) {
        Map<String, Object> body = new HashMap<>();
        body.put("query", query);
        MemoryItem[] results = client.post(BASE_PATH + "/search", body, MemoryItem[].class);
        return Arrays.asList(results);
    }

    /**
     * Searches memory with additional filters.
     */
    public List<MemoryItem> search(String query, String memoryType, String category, int limit) {
        Map<String, Object> body = new HashMap<>();
        body.put("query", query);
        if (memoryType != null) body.put("memory_type", memoryType);
        if (category != null) body.put("category", category);
        body.put("limit", limit);
        MemoryItem[] results = client.post(BASE_PATH + "/search", body, MemoryItem[].class);
        return Arrays.asList(results);
    }

    /**
     * Lists memory items with pagination.
     */
    public List<MemoryItem> list() {
        MemoryItem[] items = client.get(BASE_PATH + "/items", MemoryItem[].class);
        return Arrays.asList(items);
    }

    /**
     * Lists memory items with filters.
     */
    public List<MemoryItem> list(String memoryType, String category, int limit, String cursor) {
        StringBuilder path = new StringBuilder(BASE_PATH + "/items?");
        if (memoryType != null) path.append("memory_type=").append(memoryType).append("&");
        if (category != null) path.append("category=").append(category).append("&");
        path.append("limit=").append(limit);
        if (cursor != null) path.append("&cursor=").append(cursor);
        MemoryItem[] items = client.get(path.toString(), MemoryItem[].class);
        return Arrays.asList(items);
    }

    /**
     * Clears all memory (for current user/agent).
     */
    public void clear() {
        client.delete(BASE_PATH + "/items", Void.class);
    }

    /**
     * Clears memory by type.
     */
    public void clearByType(String memoryType) {
        Map<String, Object> body = new HashMap<>();
        body.put("memory_type", memoryType);
        client.post(BASE_PATH + "/clear", body, Void.class);
    }

    /**
     * Gets all memory categories.
     */
    public List<String> categories() {
        JsonObject result = client.get(BASE_PATH + "/categories", JsonObject.class);
        if (result != null && result.has("categories")) {
            JsonArray arr = result.getAsJsonArray("categories");
            String[] cats = client.getGson().fromJson(arr, String[].class);
            return Arrays.asList(cats);
        }
        return java.util.Collections.emptyList();
    }

    /**
     * Gets memory statistics.
     */
    public Map<String, Object> stats() {
        JsonObject result = client.get(BASE_PATH + "/stats", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }
}
