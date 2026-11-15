import logging

from alembic import context
from sqlalchemy import create_engine
from sqlmodel import SQLModel

import ep.models  # noqa: F401  (registers all tables on SQLModel.metadata)
from ep.settings import get_settings

# Alembic is configured in pyproject.toml ([tool.alembic]); there is no
# alembic.ini, so logging is set up here.
logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("alembic").setLevel(logging.INFO)

config = context.config
target_metadata = SQLModel.metadata


def database_url() -> str:
    # Tests pass an explicit URL via config attributes; otherwise use settings.
    return config.attributes.get("database_url") or get_settings().database_url


def include_name(name: str | None, type_: str, parent_names: object) -> bool:
    """Leave the job queue's tables (migration 0006, not SQLModel tables) out of
    autogenerate and ``alembic check``."""
    return not (type_ == "table" and name and name.startswith("procrastinate_"))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_name=include_name
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
