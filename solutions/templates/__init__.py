"""
LiuHao AI OS Solution Templates Package.

Provides pre-built, extensible solution templates for common
industry use cases built on the LiuHao AI OS platform.
"""

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionStatus,
    SolutionHealth,
    SolutionMetrics,
)
from solutions.templates.customer_support import CustomerSupportSolution
from solutions.templates.code_assistant import CodeAssistantSolution

__all__ = [
    "BaseSolution",
    "SolutionStatus",
    "SolutionHealth",
    "SolutionMetrics",
    "CustomerSupportSolution",
    "CodeAssistantSolution",
]
