from pydantic import BaseModel, ConfigDict, Field, field_validator


class UserBase(BaseModel):
    display_name: str | None = None
    external_user_id: str | None = None
    email: str | None = Field(default=None, examples=[None])
    is_admin: bool = False
    file_quota: int | None = Field(default=10)

    @field_validator("external_user_id", mode="before")
    @classmethod
    def _empty_external_id_to_none(cls, v):
        # Coerce "" / whitespace to NULL so it can't collide on the unique index
        # (Postgres allows many NULLs but only one empty string).
        if isinstance(v, str) and not v.strip():
            return None
        return v


class UserCreate(UserBase):
    # Reject unknown fields so callers cannot smuggle extra column names.
    model_config = ConfigDict(extra="ignore")


class UserUpdate(UserBase):
    # Reject unknown fields; update_user additionally whitelists writable columns.
    model_config = ConfigDict(extra="ignore")


class UserPublic(UserBase):
    id: int
    created_at: str | None
    file_quota: int | None = None
    file_count: int | None = None
