"""Idempotency record entity."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

from ..constants import MAX_KEY_LENGTH, MAX_OPERATION_LENGTH


class IdempotencyIdentifiers(BaseModel):
    """Validation model for idempotency identifiers."""

    operation: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_OPERATION_LENGTH, strip_whitespace=True),
    ] = Field(description="Operation name (e.g. 'user.create')")
    idempotency_key: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_KEY_LENGTH, strip_whitespace=True),
    ] = Field(description="Unique key for this operation instance")

    @field_validator("operation", "idempotency_key")
    @classmethod
    def validate_no_colon(cls, v: str) -> str:
        """Validate that the string does not contain ':'."""
        if ":" in v:
            raise ValueError("cannot contain ':'")
        return v


class IdempotencyRecord(IdempotencyIdentifiers):
    """Domain entity representing a cached idempotency result.

    Stores the result of an operation so that repeated calls with the same key
    return the cached result without re-executing business logic.
    """

    model_config = ConfigDict(frozen=True)

    # Result
    result: Mapping[str, JsonValue] = Field(description="Cached operation result (JSON-serializable)")

    # Timing
    created_at: datetime = Field(description="When this record was created")
    expires_at: datetime = Field(description="When this record should expire from cache")

    @classmethod
    def create(
        cls,
        operation: str,
        idempotency_key: str,
        result: Mapping[str, JsonValue],
        ttl_seconds: float,
    ) -> Self:
        """Create a new record with calculated expiration."""
        now = datetime.now(UTC)
        return cls(
            operation=operation,
            idempotency_key=idempotency_key,
            result=result,
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    @property
    def is_expired(self) -> bool:
        """Check if the record has expired."""
        return datetime.now(UTC) >= self.expires_at

    @property
    def ttl_seconds(self) -> float:
        """Get remaining TTL in seconds."""
        if self.is_expired:
            return 0.0
        delta = self.expires_at - datetime.now(UTC)
        return max(0.0, delta.total_seconds())
