"""Tests for extended API endpoint modules.

Covers all 8 API endpoint extensions:
- agents_api
- memory_api
- knowledge_api
- tasks_api
- health_api
- admin_api
- cost_api
- plugins_api
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

# Add src to path
SRC_PATH = Path(__file__).resolve().parents[1] / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.api import agents_api
from src.api import memory_api
from src.api import knowledge_api
from src.api import tasks_api
from src.api import health_api
from src.api import admin_api
from src.api import cost_api
from src.api import plugins_api


def _build_test_app() -> FastAPI:
    """Build a FastAPI test app with all extended API routers."""
    app = FastAPI(title="LiuHao AI OS API Tests")
    app.include_router(agents_api.router)
    app.include_router(memory_api.router)
    app.include_router(knowledge_api.router)
    app.include_router(tasks_api.router)
    app.include_router(health_api.router)
    app.include_router(admin_api.router)
    app.include_router(cost_api.router)
    app.include_router(plugins_api.router)
    return app


@pytest.fixture
def client():
    """Test client fixture with all API routers."""
    app = _build_test_app()
    with TestClient(app) as test_client:
        yield test_client


# =====================================================================
# Agents API Tests
# =====================================================================


class TestAgentsAPI:
    """Tests for agents API endpoints."""

    def test_create_agent(self, client):
        response = client.post("/agents", json={
            "name": "Test Agent",
            "role": "researcher",
            "description": "Test description",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Agent"
        assert data["role"] == "researcher"
        assert data["status"] == "idle"
        assert "id" in data

    def test_list_agents_empty(self, client):
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "items" in data

    def test_list_agents_with_filters(self, client):
        client.post("/agents", json={"name": "A1", "role": "researcher"})
        client.post("/agents", json={"name": "A2", "role": "analyst"})
        response = client.get("/agents", params={"role": "researcher"})
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1

    def test_get_agent(self, client):
        create_resp = client.post("/agents", json={"name": "GetTest", "role": "manager"})
        agent_id = create_resp.json()["id"]
        response = client.get(f"/agents/{agent_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "GetTest"

    def test_get_agent_not_found(self, client):
        response = client.get("/agents/nonexistent-id")
        assert response.status_code == 404

    def test_update_agent(self, client):
        create_resp = client.post("/agents", json={"name": "UpdateTest", "role": "analyst"})
        agent_id = create_resp.json()["id"]
        response = client.put(f"/agents/{agent_id}", json={"name": "Updated Name"})
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    def test_delete_agent(self, client):
        create_resp = client.post("/agents", json={"name": "DeleteMe", "role": "analyst"})
        agent_id = create_resp.json()["id"]
        response = client.delete(f"/agents/{agent_id}")
        assert response.status_code == 204
        # Verify it's gone
        response = client.get(f"/agents/{agent_id}")
        assert response.status_code == 404

    def test_execute_agent(self, client):
        create_resp = client.post("/agents", json={"name": "ExecTest", "role": "executor"})
        agent_id = create_resp.json()["id"]
        response = client.post(f"/agents/{agent_id}/execute", json={"task": "run analysis"})
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "running"
        assert data["task"] == "run analysis"

    def test_agent_status(self, client):
        create_resp = client.post("/agents", json={"name": "StatusTest", "role": "analyst"})
        agent_id = create_resp.json()["id"]
        response = client.get(f"/agents/{agent_id}/status")
        assert response.status_code == 200
        assert "status" in response.json()

    def test_stop_agent(self, client):
        create_resp = client.post("/agents", json={"name": "StopTest", "role": "executor"})
        agent_id = create_resp.json()["id"]
        client.post(f"/agents/{agent_id}/execute", json={"task": "stop me"})
        response = client.post(f"/agents/{agent_id}/stop")
        assert response.status_code == 200
        assert response.json()["status"] == "stopped"

    def test_agent_metrics(self, client):
        create_resp = client.post("/agents", json={"name": "MetricsTest", "role": "analyst"})
        agent_id = create_resp.json()["id"]
        response = client.get(f"/agents/{agent_id}/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "avg_response_time_ms" in data
        assert "success_rate" in data

    def test_agent_logs(self, client):
        create_resp = client.post("/agents", json={"name": "LogsTest", "role": "analyst"})
        agent_id = create_resp.json()["id"]
        response = client.get(f"/agents/{agent_id}/logs")
        assert response.status_code == 200
        data = response.json()
        assert "entries" in data


# =====================================================================
# Memory API Tests
# =====================================================================


class TestMemoryAPI:
    """Tests for memory API endpoints."""

    def test_create_memory(self, client):
        response = client.post("/memory", json={
            "content": "Test memory entry",
            "memory_type": "episodic",
            "tags": ["test", "api"],
        })
        assert response.status_code == 201
        data = response.json()
        assert data["content"] == "Test memory entry"
        assert data["memory_type"] == "episodic"

    def test_list_memories(self, client):
        client.post("/memory", json={"content": "M1", "memory_type": "semantic"})
        response = client.get("/memory")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "items" in data

    def test_get_memory(self, client):
        create_resp = client.post("/memory", json={"content": "Get test", "memory_type": "episodic"})
        mem_id = create_resp.json()["id"]
        response = client.get(f"/memory/{mem_id}")
        assert response.status_code == 200
        assert response.json()["content"] == "Get test"

    def test_get_memory_not_found(self, client):
        response = client.get("/memory/nonexistent")
        assert response.status_code == 404

    def test_update_memory(self, client):
        create_resp = client.post("/memory", json={"content": "Original", "memory_type": "episodic"})
        mem_id = create_resp.json()["id"]
        response = client.put(f"/memory/{mem_id}", json={"importance": 0.9})
        assert response.status_code == 200
        assert response.json()["importance"] == 0.9

    def test_delete_memory(self, client):
        create_resp = client.post("/memory", json={"content": "Delete me", "memory_type": "episodic"})
        mem_id = create_resp.json()["id"]
        response = client.delete(f"/memory/{mem_id}")
        assert response.status_code == 204

    def test_search_memory(self, client):
        client.post("/memory", json={"content": "Python is great", "tags": ["python"]})
        response = client.get("/memory/search", params={"query": "Python"})
        assert response.status_code == 200
        data = response.json()
        # Contract is ``items`` (MemorySearchResponse), consistent with the list
        # endpoint and the JS SDK's PaginatedResponse.items.
        assert "items" in data
        assert "total" in data

    def test_batch_create(self, client):
        response = client.post("/memory/batch", json={
            "items": [
                {"content": "Batch 1", "memory_type": "episodic"},
                {"content": "Batch 2", "memory_type": "semantic"},
            ]
        })
        assert response.status_code == 201
        data = response.json()
        assert data["created"] == 2
        assert data["failed"] == 0

    def test_memory_stats(self, client):
        client.post("/memory", json={"content": "Stats test", "memory_type": "episodic"})
        response = client.get("/memory/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_memories" in data
        assert "by_type" in data

    def test_list_categories(self, client):
        client.post("/memory", json={"content": "Cat test", "category": "test-category"})
        response = client.get("/memory/categories")
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data


# =====================================================================
# Knowledge API Tests
# =====================================================================


class TestKnowledgeAPI:
    """Tests for knowledge base API endpoints."""

    def test_create_kb(self, client):
        response = client.post("/knowledge", json={
            "name": "Test KB",
            "description": "Test knowledge base",
            "category": "test",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test KB"
        assert data["document_count"] == 0

    def test_list_kbs(self, client):
        response = client.get("/knowledge")
        assert response.status_code == 200
        assert "total" in response.json()

    def test_get_kb(self, client):
        create_resp = client.post("/knowledge", json={"name": "GetTest KB"})
        kb_id = create_resp.json()["id"]
        response = client.get(f"/knowledge/{kb_id}")
        assert response.status_code == 200
        assert response.json()["name"] == "GetTest KB"

    def test_update_kb(self, client):
        create_resp = client.post("/knowledge", json={"name": "Update KB"})
        kb_id = create_resp.json()["id"]
        response = client.put(f"/knowledge/{kb_id}", json={"description": "Updated desc"})
        assert response.status_code == 200
        assert response.json()["description"] == "Updated desc"

    def test_delete_kb(self, client):
        create_resp = client.post("/knowledge", json={"name": "Delete KB"})
        kb_id = create_resp.json()["id"]
        response = client.delete(f"/knowledge/{kb_id}")
        assert response.status_code == 204

    def test_semantic_search(self, client):
        create_resp = client.post("/knowledge", json={"name": "Search KB"})
        kb_id = create_resp.json()["id"]
        response = client.post(f"/knowledge/{kb_id}/search", params={"query": "test"})
        assert response.status_code == 200
        data = response.json()
        assert "results" in data

    def test_list_documents(self, client):
        create_resp = client.post("/knowledge", json={"name": "Docs KB"})
        kb_id = create_resp.json()["id"]
        response = client.get(f"/knowledge/{kb_id}/documents")
        assert response.status_code == 200
        assert "items" in response.json()

    def test_create_collection(self, client):
        response = client.post("/knowledge/collections", json={
            "name": "Test Collection",
            "description": "A test collection",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Collection"

    def test_list_collections(self, client):
        response = client.get("/knowledge/collections/list")
        assert response.status_code == 200
        assert "items" in response.json()


# =====================================================================
# Tasks API Tests
# =====================================================================


class TestTasksAPI:
    """Tests for tasks API endpoints."""

    def test_create_task(self, client):
        response = client.post("/tasks", json={
            "title": "Test Task",
            "description": "Test task description",
            "priority": "high",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Task"
        assert data["priority"] == "high"
        assert data["status"] == "pending"

    def test_list_tasks(self, client):
        client.post("/tasks", json={"title": "Task 1", "priority": "normal"})
        response = client.get("/tasks")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert "items" in data

    def test_get_task(self, client):
        create_resp = client.post("/tasks", json={"title": "Get Task", "priority": "low"})
        task_id = create_resp.json()["id"]
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "Get Task"

    def test_update_task(self, client):
        create_resp = client.post("/tasks", json={"title": "Update Task", "priority": "normal"})
        task_id = create_resp.json()["id"]
        response = client.put(f"/tasks/{task_id}", json={"status": "running"})
        assert response.status_code == 200
        assert response.json()["status"] == "running"

    def test_delete_task(self, client):
        create_resp = client.post("/tasks", json={"title": "Delete Task", "priority": "low"})
        task_id = create_resp.json()["id"]
        response = client.delete(f"/tasks/{task_id}")
        assert response.status_code == 204

    def test_execute_task(self, client):
        create_resp = client.post("/tasks", json={"title": "Exec Task", "priority": "high"})
        task_id = create_resp.json()["id"]
        response = client.post(f"/tasks/{task_id}/execute", json={})
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "running"

    def test_task_result(self, client):
        create_resp = client.post("/tasks", json={"title": "Result Task"})
        task_id = create_resp.json()["id"]
        response = client.get(f"/tasks/{task_id}/result")
        assert response.status_code == 200
        assert "status" in response.json()

    def test_cancel_task(self, client):
        create_resp = client.post("/tasks", json={"title": "Cancel Task"})
        task_id = create_resp.json()["id"]
        client.post(f"/tasks/{task_id}/execute", json={})
        response = client.post(f"/tasks/{task_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_task_status(self, client):
        create_resp = client.post("/tasks", json={"title": "Status Task"})
        task_id = create_resp.json()["id"]
        response = client.get(f"/tasks/{task_id}/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "progress_pct" in data


# =====================================================================
# Health API Tests
# =====================================================================


class TestHealthAPI:
    """Tests for health API endpoints."""

    def test_health_overview(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "service" in data
        assert "uptime_seconds" in data

    def test_health_ready(self, client):
        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert "checks" in data

    def test_health_live(self, client):
        response = client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"

    def test_health_detailed(self, client):
        response = client.get("/health/detailed")
        assert response.status_code == 200
        data = response.json()
        assert "components" in data
        assert "summary" in data

    def test_health_components(self, client):
        response = client.get("/health/components")
        assert response.status_code == 200
        data = response.json()
        assert "components" in data
        assert data["total"] > 0

    def test_health_component_detail(self, client):
        response = client.get("/health/components/database")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "database"
        assert "status" in data

    def test_health_component_not_found(self, client):
        response = client.get("/health/components/nonexistent")
        assert response.status_code == 404

    def test_health_metrics(self, client):
        response = client.get("/health/metrics")
        assert response.status_code == 200
        data = response.json()
        assert "uptime_seconds" in data
        assert "response_times" in data


# =====================================================================
# Admin API Tests
# =====================================================================


class TestAdminAPI:
    """Tests for admin API endpoints."""

    def test_get_system_config(self, client):
        response = client.get("/admin/config")
        assert response.status_code == 200
        data = response.json()
        assert "config" in data
        assert "count" in data

    def test_get_config_value(self, client):
        response = client.get("/admin/config/max_agents_per_user")
        assert response.status_code == 200
        assert response.json()["key"] == "max_agents_per_user"

    def test_update_config_value(self, client):
        response = client.put("/admin/config/test_key", json={"value": "test_value"})
        assert response.status_code == 200
        assert response.json()["value"] == "test_value"

    def test_create_user(self, client):
        response = client.post("/admin/users", json={
            "username": "testuser",
            "email": "test@example.com",
            "role": "admin",
            "password_hash": "hashed_password_123",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "testuser"
        assert data["is_active"] is True

    def test_list_users(self, client):
        response = client.get("/admin/users")
        assert response.status_code == 200
        assert "items" in response.json()

    def test_system_stats(self, client):
        response = client.get("/admin/stats")
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "system" in data

    def test_list_audit_logs(self, client):
        response = client.get("/admin/audit-log")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_get_audit_log_entry(self, client):
        response = client.get("/admin/audit-log/audit-001")
        assert response.status_code == 200
        assert response.json()["id"] == "audit-001"

    def test_create_feature_flag(self, client):
        response = client.post("/admin/feature-flags", json={
            "name": "new_feature",
            "description": "A new feature",
            "enabled": True,
            "target_percentage": 50.0,
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "new_feature"
        assert data["enabled"] is True

    def test_list_feature_flags(self, client):
        response = client.get("/admin/feature-flags")
        assert response.status_code == 200
        assert "flags" in response.json()


# =====================================================================
# Cost API Tests
# =====================================================================


class TestCostAPI:
    """Tests for cost API endpoints."""

    def test_cost_overview(self, client):
        response = client.get("/cost/overview")
        assert response.status_code == 200
        data = response.json()
        assert "total_cost" in data
        assert "by_category" in data
        assert data["currency"] == "USD"

    def test_cost_breakdown(self, client):
        response = client.get("/cost/breakdown", params={"period": "daily"})
        assert response.status_code == 200
        data = response.json()
        assert "breakdown" in data
        assert data["period"] == "daily"

    def test_cost_daily(self, client):
        response = client.get("/cost/daily", params={"days": 7})
        assert response.status_code == 200
        data = response.json()
        assert data["days"] == 7
        assert "daily" in data

    def test_create_budget(self, client):
        response = client.post("/cost/budgets", json={
            "name": "test-budget",
            "amount": 500.0,
            "period": "monthly",
        })
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-budget"
        assert data["amount"] == 500.0

    def test_list_budgets(self, client):
        client.post("/cost/budgets", json={"name": "b1", "amount": 100.0})
        response = client.get("/cost/budgets")
        assert response.status_code == 200
        assert "budgets" in response.json()

    def test_get_budget(self, client):
        client.post("/cost/budgets", json={"name": "get-budget", "amount": 300.0})
        response = client.get("/cost/budgets/get-budget")
        assert response.status_code == 200
        assert response.json()["name"] == "get-budget"

    def test_create_alert(self, client):
        client.post("/cost/budgets", json={"name": "alert-budget", "amount": 100.0})
        response = client.post("/cost/alerts", json={
            "budget_name": "alert-budget",
            "alert_name": "warn",
            "threshold_pct": 80.0,
        })
        assert response.status_code == 201
        assert response.json()["threshold_pct"] == 80.0

    def test_list_alerts(self, client):
        response = client.get("/cost/alerts")
        assert response.status_code == 200
        assert "alerts" in response.json()

    def test_cost_forecast(self, client):
        response = client.get("/cost/forecast", params={"days": 7})
        assert response.status_code == 200
        data = response.json()
        assert data["forecast_days"] == 7
        assert "total_forecast" in data


# =====================================================================
# Plugins API Tests
# =====================================================================


class TestPluginsAPI:
    """Tests for plugins API endpoints."""

    def test_list_plugins(self, client):
        response = client.get("/plugins")
        assert response.status_code == 200
        data = response.json()
        assert "total" in data
        assert data["total"] > 0

    def test_get_plugin(self, client):
        response = client.get("/plugins/plugin-slack")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Slack Integration"
        assert data["status"] == "enabled"

    def test_get_plugin_not_found(self, client):
        response = client.get("/plugins/nonexistent")
        assert response.status_code == 404

    def test_install_plugin(self, client):
        response = client.post("/plugins/install", json={"plugin_id": "plugin-trello"})
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "plugin-trello"
        assert data["status"] == "installed"

    def test_enable_plugin(self, client):
        response = client.post("/plugins/plugin-jira/enable")
        assert response.status_code == 200
        assert response.json()["status"] == "enabled"

    def test_disable_plugin(self, client):
        response = client.post("/plugins/plugin-slack/disable")
        assert response.status_code == 200
        assert response.json()["status"] == "disabled"

    def test_upgrade_plugin(self, client):
        response = client.post("/plugins/plugin-github/upgrade", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "upgraded"
        assert "new_version" in data

    def test_get_plugin_config(self, client):
        response = client.get("/plugins/plugin-slack/config")
        assert response.status_code == 200
        assert "config" in response.json()

    def test_update_plugin_config(self, client):
        response = client.put("/plugins/plugin-slack/config", json={
            "config": {"webhook_url": "https://hooks.slack.com/test"}
        })
        assert response.status_code == 200
        data = response.json()
        assert data["config"]["webhook_url"] == "https://hooks.slack.com/test"

    def test_search_registry(self, client):
        response = client.get("/plugins/registry/search", params={"query": "integration"})
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_get_registry_plugin(self, client):
        response = client.get("/plugins/registry/plugin-sentry")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Sentry Integration"

    def test_uninstall_plugin(self, client):
        client.post("/plugins/install", json={"plugin_id": "plugin-sentry"})
        response = client.delete("/plugins/plugin-sentry")
        assert response.status_code == 204
