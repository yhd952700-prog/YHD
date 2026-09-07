"""organization — 十源 packages 层 facade.

组织 / 团队 / 部门 / 成员

复用 src/ 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.organization import (
    Organization,
    Team,
    Department,
    Member,
    Goal,
    Budget,
)

from src.ai.employee import (
    Employee,
    AgentPool,
)

__all__ = [
    "Organization",
    "Team",
    "Department",
    "Member",
    "Goal",
    "Budget",
    "Employee",
    "AgentPool",
]
