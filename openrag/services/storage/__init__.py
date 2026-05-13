"""Storage adapters — concrete :class:`CatalogStore` and :class:`VectorStore`.

Phase 7 splits the legacy ``MilvusDB`` Ray god object into two clean
adapters that orchestrators consume through the core port ABCs:

* :class:`postgres_store.PostgresStore` — composes the asyncpg
  :class:`ConnectionManager` with every repository implementation under
  :mod:`openrag.services.persistence`, satisfying
  :class:`openrag.core.ports.catalog_store.CatalogStore`.
* :class:`milvus_store.MilvusStore` — Milvus-backed vector ops (Phase 7B,
  still a placeholder at this point in the refactor).

The Ray actor that callers know today lives at
:mod:`openrag.services.storage.milvus_ray_shim` and will be folded into the
new stores during Phase 7C.
"""

from services.storage.postgres_store import PostgresStore

__all__ = ["PostgresStore"]
