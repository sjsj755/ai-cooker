"""user_feedback.client_fingerprint + 幂等唯一索引（P5）

Revision ID: f4a9c2e6d1b0
Revises: b2e7f1c4a9d3
Create Date: 2026-08-29

- client_fingerprint VARCHAR(64)：SHA-256(IP + FEEDBACK_SALT) 十六进制指纹，
  不落明文 IP；历史行为 NULL（匿名化兜底）。
- 唯一索引 (recipe_id, client_fingerprint, action)：同 (recipe, fingerprint, action)
  重复提交幂等 200 不新增行；切换 action 允许新增行。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f4a9c2e6d1b0"
down_revision: Union[str, Sequence[str], None] = "b2e7f1c4a9d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_feedback",
        sa.Column("client_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "uq_user_feedback_recipe_fingerprint_action",
        "user_feedback",
        ["recipe_id", "client_fingerprint", "action"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_user_feedback_recipe_fingerprint_action", table_name="user_feedback"
    )
    op.drop_column("user_feedback", "client_fingerprint")
