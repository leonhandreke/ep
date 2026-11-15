"""What a client shows about the server: version, library counts, directories."""

from importlib.metadata import version

from fastapi import APIRouter
from sqlalchemy import ScalarSelect
from sqlalchemy import select as sa_select
from sqlmodel import col, func

from ep.api.deps import SessionDep, SettingsDep
from ep.api.schemas import LibraryCountsRead, ServerPathRead, ServerRead
from ep.host_paths import to_host
from ep.models import Artist, Base, RadioEpisode, RadioShow, Recording, ReleaseGroup, Track
from ep.radio import radio_only_artist

router = APIRouter(tags=["server"])


@router.get("/server")
def get_server(session: SessionDep, settings: SettingsDep) -> ServerRead:
    """The server's version, how much the library holds (artists shown by default and
    those known only from radio, release groups, recordings, tracks, radio shows and
    episodes; one query) and its directories with their host paths."""

    def count(model: type[Base]) -> ScalarSelect[int]:
        return sa_select(func.count()).select_from(model).scalar_subquery()

    radio_only = func.count(col(Artist.id)).filter(radio_only_artist(col(Artist.id)))
    row = (
        session.connection()
        .execute(
            sa_select(
                func.count(col(Artist.id)),
                radio_only,
                count(ReleaseGroup),
                count(Recording),
                count(Track),
                count(RadioShow),
                count(RadioEpisode),
            ).select_from(Artist)
        )
        .one()
    )
    artists, radio_only_artists, release_groups, recordings, tracks, shows, episodes = row

    def path(value: object) -> ServerPathRead:
        return ServerPathRead(path=str(value), host_path=to_host(str(value), settings))

    return ServerRead(
        version=version("ep"),
        counts=LibraryCountsRead(
            artists=artists - radio_only_artists,
            radio_only_artists=radio_only_artists,
            release_groups=release_groups,
            recordings=recordings,
            tracks=tracks,
            radio_shows=shows,
            radio_episodes=episodes,
        ),
        library_dir=path(settings.library_dir),
        media_roots=[path(root) for root in settings.media_roots],
    )
