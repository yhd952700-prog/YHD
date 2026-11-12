"""
Tests for LiuHao AI OS SDK and Solution Templates.

Tests cover:
- SDK exception hierarchy
- SDK configuration
- SDK client and resources
- Base solution template
- Customer support solution template
- Code assistant solution template
"""

import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from typing import Any, Dict

# Add SDK and solutions paths
SDK_PATH = os.path.join(os.path.dirname(__file__), "..", "sdk", "python")
SOLUTIONS_PATH = os.path.join(os.path.dirname(__file__), "..")
if SDK_PATH not in sys.path:
    sys.path.insert(0, SDK_PATH)
if SOLUTIONS_PATH not in sys.path:
    sys.path.insert(0, SOLUTIONS_PATH)


# ======================================================================
# Exception Tests
# ======================================================================

class TestLiuHaoExceptions(unittest.TestCase):
    """Test the SDK exception hierarchy."""

    def test_base_error_creation(self):
        """Test that the base LiuHaoError can be created with defaults."""
        from liuhao_sdk.exceptions import LiuHaoError
        err = LiuHaoError()
        self.assertIsInstance(err, Exception)
        self.assertIn("unexpected error", err.message)
        self.assertIsNone(err.code)
        self.assertIsNone(err.request_id)
        self.assertEqual(err.details, {})

    def test_base_error_with_params(self):
        """Test LiuHaoError with all parameters."""
        from liuhao_sdk.exceptions import LiuHaoError
        err = LiuHaoError(
            message="test error",
            code="E001",
            request_id="req-123",
            details={"key": "value"},
        )
        self.assertEqual(err.message, "test error")
        self.assertEqual(err.code, "E001")
        self.assertEqual(err.request_id, "req-123")
        self.assertEqual(err.details, {"key": "value"})

    def test_base_error_str_representation(self):
        """Test string representation includes request_id."""
        from liuhao_sdk.exceptions import LiuHaoError
        err = LiuHaoError(message="fail", request_id="req-456")
        s = str(err)
        self.assertIn("fail", s)
        self.assertIn("req-456", s)

    def test_authentication_error_is_liuhao_error(self):
        """Test that AuthenticationError is a subclass of LiuHaoError."""
        from liuhao_sdk.exceptions import LiuHaoError, AuthenticationError
        err = AuthenticationError()
        self.assertIsInstance(err, LiuHaoError)
        self.assertIsInstance(err, AuthenticationError)

    def test_not_found_error_has_resource_info(self):
        """Test that NotFoundError stores resource type and id."""
        from liuhao_sdk.exceptions import NotFoundError
        err = NotFoundError(
            resource_type="agent",
            resource_id="agent-123",
        )
        self.assertEqual(err.resource_type, "agent")
        self.assertEqual(err.resource_id, "agent-123")

    def test_rate_limit_error_has_retry_after(self):
        """Test that RateLimitError stores retry_after."""
        from liuhao_sdk.exceptions import RateLimitError
        err = RateLimitError(retry_after=5.5)
        self.assertEqual(err.retry_after, 5.5)

    def test_validation_error_has_field_errors(self):
        """Test that ValidationError stores field_errors."""
        from liuhao_sdk.exceptions import ValidationError
        err = ValidationError(
            field_errors={"name": "required", "email": "invalid format"},
        )
        self.assertEqual(len(err.field_errors), 2)
        self.assertIn("name", err.field_errors)

    def test_timeout_error_has_timeout(self):
        """Test that TimeoutError stores timeout value."""
        from liuhao_sdk.exceptions import TimeoutError
        err = TimeoutError(timeout=30.0)
        self.assertEqual(err.timeout, 30.0)

    def test_raise_for_status_200_no_error(self):
        """Test that raise_for_status doesn't raise for 2xx codes."""
        from liuhao_sdk.exceptions import raise_for_status
        # Should not raise
        raise_for_status(200)
        raise_for_status(201)
        raise_for_status(204)

    def test_raise_for_status_404(self):
        """Test that raise_for_status raises NotFoundError for 404."""
        from liuhao_sdk.exceptions import raise_for_status, NotFoundError
        with self.assertRaises(NotFoundError):
            raise_for_status(404)

    def test_raise_for_status_401(self):
        """Test that raise_for_status raises AuthenticationError for 401."""
        from liuhao_sdk.exceptions import raise_for_status, AuthenticationError
        with self.assertRaises(AuthenticationError):
            raise_for_status(401)

    def test_raise_for_status_429(self):
        """Test that raise_for_status raises RateLimitError for 429."""
        from liuhao_sdk.exceptions import raise_for_status, RateLimitError
        with self.assertRaises(RateLimitError):
            raise_for_status(429, details={"retry_after": "10"})

    def test_raise_for_status_500(self):
        """Test that raise_for_status raises APIError for 500."""
        from liuhao_sdk.exceptions import raise_for_status, APIError
        with self.assertRaises(APIError):
            raise_for_status(500)

    def test_all_exceptions_inherit_from_liuhao_error(self):
        """Test all exception types inherit from LiuHaoError."""
        from liuhao_sdk.exceptions import (
            LiuHaoError,
            AuthenticationError,
            APIError,
            NotFoundError,
            RateLimitError,
            ValidationError,
            TimeoutError,
        )
        classes = [
            AuthenticationError, APIError, NotFoundError,
            RateLimitError, ValidationError, TimeoutError,
        ]
        for cls in classes:
            err = cls(message="test")
            self.assertIsInstance(err, LiuHaoError,
                                f"{cls.__name__} should inherit from LiuHaoError")


