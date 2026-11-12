"""add tasks.due_date (B组第二轮 M4)

Revision ID: 007
Revises: 006
Create Date: 2026-11-12

``src/integrations/orm_models.py`` 的 Task 只有 priority，没有截止时间；
``src/api/tasks_api.py``（内存版）同样缺 due_date。本迁移给 ORM 的 tasks
表加一列可空的 due_date（DATETIME），与 API 层同步新增的字段对应。
历史行全部为 NULL，不编默认值。

Data safety: upgrade 只加一列（可空，无默认值），不碰已有数据；
downgrade 只删这一列。SQLite / Postgres 均可。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("due_date", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "due_date")
