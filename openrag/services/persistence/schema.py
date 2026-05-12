"""Metadata-only table definitions for the Postgres catalog.

Defines the same 7 tables that ``components/indexer/vectordb/models.py`` declares
with the SQLAlchemy ORM, but as :class:`sqlalchemy.Table` objects bound to a
single :class:`sqlalchemy.MetaData`. The new persistence layer talks to
Postgres through ``asyncpg`` with raw SQL; this module exists solely so that
Alembic's autogenerate has a metadata target to diff against, and so the
on-startup ``metadata.create_all()`` path keeps working until phase 9 retires
the legacy actor.

Column types, defaults, foreign keys, unique constraints, check constraints
and indexes must stay identical to the ORM models — Alembic will treat any
divergence as a pending schema change.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()


partitions = Table(
    "partitions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("partition", String, unique=True, nullable=False, index=True),
    Column("created_at", DateTime, default=datetime.now, nullable=False, index=True),
)


files = Table(
    "files",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("file_id", String, nullable=False, index=True),
    Column(
        "partition_name",
        String,
        ForeignKey("partitions.partition"),
        nullable=False,
        index=True,
    ),
    Column("file_metadata", JSON, nullable=True, default=dict),
    Column(
        "created_by",
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    ),
    Column("relationship_id", String, nullable=True, index=True),
    Column("parent_id", String, nullable=True, index=True),
    UniqueConstraint("file_id", "partition_name", name="uix_file_id_partition"),
    Index("ix_partition_file", "partition_name", "file_id"),
    Index("ix_relationship_partition", "relationship_id", "partition_name"),
    Index("ix_parent_partition", "parent_id", "partition_name"),
)


users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("external_user_id", String, unique=True, nullable=True, index=True),
    Column("display_name", String, nullable=True),
    Column("email", String, unique=True, nullable=True, index=True),
    Column("token", String, unique=True, nullable=True, index=True),
    Column("is_admin", Boolean, default=False, nullable=False),
    Column("created_at", DateTime, default=datetime.now, nullable=False),
    Column("file_quota", Integer, nullable=True, default=None),
    Column("file_count", Integer, nullable=False, default=0),
)


oidc_sessions = Table(
    "oidc_sessions",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "session_token_hash",
        String(64),
        unique=True,
        nullable=False,
        index=True,
    ),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("sid", String, nullable=True, index=True),
    Column("sub", String, nullable=False),
    Column("id_token_encrypted", LargeBinary, nullable=True),
    Column("access_token_encrypted", LargeBinary, nullable=True),
    Column("refresh_token_encrypted", LargeBinary, nullable=True),
    Column("access_token_expires_at", DateTime, nullable=False),
    Column("session_expires_at", DateTime, nullable=False),
    Column("created_at", DateTime, default=datetime.now, nullable=False),
    Column("last_refresh_at", DateTime, nullable=True),
    Column("revoked_at", DateTime, nullable=True),
    Index("ix_oidc_sessions_user_sub", "user_id", "sub"),
)


partition_memberships = Table(
    "partition_memberships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "partition_name",
        String,
        ForeignKey("partitions.partition", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "user_id",
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("role", String, nullable=False),
    Column("added_at", DateTime, default=datetime.now, nullable=False),
    UniqueConstraint("partition_name", "user_id", name="uix_partition_user"),
    CheckConstraint(
        "role IN ('owner','editor','viewer')",
        name="ck_membership_role",
    ),
    Index("ix_user_partition", "user_id", "partition_name"),
)


workspaces = Table(
    "workspaces",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("workspace_id", String, unique=True, nullable=False, index=True),
    Column(
        "partition_name",
        String,
        ForeignKey("partitions.partition", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "created_by",
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("display_name", String, nullable=True),
    Column("created_at", DateTime, default=datetime.now),
)


workspace_files = Table(
    "workspace_files",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "workspace_id",
        String,
        ForeignKey("workspaces.workspace_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "file_id",
        Integer,
        ForeignKey("files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    UniqueConstraint("workspace_id", "file_id", name="uix_workspace_file"),
)


__all__ = [
    "metadata",
    "partitions",
    "files",
    "users",
    "oidc_sessions",
    "partition_memberships",
    "workspaces",
    "workspace_files",
]
