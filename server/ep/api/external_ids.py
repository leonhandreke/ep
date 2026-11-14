"""External IDs: list/add/remove routes for each entity type that has them."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import Session, SQLModel, col, delete, select

from ep.api.deps import SessionDep, get_or_404
from ep.api.errors import CONFLICT, NOT_FOUND
from ep.api.schemas import ExternalIdCreate, ExternalIdRead
from ep.models import EntityType, ExternalId


def delete_external_ids(session: Session, entity_type: EntityType, entity_id: uuid.UUID) -> None:
    """Delete the external IDs of an entity (call this when deleting the entity)."""
    session.exec(
        delete(ExternalId).where(
            col(ExternalId.entity_type) == entity_type, col(ExternalId.entity_id) == entity_id
        )
    )


def add_external_id_routes(
    router: APIRouter, model: type[SQLModel], entity_type: EntityType, path: str
) -> None:
    """Add ``GET/POST {path}/{id}/external-ids`` and ``DELETE .../{external_id}`` to router."""

    def list_external_ids(id: uuid.UUID, session: SessionDep) -> list[ExternalIdRead]:
        get_or_404(session, model, id)
        rows = session.exec(
            select(ExternalId)
            .where(ExternalId.entity_type == entity_type, ExternalId.entity_id == id)
            .order_by(col(ExternalId.scheme), col(ExternalId.value))
        )
        return [ExternalIdRead.model_validate(row) for row in rows]

    def add_external_id(
        id: uuid.UUID, body: ExternalIdCreate, session: SessionDep
    ) -> ExternalIdRead:
        get_or_404(session, model, id)
        row = ExternalId(entity_type=entity_type, entity_id=id, **body.model_dump())
        session.add(row)
        session.commit()
        return ExternalIdRead.model_validate(row)

    def remove_external_id(id: uuid.UUID, external_id: uuid.UUID, session: SessionDep) -> None:
        row = session.get(ExternalId, external_id)
        if row is None or row.entity_type != entity_type or row.entity_id != id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"external_id {external_id} not found")
        session.delete(row)
        session.commit()

    name = entity_type.value
    router.add_api_route(
        f"{path}/{{id}}/external-ids",
        list_external_ids,
        methods=["GET"],
        responses=NOT_FOUND,
        name=f"list_{name}_external_ids",
    )
    router.add_api_route(
        f"{path}/{{id}}/external-ids",
        add_external_id,
        methods=["POST"],
        responses=NOT_FOUND | CONFLICT,
        status_code=status.HTTP_201_CREATED,
        name=f"add_{name}_external_id",
    )
    router.add_api_route(
        f"{path}/{{id}}/external-ids/{{external_id}}",
        remove_external_id,
        methods=["DELETE"],
        responses=NOT_FOUND,
        status_code=status.HTTP_204_NO_CONTENT,
        name=f"remove_{name}_external_id",
    )
