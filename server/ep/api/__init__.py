from fastapi import APIRouter

from ep.api import (
    artists,
    covers,
    health,
    imports,
    musicbrainz,
    recordings,
    release_groups,
    search,
    sources,
    tracks,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(artists.router)
router.include_router(release_groups.router)
router.include_router(recordings.router)
router.include_router(tracks.router)
router.include_router(sources.router)
router.include_router(covers.router)
router.include_router(search.router)
router.include_router(musicbrainz.router)
router.include_router(imports.router)
