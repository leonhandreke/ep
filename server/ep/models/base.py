import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import DateTime
from sqlalchemy.orm import QueryableAttribute
from sqlmodel import Field, SQLModel

# Deterministic constraint names, so Alembic can refer to them in migrations.
SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def rel(attribute: object) -> QueryableAttribute[Any]:
    """A relationship attribute, for load options: SQLModel types them as their targets."""
    return cast(QueryableAttribute[Any], attribute)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(SQLModel):
    """Common columns for every table: uuid4 primary key and timestamps.

    Usage: ``class Artist(Base, table=True): ...``
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        nullable=False,
        sa_column_kwargs={"onupdate": utcnow},
    )