# ======================================================================
# Config Tests
# ======================================================================

class TestConfig(unittest.TestCase):
    """Test the SDK configuration system."""

    def test_default_config(self):
        """Test default configuration values."""
        from liuhao_sdk.config import Config
        config = Config()
        self.assertIsNone(config.api_key)
        self.assertEqual(config.base_url, "https://api.liuhao.ai/v1")
        self.assertEqual(config.timeout, 30.0)
        self.assertEqual(config.max_retries, 3)
        self.assertFalse(config.debug)
        self.assertTrue(config.verify_ssl)

    def test_config_from_env(self):
        """Test loading config from environment variables."""
        from liuhao_sdk.config import Config
        with patch.dict(os.environ, {
            "LIUHAO_API_KEY": "test-key-env",
            "LIUHAO_BASE_URL": "https://test.liuhao.ai",
            "LIUHAO_TIMEOUT": "60",
            "LIUHAO_DEBUG": "true",
        }):
            config = Config.from_env()
            self.assertEqual(config.api_key, "test-key-env")
            self.assertEqual(config.base_url, "https://test.liuhao.ai")
            self.assertEqual(config.timeout, 60.0)
            self.assertTrue(config.debug)

    def test_config_from_json_file(self):
        """Test loading config from a JSON file."""
        from liuhao_sdk.config import Config
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, "liuhao_test_config.json")
        try:
            with open(tmp_path, "w") as f:
                json.dump({
                    "api_key": "json-test-key",
                    "base_url": "https://json-test.liuhao.ai",
                    "timeout": 45.0,
                    "max_retries": 5,
                }, f)
            config = Config.from_file(tmp_path)
            self.assertEqual(config.api_key, "json-test-key")
            self.assertEqual(config.base_url, "https://json-test.liuhao.ai")
            self.assertEqual(config.timeout, 45.0)
            self.assertEqual(config.max_retries, 5)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def test_config_validate_valid(self):
        """Test validation passes for valid config."""
        from liuhao_sdk.config import Config
        config = Config(api_key="test-key")
        issues = config.validate()
        self.assertEqual(len(issues), 0)
        self.assertTrue(config.is_valid())

    def test_config_validate_missing_api_key(self):
        """Test validation fails when API key is missing."""
        from liuhao_sdk.config import Config
        config = Config()
        issues = config.validate()
        self.assertTrue(any("api_key" in i for i in issues))
        self.assertFalse(config.is_valid())

    def test_config_validate_invalid_base_url(self):
        """Test validation fails for invalid base URL."""
        from liuhao_sdk.config import Config
        config = Config(api_key="key", base_url="not-a-url")
        issues = config.validate()
        self.assertTrue(any("base_url" in i for i in issues))

    def test_config_merge(self):
        """Test merging two configs."""
        from liuhao_sdk.config import Config
        base = Config(api_key="base-key", timeout=30.0)
        override = Config(timeout=60.0, max_retries=10)
        merged = base.merge(override)
        self.assertEqual(merged.api_key, "base-key")
        self.assertEqual(merged.timeout, 60.0)
        self.assertEqual(merged.max_retries, 10)

    def test_config_to_dict(self):
        """Test converting config to dictionary."""
        from liuhao_sdk.config import Config
        config = Config(api_key="test", timeout=25.0)
        d = config.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["api_key"], "test")
        self.assertEqual(d["timeout"], 25.0)


