"""标签与菜谱-标签关联表。"""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tag(Base):
    """标签词典：kind 区分过敏原 / 忌口 / 菜系 / 口味。"""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    recipe_tags: Mapped[list["RecipeTag"]] = relationship(back_populates="tag")


class RecipeTag(Base):
    """菜谱-标签关联（联合主键）。"""

    __tablename__ = "recipe_tags"
    __table_args__ = (
        UniqueConstraint("recipe_id", "tag_id", name="uq_recipe_tag"),
    )

    recipe_id: Mapped[int] = mapped_column(
        ForeignKey("recipes.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )

    recipe: Mapped["Recipe"] = relationship(back_populates="recipe_tags")
    tag: Mapped["Tag"] = relationship(back_populates="recipe_tags")
