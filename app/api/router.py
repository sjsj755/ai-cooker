"""API 路由聚合。"""

from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.ingredients import router as ingredients_router
from app.api.routes.recipes import router as recipes_router
from app.api.routes.recommend import router as recommend_router
from app.api.routes.tags import router as tags_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ingredients_router, prefix="/api/ingredients", tags=["ingredients"])
api_router.include_router(recommend_router, prefix="/api/recipes", tags=["recipes"])
api_router.include_router(recipes_router, prefix="/api/recipes", tags=["recipes"])
api_router.include_router(tags_router, prefix="/api/tags", tags=["tags"])