# ======================================================================
# Client Tests
# ======================================================================

class TestLiuHaoClient(unittest.TestCase):
    """Test the main SDK client."""

    def test_client_creation_with_api_key(self):
        """Test creating a client with an explicit API key."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="test-api-key")
        self.assertEqual(client.config.api_key, "test-api-key")

    def test_client_has_resources(self):
        """Test that client has all resource attributes."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="key")
        self.assertIsNotNone(client.agents)
        self.assertIsNotNone(client.memory)
        self.assertIsNotNone(client.knowledge)

    def test_client_get_request(self):
        """Test that GET requests work through the stub client."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="key")
        result = client.get("/test")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "GET")

    def test_client_post_request(self):
        """Test that POST requests work through the stub client."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="key")
        result = client.post("/test", json={"key": "value"})
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["body"], {"key": "value"})

    def test_client_build_url(self):
        """Test URL building from base and path."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="key", base_url="https://api.example.com/v1")
        url = client._build_url("/agents")
        self.assertEqual(url, "https://api.example.com/v1/agents")

    def test_client_build_headers_has_auth(self):
        """Test that request headers include auth bearer token."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="my-secret-key")
        headers = client._build_headers()
        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], "Bearer my-secret-key")

    def test_client_repr(self):
        """Test client string representation masks API key."""
        from liuhao_sdk.client import LiuHaoClient
        client = LiuHaoClient(api_key="secret123456")
        r = repr(client)
        self.assertIn("LiuHaoClient", r)
        self.assertNotIn("secret123456", r)


# ======================================================================
# Resource Tests
# ======================================================================

class TestAgentsResource(unittest.TestCase):
    """Test the Agents resource SDK."""

    def setUp(self):
        from liuhao_sdk.client import LiuHaoClient
        self.client = LiuHaoClient(api_key="test-key")
        self.agents = self.client.agents

    def test_list_agents(self):
        """Test listing agents."""
        result = self.agents.list()
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "GET")

    def test_get_agent(self):
        """Test getting a single agent."""
        result = self.agents.get("agent-123")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "GET")

    def test_create_agent(self):
        """Test creating an agent."""
        result = self.agents.create(
            name="Test Agent",
            description="A test agent",
            model="gpt-4",
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["body"]["name"], "Test Agent")

    def test_update_agent(self):
        """Test updating an agent."""
        result = self.agents.update("agent-123", name="Updated Name")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "PATCH")
        self.assertEqual(result["body"]["name"], "Updated Name")

    def test_delete_agent(self):
        """Test deleting an agent."""
        result = self.agents.delete("agent-123")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "DELETE")

    def test_run_agent(self):
        """Test running an agent task."""
        result = self.agents.run("agent-123", input_text="Hello")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "POST")

    def test_get_run_status(self):
        """Test getting run status."""
        result = self.agents.get_run_status("agent-123", "run-456")
        self.assertIsInstance(result, dict)

    def test_cancel_run(self):
        """Test cancelling a run."""
        result = self.agents.cancel_run("agent-123", "run-456")
        self.assertIsInstance(result, dict)

    def test_get_metrics(self):
        """Test getting agent metrics."""
        result = self.agents.get_metrics("agent-123")
        self.assertIsInstance(result, dict)

    def test_agent_status_enum(self):
        """Test AgentStatus enum values."""
        from liuhao_sdk.resources.agents import AgentStatus
        self.assertEqual(AgentStatus.ACTIVE.value, "active")
        self.assertEqual(AgentStatus.INACTIVE.value, "inactive")

    def test_agent_run_status_enum(self):
        """Test AgentRunStatus enum values."""
        from liuhao_sdk.resources.agents import AgentRunStatus
        self.assertEqual(AgentRunStatus.COMPLETED.value, "completed")
        self.assertEqual(AgentRunStatus.FAILED.value, "failed")


