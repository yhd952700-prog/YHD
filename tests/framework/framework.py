"""
Test Framework for LiuHao AI OS

Provides:
- Base test classes and utilities
- Mock providers and fixtures
- Test data generators
- Test runner and reporting utilities
- Coverage configuration
- CI integration helpers
"""

import pytest
import asyncio
import tempfile
import os
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, TypeVar
from unittest.mock import Mock, AsyncMock, MagicMock
from dataclasses import dataclass
from datetime import datetime

T = TypeVar('T')


# ==================== Test Configuration ====================

class TestConfig:
    """Test configuration constants."""
    
    # Default test timeouts
    UNIT_TEST_TIMEOUT = 5.0
    INTEGRATION_TEST_TIMEOUT = 30.0
    E2E_TEST_TIMEOUT = 60.0
    
    # Test data directories
    TEST_DATA_DIR = Path("tests/data")
    FIXTURES_DIR = Path("tests/fixtures")
    OUTPUT_DIR = Path("tests/output")
    
    # Coverage settings
    COVERAGE_THRESHOLD = 80
    COVERAGE_EXCLUDE = [
        "*/tests/*",
        "*/migrations/*",
        "*/__pycache__/*",
    ]


# ==================== Base Test Classes ====================

class BaseTestCase:
    """Base test case with common utilities."""
    
    def setup_method(self):
        """Setup before each test method."""
        self.temp_dir = tempfile.mkdtemp(prefix="liuhao_test_")
        self.created_files: List[str] = []
    
    def teardown_method(self):
        """Cleanup after each test method."""
        # Remove temporary files
        for file_path in self.created_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception:
                pass
        
        # Remove temporary directory
        try:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass
    
    def create_temp_file(self, content: str, suffix: str = ".json") -> str:
        """Create a temporary file with given content."""
        file_path = os.path.join(self.temp_dir, f"test_{len(self.created_files)}{suffix}")
        with open(file_path, 'w') as f:
            f.write(content)
        self.created_files.append(file_path)
        return file_path
    
    def create_temp_json(self, data: Dict[str, Any]) -> str:
        """Create a temporary JSON file."""
        return self.create_temp_file(json.dumps(data, indent=2), ".json")


