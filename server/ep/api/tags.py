"""Tags: list (with usage counts), rename/merge, delete; the tags of an entity.
Entities are named in paths by MBID or internal ID. Everything with a tag:
``GET /search?tag_id=``."""

import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement
from sqlmodel import Session, col, func, select

from ep.api.deps import PAGED, PageDep, Paging, SessionDep, get_or_404
from ep.api.entities import get_entity_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.schemas import TagAdd, TagRead, TagUpdate
from ep.models import EntityTag, EntityType, Tag
from ep.notes.tags import rename_tag, tag_entity

router = APIRouter(tags=["tags"])


class TagSort(StrEnum):
    """The order of a tag list."""

    # By name, ignoring case.
    name = "name"
    # Most used first (then by name).
    count = "count"


def _tag_reads(
    session: Session,
    *where: ColumnElement[bool],
    page: Paging | None = None,
    sort: TagSort = TagSort.name,
) -> list[TagRead]:
    """Tags with their usage counts (one query)."""
    count = func.count(col(EntityTag.id))
    order = [func.lower(Tag.name), col(Tag.id)]
    if sort is TagSort.count:
        order.insert(0, count.desc())
    query = (
        select(Tag, count)
        .outerjoin(EntityTag, col(EntityTag.tag_id) == Tag.id)
        .where(*where)
        .group_by(col(Tag.id))
        .order_by(*order)
    )
    if page is not None:
        query = page.apply(session, query)
    return [TagRead(id=tag.id, name=tag.name, count=n) for tag, n in session.exec(query)]


def _tag_read(session: Session, id: uuid.UUID) -> TagRead:
    [read] = _tag_reads(session, col(Tag.id) == id)
    return read


@router.get("/tags", responses=PAGED)
def list_tags(
    session: SessionDep,
    page: PageDep,
    prefix: Annotated[str | None, Query(min_length=1)] = None,
    sort: TagSort = TagSort.name,
) -> list[TagRead]:
    """Tags with how many entities have each, by name or (``sort=count``) most used
    first. ``prefix`` (ignoring case) narrows them, for autocomplete."""
    where = []
    if prefix is not None:
        where.append(func.starts_with(func.lower(Tag.name), func.lower(prefix)))
    return _tag_reads(session, *where, page=page, sort=sort)


@router.get("/tags/{id}", responses=NOT_FOUND)
def get_tag(id: uuid.UUID, session: SessionDep) -> TagRead:
    get_or_404(session, Tag, id)
    return _tag_read(session, id)


@router.patch("/tags/{id}", responses=NOT_FOUND | CONFLICT)
def update_tag(id: uuid.UUID, body: TagUpdate, session: SessionDep) -> TagRead:
    """Rename a tag. If another tag has the new name (ignoring case), this tag is
    merged into it (its entities get that tag, now spelt as given) and deleted: the
    answer is the other tag."""
    tag = rename_tag(session, get_or_404(session, Tag, id), body.name)
    session.commit()
    return _tag_read(session, tag.id)


@router.delete("/tags/{id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
def delete_tag(id: uuid.UUID, session: SessionDep) -> None:
    """Delete a tag and remove it from every entity."""
    session.delete(get_or_404(session, Tag, id))
    session.commit()


# The tags of an entity


@router.get("/entity-tags/{type}/{id}", responses=NOT_FOUND)
def list_entity_tags(type: EntityType, id: uuid.UUID, session: SessionDep) -> list[TagRead]:
    """The tags of an entity (``id``: its MBID or internal ID), by name, with how many
    entities have each."""
    entity = get_entity_or_404(session, type, id)
    return entity_tag_reads(session, type, entity.id)


def entity_tag_reads(
    session: Session, entity_type: EntityType, entity_id: uuid.UUID
) -> list[TagRead]:
    """The tags of an entity, by name, with their usage counts (one query)."""
    tagged = select(EntityTag.tag_id).where(
        EntityTag.entity_type == entity_type, EntityTag.entity_id == entity_id
    )
    return _tag_reads(session, col(Tag.id).in_(tagged))


@router.post("/entity-tags/{type}/{id}", responses=NOT_FOUND)
def add_entity_tag(
    type: EntityType, id: uuid.UUID, body: TagAdd, session: SessionDep
) -> list[TagRead]:
    """Add a tag by name (an existing tag, ignoring case, or a new one); nothing
    changes if the entity has it. Returns the entity's tags."""
    entity = get_entity_or_404(session, type, id)
    tag_entity(session, type, entity.id, body.name)
    session.commit()
    return entity_tag_reads(session, type, entity.id)


@router.delete("/entity-tags/{type}/{id}/{tag_id}", responses=NOT_FOUND)
def remove_entity_tag(
    type: EntityType, id: uuid.UUID, tag_id: uuid.UUID, session: SessionDep
) -> list[TagRead]:
    """Remove a tag from an entity (the tag stays, even if nothing has it any more).
    Returns the entity's tags, as adding one does."""
    entity = get_entity_or_404(session, type, id)
    row = session.exec(
        select(EntityTag).where(
            EntityTag.tag_id == tag_id,
            EntityTag.entity_type == type,
            EntityTag.entity_id == entity.id,
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type} {id} has no tag {tag_id}")
    session.delete(row)
    session.commit()
    return entity_tag_reads(session, type, entity.id)