class TestMemoryResource(unittest.TestCase):
    """Test the Memory resource SDK."""

    def setUp(self):
        from liuhao_sdk.client import LiuHaoClient
        self.client = LiuHaoClient(api_key="test-key")
        self.memory = self.client.memory

    def test_store_memory(self):
        """Test storing a memory entry."""
        result = self.memory.store(content="Test memory content")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "POST")

    def test_retrieve_memory(self):
        """Test retrieving a memory entry."""
        result = self.memory.retrieve("mem-123")
        self.assertIsInstance(result, dict)

    def test_search_memory(self):
        """Test semantic search."""
        result = self.memory.search(query="test query", limit=5)
        self.assertIsInstance(result, dict)

    def test_memory_category_enum(self):
        """Test MemoryCategory enum."""
        from liuhao_sdk.resources.memory import MemoryCategory
        self.assertEqual(MemoryCategory.FACT.value, "fact")
        self.assertEqual(MemoryCategory.PREFERENCE.value, "preference")

    def test_memory_importance_enum(self):
        """Test MemoryImportance enum."""
        from liuhao_sdk.resources.memory import MemoryImportance
        self.assertEqual(MemoryImportance.HIGH.value, "high")
        self.assertEqual(MemoryImportance.CRITICAL.value, "critical")


class TestKnowledgeResource(unittest.TestCase):
    """Test the Knowledge base resource SDK."""

    def setUp(self):
        from liuhao_sdk.client import LiuHaoClient
        self.client = LiuHaoClient(api_key="test-key")
        self.knowledge = self.client.knowledge

    def test_create_base(self):
        """Test creating a knowledge base."""
        result = self.knowledge.create_base(name="Test KB")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["method"], "POST")

    def test_list_bases(self):
        """Test listing knowledge bases."""
        result = self.knowledge.list_bases()
        self.assertIsInstance(result, dict)

    def test_search_knowledge(self):
        """Test searching within a knowledge base."""
        result = self.knowledge.search("kb-123", query="test")
        self.assertIsInstance(result, dict)

    def test_create_collection(self):
        """Test creating a collection."""
        result = self.knowledge.create_collection("kb-123", name="Docs")
        self.assertIsInstance(result, dict)

    def test_search_mode_enum(self):
        """Test SearchMode enum."""
        from liuhao_sdk.resources.knowledge import SearchMode
        self.assertEqual(SearchMode.SEMANTIC.value, "semantic")
        self.assertEqual(SearchMode.HYBRID.value, "hybrid")


# ======================================================================
# Solution Template Tests
# ======================================================================

