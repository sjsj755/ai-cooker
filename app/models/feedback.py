"""用户反馈表（收藏 / 不喜欢；P5 加入匿名指纹 + 幂等唯一索引）。"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserFeedback(Base):
    __tablename__ = "user_feedback"
    __table_args__ = (
        # 幂等：同 (recipe, fingerprint, action) 至多一行；切换 action 允许新增行。
        # 指纹为 SHA-256(IP + FEEDBACK_SALT)，64 位十六进制，不落明文 IP。
        UniqueConstraint(
            "recipe_id",
            "client_fingerprint",
            "action",
            name="uq_user_feedback_recipe_fingerprint_action",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[int | None] = mapped_column(
        ForeignKey("recipes.id", ondelete="SET NULL")
    )
    client_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # like / dislike
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
