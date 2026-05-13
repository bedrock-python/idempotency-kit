"""Idempotency-specific exceptions."""

from typing import Any


class IdempotencyError(Exception):
    """Base exception for idempotency errors."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class IdempotencyKeyCollisionError(IdempotencyError):
    """Raised when trying to save a record with a key that already exists."""

    def __init__(self, operation: str, key: str | list[str]) -> None:
        self.operation = operation
        self.key = key
        key_str = f"'{key}'" if isinstance(key, str) else f"{key}"
        super().__init__(f"Idempotency key collision for operation '{operation}', keys {key_str}")


class IdempotencyRecordExpiredError(IdempotencyError):
    """Raised when record exists but has expired."""

    def __init__(self, operation: str, key: str) -> None:
        self.operation = operation
        self.key = key
        super().__init__(f"Idempotency record expired for operation '{operation}', key '{key}'")


class IdempotencyStorageError(IdempotencyError):
    """Raised when storage backend (e.g. Redis) is unavailable or fails."""

    def __init__(self, message: str, operation: str | None = None, original_error: Exception | None = None) -> None:
        self.operation = operation
        self.original_error = original_error
        super().__init__(message)


class IdempotencyValidationError(IdempotencyError):
    """Raised when record validation fails (e.g. empty key, too long string)."""

    def __init__(self, message: str, errors: list[Any] | None = None) -> None:
        self.errors = errors or []
        super().__init__(message)


class IdempotencyInvalidTTLError(IdempotencyError):
    """Raised when TTL is outside of allowed range."""

    def __init__(self, ttl_seconds: float, min_ttl: float, max_ttl: float) -> None:
        self.ttl_seconds = ttl_seconds
        self.min_ttl = min_ttl
        self.max_ttl = max_ttl
        super().__init__(f"Invalid TTL {ttl_seconds}s. Must be between {min_ttl}s and {max_ttl}s")
