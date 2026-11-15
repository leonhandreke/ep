"""The detail panel of an entity in one request: its summary, note, tags, backlinks and
radio appearances, composed from the reads of the single-purpose endpoints (which stay
for refreshing one part after an edit)."""

import uuid

from fastapi import APIRouter

from ep.api.deps import SessionDep
from ep.api.entities import entity_reads, get_entity_or_404
from ep.api.errors import NOT_FOUND
from ep.api.notes import backlink_reads, current_note_read
from ep.api.radio import artist_appearances, recording_appearances, release_group_appearances
from ep.api.schemas import EntityDetailsRead
from ep.api.tags import entity_tag_reads
from ep.models import EntityType

router = APIRouter(tags=["notes"])


@router.get("/entity-details/{type}/{id}", responses=NOT_FOUND)
def get_entity_details(type: EntityType, id: uuid.UUID, session: SessionDep) -> EntityDetailsRead:
    """Everything the detail panel shows for an entity (``id``: its MBID or internal
    ID; a track's is its recording's), read in one transaction: the entity, its note,
    its tags, the notes linking to it and, for a recording, an artist or a release
    group, the radio episodes that played it (or something of theirs)."""
    entity = get_entity_or_404(session, type, id)
    match type:
        case EntityType.recording:
            appearances = recording_appearances(session, entity.id)
        case EntityType.artist:
            appearances = artist_appearances(session, entity.id)
        case EntityType.release_group:
            appearances = release_group_appearances(session, entity.id)
        case _:
            appearances = []
    return EntityDetailsRead(
        entity=entity_reads(session, [entity])[entity.id],
        note=current_note_read(session, type, entity.id),
        tags=entity_tag_reads(session, type, entity.id),
        backlinks=backlink_reads(session, type, entity),
        radio_appearances=appearances,
    )
