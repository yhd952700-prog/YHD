"""
pytest configuration for LiuHao AI OS

Configures pytest with custom options, markers, and fixtures.
"""

import sys
from pathlib import Path

# Add src to Python path for test imports
SRC_PATH = Path(__file__).parent.parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# ==================== Pytest Configuration ====================

def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    # Register custom markers
    config.addinivalue_line("markers", "unit: Unit tests - fast, isolated tests")
    config.addinivalue_line("markers", "integration: Integration tests - test component interactions")
    config.addinivalue_line("markers", "e2e: End-to-end tests - full system tests")
    config.addinivalue_line("markers", "slow: Slow running tests (>10s)")
    config.addinivalue_line("markers", "security: Security-related tests")
    config.addinivalue_line("markers", "performance: Performance benchmarks")
    config.addinivalue_line("markers", "deployment: Deployment and infrastructure tests")
    
    # Configure asyncio - use ini option instead
    # asyncio_mode is set via pytest.ini / pyproject.toml


def pytest_collection_modifyitems(config, items):
    """Modify test collection - add markers based on path."""
    for item in items:
        # Add markers based on test path
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath) or "e2e" in item.name:
            item.add_marker(pytest.mark.e2e)
        elif "security" in str(item.fspath) or "security" in item.name:
            item.add_marker(pytest.mark.security)
        elif "performance" in str(item.fspath) or "performance" in item.name:
            item.add_marker(pytest.mark.performance)
        elif "deployment" in str(item.fspath) or "deployment" in item.name:
            item.add_marker(pytest.mark.deployment)
        else:
            item.add_marker(pytest.mark.unit)


def pytest_runtest_setup(item):
    """Setup before each test."""
    # Skip slow tests unless explicitly requested
    if item.get_closest_marker("slow"):
        if not item.config.getoption("--run-slow", default=False):
            pytest.skip("need --run-slow option to run")


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests",
    )
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run E2E tests",
    )


# ==================== Async Fixtures ====================

import pytest
import pytest_asyncio
import asyncio

@pytest_asyncio.fixture
async def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ==================== Test Path Configuration ====================

# Ensure test output directories exist
import os
from pathlib import Path

OUTPUT_DIRS = [
    "tests/output",
    "tests/output/coverage_html",
    "tests/data",
    "tests/fixtures",
]

for dir_path in OUTPUT_DIRS:
    Path(dir_path).mkdir(parents=True, exist_ok=True)


# ==================== Export ====================

__all__ = [
    "pytest_configure",
    "pytest_collection_modifyitems", 
    "pytest_runtest_setup",
    "pytest_addoption",
]