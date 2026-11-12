package com.liuhao.sdk.resources;

import com.google.gson.JsonObject;
import com.liuhao.sdk.LiuHaoClient;
import com.liuhao.sdk.models.Task;

import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Resource class for managing asynchronous tasks.
 */
public class TasksResource {

    private final LiuHaoClient client;
    private static final String BASE_PATH = "tasks";

    public TasksResource(LiuHaoClient client) {
        this.client = client;
    }

    /**
     * Creates a new task.
     */
    public Task create(String taskType, Map<String, Object> params) {
        Map<String, Object> body = new HashMap<>();
        body.put("task_type", taskType);
        if (params != null) body.put("params", params);
        return client.post(BASE_PATH, body, Task.class);
    }

    /**
     * Creates a task associated with an agent.
     */
    public Task create(String taskType, String agentId, Map<String, Object> params) {
        Map<String, Object> body = new HashMap<>();
        body.put("task_type", taskType);
        body.put("agent_id", agentId);
        if (params != null) body.put("params", params);
        return client.post(BASE_PATH, body, Task.class);
    }

    /**
     * Gets a task by ID.
     */
    public Task get(String taskId) {
        return client.get(BASE_PATH + "/" + taskId, Task.class);
    }

    /**
     * Lists tasks with default parameters.
     */
    public List<Task> list() {
        Task[] tasks = client.get(BASE_PATH, Task[].class);
        return Arrays.asList(tasks);
    }

    /**
     * Lists tasks with filters.
     */
    public List<Task> list(Task.Status status, String taskType, int limit, String cursor) {
        StringBuilder path = new StringBuilder(BASE_PATH + "?");
        if (status != null) path.append("status=").append(status.name().toLowerCase()).append("&");
        if (taskType != null) path.append("task_type=").append(taskType).append("&");
        path.append("limit=").append(limit);
        if (cursor != null) path.append("&cursor=").append(cursor);
        Task[] tasks = client.get(path.toString(), Task[].class);
        return Arrays.asList(tasks);
    }

    /**
     * Cancels a task.
     */
    public Task cancel(String taskId) {
        return client.post(BASE_PATH + "/" + taskId + "/cancel", null, Task.class);
    }

    /**
     * Gets the status of a task.
     */
    public Task.Status status(String taskId) {
        Task task = client.get(BASE_PATH + "/" + taskId + "/status", Task.class);
        return task.getStatus();
    }

    /**
     * Gets the results of a completed task.
     */
    public Map<String, Object> results(String taskId) {
        JsonObject result = client.get(BASE_PATH + "/" + taskId + "/results", JsonObject.class);
        return client.getGson().fromJson(result, Map.class);
    }

    /**
     * Waits for a task to complete (polling).
     *
     * @param taskId the task ID
     * @param pollIntervalMs interval between polls in milliseconds
     * @param timeoutMs maximum time to wait in milliseconds
     * @return the completed task
     * @throws InterruptedException if interrupted while waiting
     * @throws com.liuhao.sdk.exceptions.TimeoutException if timeout is reached
     */
    public Task waitForCompletion(String taskId, long pollIntervalMs, long timeoutMs)
            throws InterruptedException {
        long startTime = System.currentTimeMillis();
        while (true) {
            Task task = get(taskId);
            if (task.isDone()) {
                return task;
            }
            if (System.currentTimeMillis() - startTime > timeoutMs) {
                throw new com.liuhao.sdk.exceptions.TimeoutException(
                        "Task " + taskId + " did not complete within " + timeoutMs + "ms");
            }
            Thread.sleep(pollIntervalMs);
        }
    }

    /**
     * Waits for a task to complete with default polling (1s interval, 5min timeout).
     */
    public Task waitForCompletion(String taskId) throws InterruptedException {
        return waitForCompletion(taskId, 1000, 300000);
    }
}