class AsyncTestCase:
    """Base async test case with event loop management."""
    
    def setup_method(self):
        """Setup async test environment."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
    
    def teardown_method(self):
        """Cleanup async test environment."""
        self.loop.close()


class IntegrationTestCase(BaseTestCase):
    """Base class for integration tests."""
    
    def setup_method(self):
        super().setup_method()
        # Additional integration test setup
        self.services: Dict[str, Any] = {}
    
    def teardown_method(self):
        # Cleanup services
        for service in self.services.values():
            if hasattr(service, 'shutdown'):
                service.shutdown()
        super().teardown_method()


# ==================== Mock Providers ====================

class MockProviderFactory:
    """Factory for creating mock objects for various components."""
    
    @staticmethod
    def create_mock_provider(
        name: str = "mock_provider",
        model: str = "mock-model",
        responses: Optional[Dict[str, str]] = None,
    ) -> Mock:
        """Create a mock AI provider."""
        provider = Mock()
        provider.name = name
        provider.model = model
        provider.api_key = "[REDACTED]"
        
        default_responses = {
            "hello": "Hello! I'm a mock response.",
            "status": "System operational",
            "default": "Mock response",
        }
        all_responses = {**default_responses, **(responses or {})}
        
        def mock_generate(prompt: str, **kwargs) -> str:
            for key, response in all_responses.items():
                if key in prompt.lower():
                    return response
            return all_responses["default"]
        
        provider.generate = Mock(side_effect=mock_generate)
        provider.generate_with_retry = Mock(side_effect=mock_generate)
        provider.get_capabilities = Mock(return_value={
            "name": name,
            "model": model,
            "supports_streaming": False,
            "supports_structured_output": False,
            "supports_function_calling": False,
            "max_context_tokens": 4096,
        })
        
        return provider
    
    @staticmethod
    def create_mock_audit_store() -> Mock:
        """Create a mock audit store."""
        store = Mock()
        store.emit = Mock(return_value="mock-event-id")
        store.get = Mock(return_value=None)
        store.list = Mock(return_value=[])
        store.filter_by_type = Mock(return_value=[])
        store.filter_by_severity = Mock(return_value=[])
        store.filter_by_user = Mock(return_value=[])
        store.get_stats = Mock(return_value={
            "total": 0,
            "by_type": {},
            "by_severity": {},
            "by_status": {},
        })
        store.verify_integrity = Mock(return_value=True)
        return store
    
    @staticmethod
    def create_mock_observability_store() -> Mock:
        """Create a mock observability store."""
        store = Mock()
        store.emit_span = Mock(return_value="mock-span-id")
        store.get_span = Mock(return_value=None)
        store.list_spans = Mock(return_value=[])
        store.emit_metric = Mock(return_value="mock-metric-id")
        store.get_stats = Mock(return_value={
            "total_spans": 0,
            "total_metrics": 0,
            "by_kind": {},
            "by_status": {},
        })
        store.verify_integrity = Mock(return_value=True)
        return store
    
    @staticmethod
    def create_mock_performance_cache() -> Mock:
        """Create a mock performance cache."""
        cache = Mock()
        cache.get = Mock(return_value=None)
        cache.put = Mock(return_value=None)
        cache.get_stats = Mock(return_value={
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "eviction_count": 0,
            "expired_count": 0,
            "hit_rate": 0.0,
        })
        return cache


# ==================== Test Data Generators ====================

class TestDataGenerator:
    """Generate test data for various modules."""
    
    @staticmethod
    def generate_audit_event(**overrides) -> Dict[str, Any]:
        """Generate a test audit event."""
        base = {
            "event_id": f"test-event-{datetime.now().timestamp()}",
            "event_type": "test",
            "timestamp": datetime.now().timestamp(),
            "source": "test_module",
            "user_id": "test_user",
            "severity": "low",
            "status": "success",
            "message": "Test audit event",
            "details": {},
        }
        base.update(overrides)
        return base
    
    @staticmethod
    def generate_span(**overrides) -> Dict[str, Any]:
        """Generate a test span."""
        base = {
            "span_id": f"span-{datetime.now().timestamp()}",
            "trace_id": f"trace-{datetime.now().timestamp()}",
            "name": "test_operation",
            "kind": "internal",
            "start_time": int(datetime.now().timestamp() * 1e9),
            "end_time": int((datetime.now().timestamp() + 1) * 1e9),
            "attributes": {"test": "value"},
            "status": "ok",
        }
        base.update(overrides)
        return base
    
    @staticmethod
    def generate_metric(**overrides) -> Dict[str, Any]:
        """Generate a test metric."""
        base = {
            "name": "test_metric",
            "description": "Test metric",
            "unit": "count",
            "kind": "counter",
            "double_data_points": [
                {"timestamp": int(datetime.now().timestamp() * 1e9), "value": 1.0, "attributes": {}}
            ],
        }
        base.update(overrides)
        return base
    
    @staticmethod
    def generate_alert(**overrides) -> Dict[str, Any]:
        """Generate a test alert."""
        base = {
            "id": f"alert-{datetime.now().timestamp()}",
            "alert_type": "metric_threshold",
            "name": "Test Alert",
            "severity": "medium",
            "message": "Test alert triggered",
            "source": "test",
            "state": "firing",
            "fired_at": datetime.now().timestamp(),
            "resolved_at": None,
        }
        base.update(overrides)
        return base
    
    @staticmethod
    def generate_plugin_metadata(**overrides) -> Dict[str, Any]:
        """Generate plugin metadata."""
        base = {
            "name": "test_plugin",
            "version": "1.0.0",
            "description": "Test plugin",
            "author": "Test Author",
            "license": "MIT",
            "keywords": ["test"],
            "compatibility": ">=1.0.0",
            "entry_points": {},
        }
        base.update(overrides)
        return base
    
    @staticmethod
    def generate_security_key(**overrides) -> Dict[str, Any]:
        """Generate a test API key."""
        base = {
            "id": f"key-{datetime.now().timestamp()}",
            "name": "Test Key",
            "key_hash": "test_hash",
            "encrypted_key": "encrypted_value",
            "scopes": ["readonly"],
            "status": "active",
            "created_at": datetime.now().timestamp(),
            "expires_at": None,
            "usage_count": 0,
            "rate_limit_rpm": 60,
        }
        base.update(overrides)
        return base


# ==================== Test Utilities ====================

class TestUtils:
    """Common test utilities."""
    
    @staticmethod
    def assert_eventually(condition: Callable[[], bool], timeout: float = 5.0, interval: float = 0.1):
        """Assert that a condition becomes true within timeout."""
        import time
        start = time.time()
        while time.time() - start < timeout:
            if condition():
                return
            time.sleep(interval)
        raise AssertionError(f"Condition not met within {timeout}s")
    
    @staticmethod
    def assert_dict_contains(actual: Dict[str, Any], expected: Dict[str, Any]):
        """Assert that actual dict contains all expected key-value pairs."""
        for key, value in expected.items():
            assert key in actual, f"Missing key: {key}"
            assert actual[key] == value, f"Value mismatch for {key}: expected {value}, got {actual[key]}"
    
    @staticmethod
    def create_test_config(**overrides) -> Dict[str, Any]:
        """Create a test configuration dict."""
        config = {
            "app": {
                "name": "LiuHao AI OS Test",
                "version": "1.0.0-test",
            },
            "api": {
                "host": "127.0.0.1",
                "port": 8080,
                "swagger_ui": True,
            },
            "security": {
                "api_keys": {
                    "storage_path": "data/security",
                },
                "jwt": {
                    "secret_key": "test_secret",
                    "algorithm": "HS256",
                },
            },
            "plugins": {
                "path": "src/plugins",
            },
            "storage": {
                "path": "data/storage",
            },
            "observability": {
                "path": "data/observability",
            },
        }
        config.update(overrides)
        return config


# ==================== Test Fixtures (Pytest) ====================

@pytest.fixture
def temp_dir():
    """Provide a temporary directory for tests."""
    temp_dir = tempfile.mkdtemp(prefix="liuhao_test_")
    yield Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def test_config():
    """Provide test configuration."""
    return TestUtils.create_test_config()


@pytest.fixture
def mock_provider():
    """Provide a mock AI provider."""
    return MockProviderFactory.create_mock_provider()


@pytest.fixture
def mock_audit_store():
    """Provide a mock audit store."""
    return MockProviderFactory.create_mock_audit_store()


@pytest.fixture
def mock_observability_store():
    """Provide a mock observability store."""
    return MockProviderFactory.create_mock_observability_store()


@pytest.fixture
def mock_cache():
    """Provide a mock performance cache."""
    return MockProviderFactory.create_mock_performance_cache()


@pytest.fixture
def test_data():
    """Provide test data generator."""
    return TestDataGenerator()


# ==================== Test Runner Utilities ====================

class TestRunner:
    """Utility for running tests programmatically."""
    
    @staticmethod
    def run_unit_tests(test_path: str = "tests", **pytest_args) -> int:
        """Run unit tests."""
        args = ["-v", "--tb=short", test_path, *pytest_args]
        return pytest.main(args)
    
    @staticmethod
    def run_integration_tests(test_path: str = "tests/integration", **pytest_args) -> int:
        """Run integration tests."""
        args = ["-v", "--tb=long", "-m", "integration", test_path, *pytest_args]
        return pytest.main(args)
    
    @staticmethod
    def run_e2e_tests(test_path: str = "tests", **pytest_args) -> int:
        """Run E2E tests."""
        args = ["-v", "--tb=long", "-m", "e2e", test_path, *pytest_args]
        return pytest.main(args)
    
    @staticmethod
    def run_with_coverage(
        test_path: str = "tests",
        coverage_threshold: int = TestConfig.COVERAGE_THRESHOLD,
        **pytest_args,
    ) -> int:
        """Run tests with coverage reporting."""
        args = [
            f"--cov=src",
            f"--cov-report=term-missing",
            f"--cov-fail-under={coverage_threshold}",
            "-v",
            test_path,
            *pytest_args,
        ]
        return pytest.main(args)


# ==================== Test Markers ====================

# Register custom pytest markers
pytestmark = [
    pytest.mark.unit,
    pytest.mark.integration,
    pytest.mark.e2e,
    pytest.mark.slow,
    pytest.mark.security,
    pytest.mark.performance,
]


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "security: Security-related tests")
    config.addinivalue_line("markers", "performance: Performance tests")


# ==================== CI Integration ====================

class CIIntegration:
    """CI/CD integration helpers."""
    
    @staticmethod
    def generate_github_actions_workflow() -> str:
        """Generate GitHub Actions workflow YAML."""
        return """
