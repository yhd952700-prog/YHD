"""
Tests for Sandbox Backends

Tests all sandbox backend implementations:
- Base interface compliance
- Subprocess backend execution
- Backend manager fallback chain
- Resource limits enforcement
"""

import os
import sys
import time
import uuid
import pytest

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))))


from src.plugins.sandbox.backends.base import (
    SandboxBackendBase,
    SandboxBackendType,
    SandboxBackendStatus,
    ResourceLimits,
    ExecutionResult,
)
from src.plugins.sandbox.backends.subprocess_backend import SubprocessBackend, IS_UNIX
from src.plugins.sandbox.backends.manager import SandboxBackendManager


class TestSubprocessBackend:
    """Test the subprocess fallback backend."""

    def setup_method(self):
        self.backend = SubprocessBackend()

    def test_backend_type(self):
        assert self.backend.backend_type == SandboxBackendType.SUBPROCESS

    def test_is_available(self):
        assert self.backend.is_available() is True

    def test_get_status(self):
        assert self.backend.get_status() == SandboxBackendStatus.HEALTHY

    def test_get_backend_info(self):
        info = self.backend.get_backend_info()
        assert info["type"] == "subprocess"
        assert info["available"] is True
        assert info["status"] == "healthy"

    def test_execute_simple_command(self):
        result = self.backend.execute(
            execution_id="test-001",
            command=["echo", "hello world"],
        )
        assert result.success is True
        assert result.exit_code == 0
        assert "hello world" in result.stdout
        assert result.execution_time > 0

    def test_execute_failing_command(self):
        result = self.backend.execute(
            execution_id="test-002",
            command=["false"],  # exits with code 1
        )
        assert result.success is False
        assert result.exit_code == 1

    def test_execute_with_timeout(self):
        result = self.backend.execute(
            execution_id="test-003",
            command=["sleep", "10"],
            timeout=2,
        )
        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    def test_execute_with_environment(self):
        result = self.backend.execute(
            execution_id="test-004",
            command=["python", "-c", "import os; print(os.environ.get('TEST_VAR', 'not_set'))"],
            environment={"TEST_VAR": "injected"},
        )
        assert result.success is True
        assert "injected" in result.stdout

    def test_execute_with_input_data(self):
        result = self.backend.execute(
            execution_id="test-005",
            command=["python", "-c", "import sys; print(sys.stdin.read().strip())"],
            input_data="test input",
        )
        assert result.success is True
        assert "test input" in result.stdout

    def test_execute_with_memory_limit(self):
        # 128MB address space: a real cap that still leaves room for the
        # command. The previous 1MB never worked on Linux CI -- RLIMIT_AS of
        # 1MB cannot even start a dynamically linked binary. It passed locally
        # only because IS_UNIX is False on Windows, so no limit was applied.
        limits = ResourceLimits(memory_limit=128 * 1024 * 1024)
        result = self.backend.execute(
            execution_id="test-006",
            command=["echo", "test"],
            resource_limits=limits,
        )
        assert result.success is True

    @pytest.mark.skipif(not IS_UNIX, reason="RLIMIT_AS is Unix-only")
    def test_memory_limit_is_actually_enforced(self):
        # 1MB address space cannot start a dynamically linked binary, so the
        # child must fail -- this is the positive proof that the limit
        # actually reaches the process instead of being silently ignored.
        limits = ResourceLimits(memory_limit=1024 * 1024)
        result = self.backend.execute(
            execution_id="test-006-memcap",
            command=["echo", "test"],
            resource_limits=limits,
        )
        assert result.success is False

    def test_execute_with_working_directory(self):
        import tempfile
        tmpdir = tempfile.gettempdir()
        result = self.backend.execute(
            execution_id="test-007",
            command=["echo", "test"],
            working_directory=tmpdir,
        )
        assert result.success is True

    def test_cleanup(self):
        execution_id = f"test-{uuid.uuid4().hex[:8]}"
        self.backend.execute(
            execution_id=execution_id,
            command=["echo", "test"],
        )
        result = self.backend.cleanup(execution_id)
        assert result is True

    def test_result_structure(self):
        result = self.backend.execute(
            execution_id="test-structure",
            command=["echo", "structure_test"],
        )
        assert hasattr(result, "execution_id")
        assert hasattr(result, "success")
        assert hasattr(result, "exit_code")
        assert hasattr(result, "stdout")
        assert hasattr(result, "stderr")
        assert hasattr(result, "execution_time")
        assert hasattr(result, "resource_usage")
        assert hasattr(result, "backend_info")
        assert result.backend_info["backend"] == "subprocess"

    def test_result_to_dict_and_back(self):
        result = self.backend.execute(
            execution_id="test-dict",
            command=["echo", "dict_test"],
        )
        d = result.to_dict()
        restored = ExecutionResult.from_dict(d)
        assert restored.execution_id == result.execution_id
        assert restored.success == result.success
        assert restored.stdout == result.stdout


