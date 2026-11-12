"""
Test Coverage Configuration for LiuHao AI OS

Defines coverage settings, exclusions, and reporting options.

历史遗留说明：本文件只是一个名为 ``coverage`` 的 Python dict，
pytest-cov 不会自动读取它。真实生效的覆盖率配置在 pyproject.toml 的
[tool.coverage.run] / [tool.coverage.report]（与 scripts/run_tests.py
--coverage 的 --cov-fail-under=80 保持一致）。本文件保留仅作历史参照，
不要在这里改阈值——改 pyproject.toml。
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