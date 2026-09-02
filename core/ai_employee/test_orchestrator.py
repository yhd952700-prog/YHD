"""Test file for AI Employee multi-agent orchestration."""

import pytest
import asyncio
from unittest.mock import MagicMock, patch

from core.ai_employee import Orchestrator, AgentRegistry
from core.ai_employee.agents import AgentSpec, AgentStatus


class TestOrchestrator:
    """Test orchestrator functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.registry = AgentRegistry()
        self.orchestrator = Orchestrator(self.registry)

    def test_register_agent(self):
        """Test agent registration."""
        spec = AgentSpec(
            agent_id="test_agent_1",
            name="Test Agent",
            capabilities=["text-generation", "summarization"],
        )
        agent_id = self.registry.register(spec)
        assert agent_id == "test_agent_1"
        assert self.registry.get_status(agent_id) == AgentStatus.OFFLINE

    def test_allocate_task(self):
        """Test task allocation."""
        # Register an agent with required capabilities
        spec = AgentSpec(
            agent_id="test_agent_1",
            name="Test Agent",
            capabilities=["text-generation", "summarization"],
        )
        self.registry.register(spec)

        # Allocate a task
        agent_id, message = asyncio.get_event_loop().run_until_complete(
            self.orchestrator.allocate_task(
                task_description="Summarize this document",
                required_capabilities=["summarization"],
            )
        )
        assert agent_id == "test_agent_1"
        assert "allocated" in message.lower()

    def test_complete_task(self):
        """Test task completion."""
        # Register an agent
        spec = AgentSpec(
            agent_id="test_agent_1",
            name="Test Agent",
            capabilities=["text-generation"],
        )
        self.registry.register(spec)

        # Allocate task
        asyncio.get_event_loop().run_until_complete(
            self.orchestrator.allocate_task(
                task_description="Generate text",
                required_capabilities=["text-generation"],
            )
        )

        # Complete task
        result = asyncio.get_event_loop().run_until_complete(
            self.orchestrator.complete_task(
                task_id="task_1",
                result="Generated summary",
                agent_id="test_agent_1",
            )
        )
        assert "completed" in result.lower()

    def test_summarize_results(self):
        """Test result summarization."""
        # Register multiple agents
        for i in range(3):
            spec = AgentSpec(
                agent_id=f"agent_{i}",
                name=f"Agent {i}",
                capabilities=["text-generation"],
            )
            self.registry.register(spec)

        summarization = self.orchestrator.summarize_results()
        assert summarization["total_agents"] == 3
        assert summarization["idle_agents"] + summarization["busy_agents"] == 3
