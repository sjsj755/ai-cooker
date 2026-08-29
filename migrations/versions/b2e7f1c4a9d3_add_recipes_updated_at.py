"""add recipes.updated_at

Revision ID: b2e7f1c4a9d3
Revises: d7171e29d6e0
Create Date: 2026-08-29

updated_at 由 MySQL DDL 强制（DATETIME(3) + ON UPDATE CURRENT_TIMESTAMP(3)），
任何 SQL 更新路径（ORM / bulk / 原生 SQL）都会自动刷新，用于 BM25 语料缓存探针。
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b2e7f1c4a9d3"
down_revision: Union[str, Sequence[str], None] = "d7171e29d6e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 原生 SQL 确保 ON UPDATE CURRENT_TIMESTAMP(3) 落到 DDL；
    # Alembic add_column 不会渲染 server_onupdate。
    op.execute(
        "ALTER TABLE recipes "
        "ADD COLUMN updated_at DATETIME(3) NOT NULL "
        "DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)"
    )


def downgrade() -> None:
    op.drop_column("recipes", "updated_at")
