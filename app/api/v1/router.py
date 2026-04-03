from fastapi import APIRouter

from app.api.v1.endpoints import webhook, health

api_router = APIRouter()

api_router.include_router(webhook.router, tags=["webhook"])
api_router.include_router(health.router, tags=["health"])