class TestResourceLimits:
    """Test ResourceLimits model."""

    def test_default_limits(self):
        limits = ResourceLimits()
        assert limits.cpu_limit is None
        assert limits.memory_limit is None
        assert limits.network_access is False

    def test_custom_limits(self):
        limits = ResourceLimits(
            cpu_limit=0.5,
            memory_limit=100 * 1024 * 1024,
            execution_time_limit=30,
            network_access=False,
            max_output_size=1024,
        )
        assert limits.cpu_limit == 0.5
        assert limits.memory_limit == 100 * 1024 * 1024
        assert limits.execution_time_limit == 30

    def test_to_dict_and_back(self):
        original = ResourceLimits(
            cpu_limit=1.0,
            memory_limit=512 * 1024 * 1024,
            execution_time_limit=60,
            network_access=True,
        )
        d = original.to_dict()
        restored = ResourceLimits.from_dict(d)
        assert restored.cpu_limit == original.cpu_limit
        assert restored.memory_limit == original.memory_limit
        assert restored.execution_time_limit == original.execution_time_limit
        assert restored.network_access == original.network_access


class TestSandboxBackendManager:
    """Test the backend manager."""

    def setup_method(self):
        self.manager = SandboxBackendManager()

    def test_init(self):
        assert self.manager._total_executions == 0

    def test_get_availability(self):
        avail = self.manager.get_availability()
        assert "gvisor" in avail
        assert "subprocess" in avail
        assert avail["subprocess"]["available"] is True
        assert avail["subprocess"]["status"] == "healthy"

    def test_select_backend_subprocess(self):
        """Subprocess should always be selectable as fallback."""
        backend = self.manager.select_backend(SandboxBackendType.SUBPROCESS)
        assert isinstance(backend, SubprocessBackend)

    def test_select_backend_fallback(self):
        """Manager should select subprocess when gVisor is unavailable."""
        backend = self.manager.select_backend()
        # On this dev machine, gVisor likely unavailable
        assert backend is not None
        assert backend.is_available() is True

    def test_execute_simple(self):
        result = self.manager.execute(
            execution_id="mgr-test-001",
            command=["echo", "manager_test"],
        )
        assert result.success is True
        assert "manager_test" in result.stdout

    def test_execute_records_history(self):
        execution_id = "mgr-test-002"
        self.manager.execute(
            execution_id=execution_id,
            command=["echo", "history_test"],
        )
        cached = self.manager.get_execution_result(execution_id)
        assert cached is not None
        assert cached.success is True

    def test_get_stats(self):
        self.manager.execute(
            execution_id="mgr-stats-001",
            command=["echo", "stats"],
        )
        stats = self.manager.get_stats()
        assert "total_executions" in stats
        assert "successful" in stats
        assert "failed" in stats
        assert "backends" in stats
        assert "active_backend" in stats
        assert stats["total_executions"] >= 1


class TestBackendBase:
    """Test abstract base class enforcement."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            SandboxBackendBase()

    def test_backend_type_enum_values(self):
        assert SandboxBackendType.GVISOR.value == "gvisor"
        assert SandboxBackendType.DOCKER.value == "docker"
        assert SandboxBackendType.SUBPROCESS.value == "subprocess"

    def test_backend_status_enum_values(self):
        assert SandboxBackendStatus.HEALTHY.value == "healthy"
        assert SandboxBackendStatus.UNAVAILABLE.value == "unavailable"
        assert SandboxBackendStatus.UNKNOWN.value == "unknown"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
