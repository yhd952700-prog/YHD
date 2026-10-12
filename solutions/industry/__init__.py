"""
LiuHao AI OS Industry Solutions Package.

Provides pre-built, industry-specific AI solution templates for
various verticals built on the LiuHao AI OS platform.

Each industry solution extends the BaseSolution template with
domain-specific roles, workflows, metrics, and configuration.
"""

from solutions.industry.registry import SolutionRegistry

__all__ = [
    "SolutionRegistry",
]
