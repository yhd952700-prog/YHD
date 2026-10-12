package com.liuhao.sdk.resources;

import com.google.gson.JsonObject;
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.Agent;
import com.liuhao.sdk.models.AgentStatus;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Resource class for managing AI Agents.
 */
public class AgentsResource {

    private final LiuHaoClient client;
    private static final String BASE_PATH = "agents";

    public AgentsResource(LiuHaoClient client) {
        this.client = client;
    }

    /**
     * Lists all agents with pagination.
     */
    public List<Agent> list() {
        Agent[] agents = client.get(BASE_PATH, Agent[].class);
        return Arrays.asList(agents);
    }

    /**
     * Lists agents with filters.
     */
    public List<Agent> list(int limit, String cursor) {
        StringBuilder path = new StringBuilder(BASE_PATH + "?limit=" + limit);
        if (cursor != null) {
            path.append("&cursor=").append(cursor);
        }
        Agent[] agents = client.get(path.toString(), Agent[].class);
        return Arrays.asList(agents);
    }

    /**
     * Gets an agent by ID.
     */
    public Agent get(String agentId) {
        return client.get(BASE_PATH + "/" + agentId, Agent.class);
    }

    /**
     * Creates a new agent.
     */
    public Agent create(Agent agent) {
        return client.post(BASE_PATH, agent, Agent.class);
    }

    /**
     * Creates an agent from a name and model.
     */
    public Agent create(String name, String modelName) {
        Map<String, Object> body = new HashMap<>();
        body.put("name", name);
        body.put("model_name", modelName);
        return client.post(BASE_PATH, body, Agent.class);
    }

    /**
     * Updates an existing agent.
     */
    public Agent update(String agentId, Agent agent) {
        return client.put(BASE_PATH + "/" + agentId, agent, Agent.class);
    }

    /**
     * Partially updates an agent.
     */
    public Agent patch(String agentId, Map<String, Object> updates) {
        return client.patch(BASE_PATH + "/" + agentId, updates, Agent.class);
    }

    /**
     * Deletes an agent.
     */
    public void delete(String agentId) {
        client.delete(BASE_PATH + "/" + agentId, Void.class);
    }

    /**
     * Executes an agent with the given input.
     */
    public String execute(String agentId, String input) {
        Map<String, Object> body = new HashMap<>();
        body.put("input", input);
        JsonObject result = client.post(BASE_PATH + "/" + agentId + "/execute", body, JsonObject.class);
        if (result != null && result.has("output")) {
            return result.get("output").getAsString();
        }
        return null;
    }

    /**
     * Executes an agent with input and additional parameters.
     */
    public Map<String, Object> executeWithParams(String agentId, Map<String, Object> params) {
        JsonObject result = client.post(BASE_PATH + "/" + agentId + "/execute", params, JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Gets the current status of an agent.
     */
    public AgentStatus getStatus(String agentId) {
        Agent agent = client.get(BASE_PATH + "/" + agentId + "/status", Agent.class);
        return agent.getStatus();
    }

    /**
     * Cancels a running agent execution.
     */
    public void cancel(String agentId) {
        client.post(BASE_PATH + "/" + agentId + "/cancel", null, Void.class);
    }

    /**
     * Gets metrics for an agent.
     */
    public Map<String, Object> metrics(String agentId) {
        JsonObject result = client.get(BASE_PATH + "/" + agentId + "/metrics", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Pauses an agent.
     */
    public Agent pause(String agentId) {
        return client.post(BASE_PATH + "/" + agentId + "/pause", null, Agent.class);
    }

    /**
     * Resumes a paused agent.
     */
    public Agent resume(String agentId) {
        return client.post(BASE_PATH + "/" + agentId + "/resume", null, Agent.class);
    }

    /**
     * Lists all tools available to an agent.
     */
    public List<String> listTools(String agentId) {
        String[] tools = client.get(BASE_PATH + "/" + agentId + "/tools", String[].class);
        return Arrays.asList(tools);
    }
}
