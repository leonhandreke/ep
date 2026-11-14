from fastapi import APIRouter

from ep.api import artists, health, recordings, release_groups, tracks

router = APIRouter()
router.include_router(health.router)
router.include_router(artists.router)
router.include_router(release_groups.router)
router.include_router(recordings.router)
router.include_router(tracks.router)
