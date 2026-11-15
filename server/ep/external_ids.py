"""External IDs have no foreign key: deleting an entity must delete its external IDs."""

import uuid

from sqlmodel import Session, col, delete

from ep.models import EntityType, ExternalId


def delete_external_ids(session: Session, entity_type: EntityType, entity_id: uuid.UUID) -> None:
    """Delete the external IDs of an entity (call this when deleting the entity)."""
    session.exec(
        delete(ExternalId).where(
            col(ExternalId.entity_type) == entity_type, col(ExternalId.entity_id) == entity_id
        )
    )
