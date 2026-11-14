"""SQLModel tables.

Import every table module here so that ``SQLModel.metadata`` (used by Alembic
autogenerate) sees all tables.
"""

from ep.models.base import Base

__all__ = ["Base"]
