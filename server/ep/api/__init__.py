from fastapi import APIRouter

from ep.api import health

router = APIRouter()
router.include_router(health.router)
