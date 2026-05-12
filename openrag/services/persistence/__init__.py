"""Postgres persistence adapter — connection manager, schema, repositories.

This package contains the asyncpg-based Postgres adapter that replaces the
synchronous SQLAlchemy ORM in ``components/indexer/vectordb/utils.py``.

Public entry points (phase 7A.1):
    - :class:`connection.ConnectionManager` — pool lifecycle + migrations
    - :mod:`schema` — metadata-only Alembic target
"""

from openrag.services.persistence.connection import ConnectionManager
from openrag.services.persistence.schema import metadata

__all__ = ["ConnectionManager", "metadata"]
