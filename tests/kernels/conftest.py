"""Shared fixtures for kernel-level unit tests.

Ensures the project root is on sys.path so that kernel modules using
absolute imports (``from src.kernels... import ...``) resolve correctly.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
