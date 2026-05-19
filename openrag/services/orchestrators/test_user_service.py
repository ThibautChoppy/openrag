"""Unit tests for :class:`UserService` (Phase 8A.2)."""

from __future__ import annotations

import pytest
from core.models.user import User
from core.utils.exceptions import UserNotFoundError, ValidationError
from models.user import UserCreate, UserUpdate
from services.orchestrators.user_service import UserService


class FakeUserRepo:
    def __init__(self, existing: set[int] | None = None):
        self._existing = existing if existing is not None else set()
        self.created: list[dict] = []
        self.deleted: list[int] = []
        self.regenerated: list[int] = []
        self.regen_results: dict[int, dict] = {}
        self.updated: list[tuple[int, dict]] = []
        self._users: dict[int, User] = {}

    async def user_exists(self, user_id: int) -> bool:
        return user_id in self._existing

    async def create_legacy_user(self, *, display_name, external_user_id, email, is_admin, file_quota):
        rec = {
            "id": 42,
            "display_name": display_name,
            "external_user_id": external_user_id,
            "email": email,
            "token": "or-deadbeef",
            "is_admin": is_admin,
            "file_quota": file_quota,
            "file_count": 0,
        }
        self.created.append(rec)
        return rec

    async def list_users_dict(self):
        return [{"id": 1, "display_name": "Admin"}]

    async def get_user_dict_by_id(self, user_id: int):
        return {"id": user_id, "display_name": "U"}

    async def delete_user(self, user_id: int) -> bool:
        self.deleted.append(user_id)
        return True

    async def regenerate_user_token(self, user_id: int):
        self.regenerated.append(user_id)
        return self.regen_results.get(user_id)

    async def update_user(self, user_id: int, **fields):
        self.updated.append((user_id, fields))
        return self._users.get(user_id)


def _svc(repo: FakeUserRepo, *, default_quota: int = 10) -> UserService:
    return UserService(user_repo=repo, auth_service=object(), default_file_quota=default_quota)


# --------------------------------------------------------------------------- #
# create_user
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_create_user_passes_through_explicit_quota():
    repo = FakeUserRepo()
    svc = _svc(repo, default_quota=10)
    out = await svc.create_user(UserCreate(display_name="Bob", file_quota=3))
    assert out["token"] == "or-deadbeef"
    assert repo.created[0]["file_quota"] == 3


@pytest.mark.asyncio
async def test_create_user_applies_default_quota_when_none_and_default_positive():
    repo = FakeUserRepo()
    svc = _svc(repo, default_quota=7)
    await svc.create_user(UserCreate(display_name="Bob", file_quota=None))
    assert repo.created[0]["file_quota"] == 7


@pytest.mark.asyncio
async def test_create_user_no_default_when_default_not_positive():
    repo = FakeUserRepo()
    svc = _svc(repo, default_quota=-1)
    await svc.create_user(UserCreate(display_name="Bob", file_quota=None))
    assert repo.created[0]["file_quota"] is None


@pytest.mark.asyncio
async def test_create_user_rejects_bad_email():
    svc = _svc(FakeUserRepo())
    with pytest.raises(ValidationError) as ei:
        await svc.create_user(UserCreate(display_name="Bob", email="not-an-email"))
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_create_user_rejects_overlong_display_name():
    svc = _svc(FakeUserRepo())
    with pytest.raises(ValidationError):
        await svc.create_user(UserCreate(display_name="x" * 256))


# --------------------------------------------------------------------------- #
# read / delete / regenerate / update
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_list_users_delegates():
    assert await _svc(FakeUserRepo()).list_users() == [{"id": 1, "display_name": "Admin"}]


@pytest.mark.asyncio
async def test_get_user_missing_raises_404():
    svc = _svc(FakeUserRepo(existing=set()))
    with pytest.raises(UserNotFoundError) as ei:
        await svc.get_user(9)
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_user_existing_returns_dict():
    svc = _svc(FakeUserRepo(existing={9}))
    assert await svc.get_user(9) == {"id": 9, "display_name": "U"}


@pytest.mark.asyncio
async def test_delete_user_missing_raises_and_no_repo_call():
    repo = FakeUserRepo(existing=set())
    with pytest.raises(UserNotFoundError):
        await _svc(repo).delete_user(5)
    assert repo.deleted == []


@pytest.mark.asyncio
async def test_delete_user_existing_deletes_without_cascade():
    repo = FakeUserRepo(existing={5})
    await _svc(repo).delete_user(5)
    assert repo.deleted == [5]  # no partition cascade in 8A.2 (deferred to 8B)


@pytest.mark.asyncio
async def test_regenerate_token_missing_user_404():
    repo = FakeUserRepo(existing=set())
    with pytest.raises(UserNotFoundError):
        await _svc(repo).regenerate_token(3)


@pytest.mark.asyncio
async def test_regenerate_token_repo_returns_none_404():
    repo = FakeUserRepo(existing={3})  # exists but repo regen returns None
    with pytest.raises(UserNotFoundError):
        await _svc(repo).regenerate_token(3)


@pytest.mark.asyncio
async def test_regenerate_token_success():
    repo = FakeUserRepo(existing={3})
    repo.regen_results[3] = {"id": 3, "token": "or-new"}
    out = await _svc(repo).regenerate_token(3)
    assert out == {"id": 3, "token": "or-new"}
    assert repo.regenerated == [3]


@pytest.mark.asyncio
async def test_update_user_missing_404():
    repo = FakeUserRepo(existing=set())
    with pytest.raises(UserNotFoundError):
        await _svc(repo).update_user(2, UserUpdate(display_name="X"))


@pytest.mark.asyncio
async def test_update_user_returns_legacy_dict_shape():
    repo = FakeUserRepo(existing={2})
    repo._users[2] = User(id=2, display_name="New", email="a@b.io", is_admin=True, file_quota=5, file_count=4)
    out = await _svc(repo).update_user(2, UserUpdate(display_name="New"))
    assert set(out) == {
        "id",
        "display_name",
        "external_user_id",
        "email",
        "is_admin",
        "created_at",
        "file_quota",
        "file_count",
    }
    assert out["id"] == 2 and out["display_name"] == "New" and out["file_count"] == 4
    assert isinstance(out["created_at"], str)  # iso-formatted


@pytest.mark.asyncio
async def test_update_user_validates_email():
    repo = FakeUserRepo(existing={2})
    with pytest.raises(ValidationError):
        await _svc(repo).update_user(2, UserUpdate(email="bogus"))
