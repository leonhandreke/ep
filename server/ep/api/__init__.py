from fastapi import APIRouter

from ep.api import (
    artists,
    covers,
    details,
    health,
    imports,
    musicbrainz,
    notes,
    radio,
    recordings,
    release_groups,
    search,
    sources,
    tags,
    tracks,
    watch_dirs,
)

router = APIRouter()
router.include_router(health.router)
router.include_router(artists.router)
router.include_router(release_groups.router)
router.include_router(recordings.router)
router.include_router(tracks.router)
router.include_router(sources.router)
router.include_router(radio.router)
router.include_router(radio.appearances_router)
router.include_router(covers.router)
router.include_router(search.router)
router.include_router(notes.router)
router.include_router(details.router)
router.include_router(tags.router)
router.include_router(musicbrainz.router)
router.include_router(imports.router)
router.include_router(watch_dirs.router)
