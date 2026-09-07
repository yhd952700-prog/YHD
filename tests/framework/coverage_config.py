"""
Test Coverage Configuration for LiuHao AI OS

Defines coverage settings, exclusions, and reporting options.
"""

# Coverage configuration for pytest-cov
# This file is used by pytest-cov automatically when present

coverage = {
    "run": {
        "source": ["src"],
        "omit": [
            "*/tests/*",
            "*/test_*.py",
            "*/__pycache__/*",
            "*/venv/*",
            "*/.venv/*",
            "*/env/*",
            "*/.env/*",
            "setup.py",
            "setup.cfg",
            "*/migrations/*",
            "*/conftest.py",
        ],
        "branch": True,
        "parallel": True,
        "concurrency": "multiprocessing",
    },
    "report": {
        "exclude_lines": [
            "pragma: no cover",
            "def __repr__",
            "raise AssertionError",
            "raise NotImplementedError",
            "if __name__ == .__main__.:",
            "pragma: no branch",
        ],
        "ignore_errors": False,
        "skip_covered": False,
        "show_missing": True,
        "precision": 2,
    },
    "html": {
        "directory": "tests/output/coverage_html",
        "title": "LiuHao AI OS Coverage Report",
    },
    "xml": {
        "output": "tests/output/coverage.xml",
    },
    "json": {
        "output": "tests/output/coverage.json",
    },
}