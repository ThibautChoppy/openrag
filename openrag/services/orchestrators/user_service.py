"""UserService — user CRUD orchestration (Phase 8A.2).

Business logic extracted from ``routers/users.py``. The legacy router was
already mostly delegation (each endpoint issued one Ray ``vectordb``
user call); this service owns the parts that were *not* pure
delegation: input validation, the default-quota rule, and the
existence / not-found semantics. It talks to the Phase 7
:class:`UserRepository` directly instead of the Ray ``vectordb`` actor.

Response shape is kept identical to the legacy endpoints — the repo's
``*_dict`` helpers reproduce the exact ``PartitionFileManager`` dict
contract the Ray actor used to expose, so existing clients are
unaffected.

Owner-partition cascade (restored in Phase 8B): the legacy Ray
``delete_user`` deleted every partition the user owned (Milvus +
Postgres) before removing the row. That cross-cutting delete is owned by
PartitionService; :meth:`delete_user` composes it so the behaviour
matches the legacy endpoint again.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from core.utils.exceptions import UserNotFoundError, ValidationError
from utils.logger import get_logger

if TYPE_CHECKING:
    from core.ports.partition_membership_repo import PartitionMembershipRepository
    from core.ports.user_repo import UserRepository
    from models.user import UserCreate, UserUpdate
    from services.orchestrators.auth_service import AuthService
    from services.orchestrators.partition_service import PartitionService

logger = get_logger()

# Pragmatic, permissive address shape — the IdP / caller is the real
# source of truth; this only rejects obviously malformed input.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MAX_DISPLAY_NAME = 255


class UserService:
    """User account CRUD — validation + repo delegation."""

    def __init__(
        self,
        *,
        user_repo: UserRepository,
        auth_service: AuthService,
        default_file_quota: int,
        partition_service: PartitionService,
        membership_repo: PartitionMembershipRepository,
    ) -> None:
        self._user_repo = user_repo
        # Injected per the Phase 8 prescribed signature: the place future
        # phases consolidate authz (e.g. require_admin) once the shared
        # FastAPI Depends wrappers in routers/utils.py are retired.
        self._auth_service = auth_service
        # Legacy ``file_quota_per_user`` (config.rdb.default_file_quota).
        # Only applied as a creation default when > 0, matching the old
        # ``vectordb.create_user`` behaviour.
        self._default_file_quota = default_file_quota
        # 8B: reinstating the owner-partition cascade dropped in 8A.2.
        # delete_user must also delete every partition the user owns
        # (Milvus + Postgres) — that cross-cutting delete is owned by
        # PartitionService, so UserService composes it.
        self._partition_service = partition_service
        self._membership_repo = membership_repo

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_profile(display_name: Any, email: Any) -> None:
        if isinstance(display_name, str) and len(display_name) > _MAX_DISPLAY_NAME:
            raise ValidationError(
                f"display_name exceeds {_MAX_DISPLAY_NAME} characters.",
                status_code=400,
            )
        if isinstance(email, str) and email.strip() and not _EMAIL_RE.match(email.strip()):
            raise ValidationError(f"Invalid email address: {email!r}", status_code=400)

    async def _ensure_exists(self, user_id: int) -> None:
        if not await self._user_repo.user_exists(user_id):
            logger.warning(f"User with ID {user_id} does not exist.")
            raise UserNotFoundError(f"User with ID {user_id} does not exist.")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    async def create_user(self, body: UserCreate) -> dict:
        """Create a user, returning the legacy dict (token shown once)."""
        fields = body.model_dump()
        self._validate_profile(fields.get("display_name"), fields.get("email"))

        file_quota = fields.get("file_quota")
        if self._default_file_quota > 0 and file_quota is None:
            file_quota = self._default_file_quota

        user = await self._user_repo.create_legacy_user(
            display_name=fields.get("display_name"),
            external_user_id=fields.get("external_user_id"),
            email=fields.get("email"),
            is_admin=fields.get("is_admin", False),
            file_quota=file_quota,
        )
        logger.info("Created new user", user_id=user["id"])
        return user

    async def list_users(self) -> list[dict]:
        users = await self._user_repo.list_users_dict()
        logger.debug("Returned list of users.", user_count=len(users))
        return users

    async def get_user(self, user_id: int) -> dict:
        await self._ensure_exists(user_id)
        user = await self._user_repo.get_user_dict_by_id(user_id)
        if user is None:
            raise UserNotFoundError(f"User '{user_id}' not found")
        return user

    async def delete_user(self, user_id: int) -> None:
        """Delete a user, cascading partitions the user owns first.

        Mirrors the legacy Ray ``delete_user``: every partition where the
        user holds the ``owner`` role is deleted (vectors + relational
        rows, via PartitionService) before the user row is removed.
        """
        await self._ensure_exists(user_id)
        owned = [
            p["partition"]
            for p in await self._membership_repo.list_user_partitions_dict(user_id)
            if p.get("role") == "owner"
        ]
        for partition in owned:
            await self._partition_service.delete_partition(partition)
        await self._user_repo.delete_user(user_id)
        logger.info("Deleted user", user_id=user_id, cascaded_partitions=len(owned))

    async def regenerate_token(self, user_id: int) -> dict:
        await self._ensure_exists(user_id)
        user = await self._user_repo.regenerate_user_token(user_id)
        if user is None:
            raise UserNotFoundError(f"User '{user_id}' not found")
        logger.info("Regenerated user token", user_id=user_id)
        return user

    async def update_user(self, user_id: int, body: UserUpdate) -> dict:
        await self._ensure_exists(user_id)
        updates = body.model_dump(exclude_unset=True)
        self._validate_profile(updates.get("display_name"), updates.get("email"))

        user = await self._user_repo.update_user(user_id, **updates)
        if user is None:
            raise UserNotFoundError(f"User '{user_id}' not found")
        logger.info("Updated user info", user_id=user_id)
        return {
            "id": user.id,
            "display_name": user.display_name,
            "external_user_id": user.external_user_id,
            "email": user.email,
            "is_admin": user.is_admin,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "file_quota": user.file_quota,
            "file_count": user.file_count,
        }


__all__ = ["UserService"]