name: CI/CD Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install -r requirements-test.txt
      - name: Run unit tests
        run: python -m pytest tests/ -v --tb=short -m "not integration and not e2e"
      - name: Run integration tests
        run: python -m pytest tests/integration/ -v --tb=long -m integration
      - name: Check coverage
        run: python -m pytest tests/ --cov=src --cov-report=xml --cov-fail-under=80
      - name: Upload coverage
        uses: codecov/codecov-action@v4
  
  security:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run security tests
        run: python -m pytest tests/ -v --tb=short -m security
      - name: Run bandit
        run: bandit -r src/ -f json -o bandit-report.json || true
      - name: Upload security report
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: bandit-report.json

  deploy:
    needs: [test, security]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -t liuhao-ai-os:${{ github.sha }} .
      - name: Push to registry
        run: |
          echo ${{ secrets.DOCKER_PASSWORD }} | docker login -u ${{ secrets.DOCKER_USERNAME }} --password-stdin
          docker push liuhao-ai-os:${{ github.sha }}
"""

    @staticmethod
    def generate_dockerfile() -> str:
        """Generate Dockerfile for the project."""
        return """
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Create non-root user
RUN useradd --create-home --shell /bin/bash app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/app/.local

# Copy application code
COPY --chown=app:app src/ ./src/
COPY --chown=app:app config/ ./config/
COPY --chown=app:app scripts/ ./scripts/

# Switch to non-root user
USER app

# Add local packages to PATH
ENV PATH=/home/app/.local/bin:$PATH

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run application
CMD ["python", "-m", "uvicorn", "src.gateway.main:app", "--host", "0.0.0.0", "--port", "8080"]
"""


# ==================== Export Functions ====================

__all__ = [
    "BaseTestCase",
    "AsyncTestCase", 
    "IntegrationTestCase",
    "TestConfig",
    "MockProviderFactory",
    "TestDataGenerator",
    "TestUtils",
    "TestRunner",
    "CIIntegration",
    "TestUtils",
]