class TestBaseSolution(unittest.TestCase):
    """Test the base solution template."""

    def _create_solution(self):
        """Create a concrete solution for testing."""
        from solutions.templates.base_solution import (
            BaseSolution, SolutionMetadata
        )

        class ConcreteSolution(BaseSolution):
            def _build_metadata(self):
                return SolutionMetadata(
                    name="test-solution",
                    version="0.1.0",
                    industry="test",
                    description="Test solution",
                )

            def _build_config_schema(self):
                self.config_schema.add_field(
                    name="setting_a", field_type="string", required=True,
                    description="Test setting A",
                )
                self.config_schema.add_field(
                    name="setting_b", field_type="int", required=False,
                    default=42, description="Test setting B",
                )

            def _on_initialize(self):
                self._initialized = True

            def _on_deploy(self):
                self._deployed = True

            def _on_run(self, input_data):
                return {"result": "success", "input": input_data}

            def _on_teardown(self):
                self._torn_down = True

        return ConcreteSolution()

    def test_solution_creation(self):
        """Test creating a solution instance."""
        solution = self._create_solution()
        self.assertEqual(solution.metadata.name, "test-solution")
        self.assertEqual(solution.status.value, "uninitialized")

    def test_solution_initialize(self):
        """Test solution initialization."""
        solution = self._create_solution()
        solution.initialize()
        self.assertEqual(solution.status.value, "initialized")
        self.assertTrue(solution._initialized)

    def test_solution_configure(self):
        """Test solution configuration."""
        solution = self._create_solution()
        solution.initialize()
        solution.configure({"setting_a": "hello"})
        self.assertEqual(solution.status.value, "configured")
        self.assertEqual(solution.config["setting_a"], "hello")
        self.assertEqual(solution.config["setting_b"], 42)  # default applied

    def test_solution_deploy(self):
        """Test solution deployment."""
        solution = self._create_solution()
        solution.initialize()
        solution.configure({"setting_a": "value"})
        solution.deploy()
        self.assertEqual(solution.status.value, "deployed")
        self.assertTrue(solution._deployed)

    def test_solution_run(self):
        """Test running the solution."""
        solution = self._create_solution()
        solution.initialize()
        solution.configure({"setting_a": "value"})
        solution.deploy()
        result = solution.run({"key": "value"})
        self.assertEqual(result["result"], "success")
        self.assertEqual(result["input"], {"key": "value"})
        self.assertEqual(solution.metrics.total_requests, 1)

    def test_solution_monitor(self):
        """Test solution monitoring."""
        solution = self._create_solution()
        solution.initialize()
        solution.configure({"setting_a": "value"})
        solution.deploy()
        status = solution.monitor()
        self.assertIn("status", status)
        self.assertIn("health", status)
        self.assertIn("metrics", status)

    def test_solution_teardown(self):
        """Test solution teardown."""
        solution = self._create_solution()
        solution.initialize()
        solution.teardown()
        self.assertEqual(solution.status.value, "teardown")
        self.assertTrue(solution._torn_down)

    def test_solution_get_info(self):
        """Test getting solution info."""
        solution = self._create_solution()
        info = solution.get_info()
        self.assertEqual(info["name"], "test-solution")
        self.assertEqual(info["version"], "0.1.0")
        self.assertIn("status", info)

    def test_config_schema_validation_required(self):
        """Test config schema validation catches missing required fields."""
        solution = self._create_solution()
        solution.initialize()
        with self.assertRaises(ValueError):
            solution.configure({})  # missing required setting_a

    def test_config_schema_apply_defaults(self):
        """Test that config schema applies defaults."""
        from solutions.templates.base_solution import SolutionConfigSchema
        schema = SolutionConfigSchema()
        schema.add_field("a", default="default_a")
        schema.add_field("b", default=100)
        result = schema.apply_defaults({"a": "custom"})
        self.assertEqual(result["a"], "custom")
        self.assertEqual(result["b"], 100)

    def test_solution_pause_resume(self):
        """Test pausing and resuming a solution."""
        solution = self._create_solution()
        solution.initialize()
        solution.configure({"setting_a": "value"})
        solution.deploy()
        solution.pause()
        self.assertEqual(solution.status.value, "paused")
        solution.resume()
        self.assertEqual(solution.status.value, "deployed")


