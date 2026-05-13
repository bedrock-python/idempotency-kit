"""Async idempotency repository protocol."""

from typing import Protocol, runtime_checkable

from ...models.entities import IdempotencyRecord


@runtime_checkable
class AsyncIdempotencyRepository(Protocol):
    """Base protocol for async idempotency repository.

    Implementations must provide storage and retrieval of idempotency records.
    """

    async def get(self, operation: str, idempotency_key: str) -> IdempotencyRecord | None:
        """Retrieve an idempotency record.

        Args:
            operation: Operation name (e.g., 'user.create')
            idempotency_key: Unique key for this operation instance

        Returns:
            IdempotencyRecord if found and not expired, None otherwise

        Raises:
            IdempotencyValidationError: If inputs are invalid
            IdempotencyError: If stored data is corrupted
            IdempotencyStorageError: If storage backend fails
        """
        ...

    async def save(self, record: IdempotencyRecord) -> None:
        """Save an idempotency record.

        Args:
            record: Record to save

        Raises:
            IdempotencyKeyCollisionError: If a record with the same key already exists
            IdempotencyValidationError: If record fails validation
            IdempotencyStorageError: If storage operation fails
            IdempotencyError: If internal error (e.g. serialization) occurs
        """
        ...

    async def delete(self, operation: str, idempotency_key: str) -> bool:
        """Delete an idempotency record.

        Args:
            operation: Operation name
            idempotency_key: Unique key

        Returns:
            True if record was deleted, False if not found

        Raises:
            IdempotencyValidationError: If inputs are invalid
            IdempotencyStorageError: If storage backend fails
        """
        ...

    async def get_many(self, operation: str, idempotency_keys: list[str]) -> dict[str, IdempotencyRecord]:
        """Retrieve multiple idempotency records.

        Args:
            operation: Operation name
            idempotency_keys: List of unique keys

        Returns:
            Dictionary mapping key to IdempotencyRecord (only for found records)

        Raises:
            IdempotencyValidationError: If inputs are invalid
            IdempotencyError: If stored data is corrupted
            IdempotencyStorageError: If storage backend fails
        """
        ...

    async def save_many(self, records: list[IdempotencyRecord], *, rollback_on_error: bool = False) -> None:
        """Save multiple idempotency records.

        Note: Operation is not atomic by default. If rollback_on_error is True,
        successfully written records will be deleted if any error occurs.

        Args:
            records: List of records to save
            rollback_on_error: Whether to delete successfully saved records on error

        Raises:
            IdempotencyKeyCollisionError: If any key already exists
            IdempotencyValidationError: If validation fails
            IdempotencyStorageError: If storage operation fails
            IdempotencyError: If internal error occurs
        """
        ...

    async def delete_many(self, operation: str, idempotency_keys: list[str]) -> int:
        """Delete multiple idempotency records.

        Args:
            operation: Operation name
            idempotency_keys: List of unique keys

        Returns:
            Number of deleted records

        Raises:
            IdempotencyValidationError: If inputs are invalid
            IdempotencyStorageError: If storage backend fails
        """
        ...
