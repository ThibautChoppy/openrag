"""Asynchronous Postgres connection manager.

Owns a single :mod:`asyncpg` pool that every repository in
``services/persistence/`` borrows from. Lifecycle:

1. :meth:`ConnectionManager.initialize` creates the pool with a bounded
   exponential-backoff retry — the database container is often slower to
   accept connections than the app at startup.
2. :meth:`ConnectionManager.run_migrations` upgrades the schema to ``head``
   by invoking Alembic synchronously. Safe to call after ``initialize()``.
3. :meth:`ConnectionManager.shutdown` closes the pool.

The pool is exposed via the :attr:`pool` property; repositories receive a
``pool_getter`` callable so they always see the live pool even when the
manager is reinitialised in tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from openrag.core.config.infrastructure import RDBConfig


logger = logging.getLogger(__name__)


_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "alembic"
_ALEMBIC_INI = _MIGRATIONS_DIR / "alembic.ini"

_RETRY_ATTEMPTS = 5
_RETRY_BASE_DELAY_SECONDS = 1.0
_RETRY_MAX_DELAY_SECONDS = 16.0


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection init: decode ``json`` / ``jsonb`` columns into Python.

    asyncpg returns JSON values as strings by default. Repositories prefer
    receiving dicts so they don't have to ``json.loads`` every row read; we
    register codecs for both flavours so the column type doesn't matter.
    """
    await conn.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class ConnectionManager:
    """Lifecycle wrapper around an :class:`asyncpg.Pool`."""

    def __init__(self, config: RDBConfig) -> None:
        if not config.database:
            raise ValueError(
                "RDBConfig.database is required for ConnectionManager — "
                "set POSTGRES_DATABASE or wire it from the collection name."
            )
        self._dsn = f"postgresql://{config.user}:{config.password}@{config.host}:{config.port}/{config.database}"
        # Kept separately so we never log the password.
        self._dsn_log = f"postgresql://{config.user}@{config.host}:{config.port}/{config.database}"
        self._min_size = config.pool_min_size
        self._max_size = config.pool_max_size
        self._command_timeout = config.command_timeout
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError(
                "ConnectionManager.initialize() has not been called",
            )
        return self._pool

    async def initialize(self) -> None:
        """Open the pool with bounded exponential-backoff retry.

        Retries up to ``_RETRY_ATTEMPTS`` times with delays of 1, 2, 4, 8, 16
        seconds. Re-raises the final exception if every attempt fails.
        """
        if self._pool is not None:
            return

        last_exc: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                    command_timeout=self._command_timeout,
                    init=_init_connection,
                )
                logger.info(
                    "Connected to Postgres at %s (pool=%d..%d)",
                    self._dsn_log,
                    self._min_size,
                    self._max_size,
                )
                return
            except (OSError, asyncpg.PostgresError) as exc:
                last_exc = exc
                if attempt == _RETRY_ATTEMPTS:
                    break
                delay = min(
                    _RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                    _RETRY_MAX_DELAY_SECONDS,
                )
                logger.warning(
                    "Postgres connection attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt,
                    _RETRY_ATTEMPTS,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise RuntimeError(
            f"Failed to connect to Postgres at {self._dsn_log} after {_RETRY_ATTEMPTS} attempts: {last_exc}",
        ) from last_exc

    async def shutdown(self) -> None:
        if self._pool is None:
            return
        await self._pool.close()
        self._pool = None

    async def run_migrations(self) -> None:
        """Upgrade the schema to ``head`` via Alembic.

        Alembic uses a synchronous SQLAlchemy engine, so the call is offloaded
        to the default executor to avoid blocking the event loop.
        """
        await asyncio.to_thread(self._run_migrations_sync)

    def _run_migrations_sync(self) -> None:
        # Imported lazily so the module can be imported without alembic
        # installed (e.g. for type-checking in tooling).
        from alembic import command
        from alembic.config import Config

        if not _ALEMBIC_INI.exists():
            raise FileNotFoundError(
                f"Alembic config not found at {_ALEMBIC_INI}; did the migrations move?",
            )

        cfg = Config(str(_ALEMBIC_INI))
        # ``script_location`` in alembic.ini is relative to the .ini file via
        # %(here)s, so this resolves to services/persistence/migrations/alembic/.
        cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
        cfg.set_main_option("sqlalchemy.url", self._dsn)
        logger.info("Running Alembic migrations against %s", self._dsn_log)
        command.upgrade(cfg, "head")


__all__ = ["ConnectionManager"]
