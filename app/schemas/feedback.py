"""反馈 API 请求 / 响应 Schema（P5）。"""

from typing import Literal

from pydantic import BaseModel, Field


class FeedbackIn(BaseModel):
    """匿名收藏 / 不喜欢提交。"""

    recipe_id: int = Field(description="菜谱 ID")
    action: Literal["like", "dislike"] = Field(description="行为：收藏 / 不喜欢")


class FeedbackOut(BaseModel):
    """反馈写入结果（幂等重复提交返回既有行 id）。"""

    id: int
