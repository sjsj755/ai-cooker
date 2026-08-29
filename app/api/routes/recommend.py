"""POST /api/recipes/recommend：P0 占位，P3 实现完整工作流。"""

from fastapi import APIRouter, HTTPException

from app.schemas.recommend import RecommendRequest, RecommendResponse

router = APIRouter()


@router.post("/recommend", response_model=RecommendResponse, status_code=501)
async def recommend(payload: RecommendRequest) -> RecommendResponse:
    """占位端点：P3 接入 LangGraph 工作流。501 为 P0 预期行为。"""
    if not any(ingredient.strip() for ingredient in payload.ingredients):
        raise HTTPException(status_code=400, detail="食材列表不能为空")
    return RecommendResponse(
        recipes=[],
        degraded=True,
        notice="推荐功能将在 P3 阶段实现（当前为骨架占位）",
    )