class TestCustomerSupportSolution(unittest.TestCase):
    """Test the customer support solution template."""

    def test_solution_metadata(self):
        """Test solution metadata is correct."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        self.assertEqual(solution.metadata.name, "customer-support")
        self.assertEqual(solution.metadata.industry, "customer-service")
        self.assertIn("customer-support", solution.metadata.tags)

    def test_full_workflow(self):
        """Test the full customer support ticket workflow."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        solution.initialize()
        solution.configure({"knowledge_base_id": "kb-123"})
        solution.deploy()

        result = solution.run({
            "subject": "Login problem",
            "description": "I can't log in to my account. Getting an error message about invalid credentials.",
            "customer_id": "cust-001",
            "priority": "high",
        })

        self.assertIn("ticket_id", result)
        self.assertIn("response", result)
        self.assertIn("quality_score", result)
        self.assertIn("action", result)
        self.assertIsInstance(result["quality_score"], float)

    def test_ticket_triage_billing(self):
        """Test that billing keywords route to billing category."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        solution.initialize()
        solution.configure({"knowledge_base_id": "kb-123"})
        solution.deploy()

        result = solution.run({
            "subject": "Refund request",
            "description": "I want a refund for my payment. The invoice is wrong.",
            "customer_id": "cust-002",
        })

        self.assertEqual(result["ticket"]["category"], "billing")

    def test_ticket_triage_technical(self):
        """Test that technical keywords route to technical category."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        solution.initialize()
        solution.configure({"knowledge_base_id": "kb-123"})
        solution.deploy()

        result = solution.run({
            "subject": "App crash",
            "description": "The app keeps crashing with an error. It's broken and not working.",
            "customer_id": "cust-003",
        })

        self.assertEqual(result["ticket"]["category"], "technical")

    def test_urgent_ticket_escalation(self):
        """Test that urgent tickets get escalated."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        solution.initialize()
        solution.configure({"knowledge_base_id": "kb-123"})
        solution.deploy()

        result = solution.run({
            "subject": "URGENT: System down",
            "description": "Everything is broken, production is down.",
            "customer_id": "cust-004",
            "priority": "urgent",
        })

        self.assertEqual(result["action"], "escalate")

    def test_support_metrics(self):
        """Test support metrics tracking."""
        from solutions.templates.customer_support import CustomerSupportSolution
        solution = CustomerSupportSolution()
        solution.initialize()
        solution.configure({"knowledge_base_id": "kb-123"})
        solution.deploy()

        # Run a few tickets
        for i in range(3):
            solution.run({
                "subject": f"Ticket {i}",
                "description": "Test ticket description",
                "customer_id": f"cust-{i}",
            })

        metrics = solution.get_support_metrics()
        self.assertIn("tickets_resolved", metrics)
        self.assertGreater(metrics["tickets_resolved"], 0)


class TestCodeAssistantSolution(unittest.TestCase):
    """Test the code assistant solution template."""

    def test_solution_metadata(self):
        """Test solution metadata is correct."""
        from solutions.templates.code_assistant import CodeAssistantSolution
        solution = CodeAssistantSolution()
        self.assertEqual(solution.metadata.name, "code-assistant")
        self.assertEqual(solution.metadata.industry, "software-development")

    def test_code_review_workflow(self):
        """Test the code review workflow."""
        from solutions.templates.code_assistant import CodeAssistantSolution
        solution = CodeAssistantSolution()
        solution.initialize()
        solution.configure({
            "languages": ["python"],
            "check_security": True,
        })
        solution.deploy()

        result = solution.run({
            "files": [
                {
                    "path": "main.py",
                    "content": "def hello():\n    print('hello world')\n    x = 1 + 1\n    return x\n",
                }
            ],
            "language": "python",
        })

        self.assertIn("review_id", result)
        self.assertIn("overall_score", result)
        self.assertIn("total_issues", result)
        self.assertIn("issues", result)
        self.assertIn("recommendations", result)
        self.assertIsInstance(result["overall_score"], float)
        self.assertGreaterEqual(result["overall_score"], 0.0)
        self.assertLessEqual(result["overall_score"], 100.0)

    def test_code_issues_have_severity(self):
        """Test that code review issues have severity levels."""
        from solutions.templates.code_assistant import CodeAssistantSolution
        solution = CodeAssistantSolution()
        solution.initialize()
        solution.configure({"languages": ["python"]})
        solution.deploy()

        result = solution.run({
            "files": [
                {"path": "test.py", "content": "x = 1\n" * 50},
            ],
            "language": "python",
        })

        if result["issues"]:
            issue = result["issues"][0]
            self.assertIn("severity", issue)
            self.assertIn(issue["severity"],
                         ["info", "low", "medium", "high", "critical"])

    def test_best_practices_enforcement(self):
        """Test best practices enforcement."""
        from solutions.templates.code_assistant import CodeAssistantSolution
        solution = CodeAssistantSolution()
        solution.initialize()
        solution.configure({"languages": ["python"]})
        solution.deploy()

        result = solution.enforce_best_practices(
            code="print('hello')",
            language="python",
        )
        self.assertIn("language", result)
        self.assertIn("compliance_score", result)

    def test_code_metrics(self):
        """Test code metrics tracking."""
        from solutions.templates.code_assistant import CodeAssistantSolution
        solution = CodeAssistantSolution()
        solution.initialize()
        solution.configure({"languages": ["python"]})
        solution.deploy()

        for _ in range(2):
            solution.run({
                "files": [{"path": "test.py", "content": "x = 1\n" * 30}],
                "language": "python",
            })

        metrics = solution.get_code_metrics()
        self.assertGreaterEqual(metrics["total_reviews"], 2)
        self.assertIn("bugs_found", metrics)
        self.assertIn("avg_code_quality_score", metrics)

    def test_strictness_affects_issue_count(self):
        """Test that stricter mode finds more issues."""
        from solutions.templates.code_assistant import CodeAssistantSolution

        lenient = CodeAssistantSolution()
        lenient.initialize()
        lenient.configure({"languages": ["python"], "strictness": "lenient"})
        lenient.deploy()

        strict = CodeAssistantSolution()
        strict.initialize()
        strict.configure({"languages": ["python"], "strictness": "strict"})
        strict.deploy()

        code_content = "x = 1\n" * 100
        lenient_result = lenient.run({
            "files": [{"path": "test.py", "content": code_content}],
            "language": "python",
        })
        strict_result = strict.run({
            "files": [{"path": "test.py", "content": code_content}],
            "language": "python",
        })

        # Strict mode should find at least as many issues as lenient
        self.assertGreaterEqual(
            strict_result["total_issues"],
            lenient_result["total_issues"],
        )


# ======================================================================
# SDK Package Tests
# ======================================================================

class TestSDKPackage(unittest.TestCase):
    """Test the SDK package imports and version."""

    def test_version_available(self):
        """Test that __version__ is available from the package."""
        import liuhao_sdk
        self.assertTrue(hasattr(liuhao_sdk, "__version__"))
        self.assertIsInstance(liuhao_sdk.__version__, str)

    def test_client_exported(self):
        """Test that LiuHaoClient is exported from the package."""
        from liuhao_sdk import LiuHaoClient
        self.assertTrue(callable(LiuHaoClient))

    def test_config_exported(self):
        """Test that Config is exported from the package."""
        from liuhao_sdk import Config
        self.assertTrue(callable(Config))

    def test_exceptions_exported(self):
        """Test that exception classes are exported."""
        from liuhao_sdk import (
            LiuHaoError,
            AuthenticationError,
            APIError,
            NotFoundError,
            RateLimitError,
            ValidationError,
            TimeoutError,
        )
        self.assertTrue(issubclass(AuthenticationError, LiuHaoError))
        self.assertTrue(issubclass(APIError, LiuHaoError))
        self.assertTrue(issubclass(NotFoundError, LiuHaoError))
        self.assertTrue(issubclass(RateLimitError, LiuHaoError))
        self.assertTrue(issubclass(ValidationError, LiuHaoError))
        self.assertTrue(issubclass(TimeoutError, LiuHaoError))


# ======================================================================
# Main
# ======================================================================

if __name__ == "__main__":
    unittest.main(verbosity=2)
