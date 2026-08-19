"""Declarative base, naming convention, and engine/session factory.

The naming convention gives every constraint and index a deterministic
name so Alembic autogenerate produces stable, reviewable migrations
instead of driver-assigned names that churn between runs.
"""

from __future__ import annotations

import os

from sqlalchemy import MetaData
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def database_url() -> str:
    """Resolve the Postgres connection URL from the environment.

    DATABASE_URL takes precedence (used by Alembic and the app runtime
    alike); falls back to a local dev default.
    """
    return os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://localhost/subscription_forecasting",
    )


_db_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def get_db_engine() -> Engine:
    global _db_engine
    if _db_engine is None:
        from sqlalchemy import create_engine

        _db_engine = create_engine(database_url(), future=True)
    return _db_engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_db_engine(), expire_on_commit=False, future=True)
    return _SessionFactory
