"""Redis-based async idempotency repository."""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from pydantic import ValidationError

try:
    import orjson

    _HAS_ORJSON = True
except ImportError:
    _HAS_ORJSON = False

try:
    from redis.asyncio import Redis as AsyncRedisClient

    _HAS_REDIS = True
except ImportError:
    _HAS_REDIS = False
    AsyncRedisClient = Any  # type: ignore[assignment,misc,valid-type]

from .....core.exceptions import (
    IdempotencyError,
    IdempotencyKeyCollisionError,
    IdempotencyStorageError,
    IdempotencyValidationError,
)
from .....core.models.entities import IdempotencyIdentifiers, IdempotencyRecord
from .....core.protocols.aio import AsyncIdempotencyRepository
from .....core.protocols.metrics import IdempotencyMetricsProtocol, NoOpIdempotencyMetrics

logger = logging.getLogger(__name__)

DEFAULT_KEY_PREFIX = "idempotency:"


class RedisAsyncIdempotencyRepository(AsyncIdempotencyRepository):
    """Redis implementation of idempotency repository.

    Stores records as JSON with automatic TTL expiration.
    """

    def __init__(
        self,
        redis: AsyncRedisClient,
        *,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        metrics: IdempotencyMetricsProtocol | None = None,
    ) -> None:
        """Initialize repository.

        Args:
            redis: Redis client instance
            key_prefix: Prefix for all Redis keys (default: "idempotency:")
            metrics: Metrics collector instance (default: NoOp)
        """
        if not _HAS_REDIS or not _HAS_ORJSON:
            raise ImportError(
                "RedisAsyncIdempotencyRepository requires redis-client-kit and orjson. "
                "Install them with: pip install idempotency-kit[redis-aio]"
            )
        self._redis = redis
        self._key_prefix = key_prefix
        self._metrics = metrics or NoOpIdempotencyMetrics()

    def _make_key(self, operation: str, idempotency_key: str) -> str:
        """Construct Redis key."""
        return f"{self._key_prefix}{operation}:{idempotency_key}"

    def _deserialize_record(
        self,
        data: bytes,
        operation: str,
        idempotency_key: str,
    ) -> IdempotencyRecord | None:
        """Deserialize record from JSON bytes.

        Returns None if record is expired.
        Raises IdempotencyValidationError or IdempotencyError on corruption.
        """
        try:
            record = IdempotencyRecord.model_validate_json(data)

            if record.is_expired:
                return None
        except ValidationError as e:
            self._metrics.record_error(operation, "validation_error")
            logger.exception(
                "Invalid record data in Redis. Record failed validation.",
                extra={"operation": operation, "key": idempotency_key},
            )
            # If JSON is invalid, it's a corruption error
            if any(err.get("type") == "json_invalid" for err in e.errors()):
                raise IdempotencyError(f"Corrupted idempotency record for {operation}:{idempotency_key}") from e

            raise IdempotencyValidationError(
                f"Invalid record data for {operation}:{idempotency_key}",
                errors=e.errors(),
            ) from e
        except Exception as e:
            self._metrics.record_error(operation, "deserialization_error")
            logger.exception(
                "Failed to deserialize idempotency record. Data might be corrupted.",
                extra={"operation": operation, "key": idempotency_key},
            )
            raise IdempotencyError(f"Corrupted idempotency record for {operation}:{idempotency_key}") from e
        else:
            return record

    def _validate_inputs(self, operation: str, idempotency_key: str) -> None:
        """Validate input parameters using domain model."""
        try:
            IdempotencyIdentifiers(operation=operation, idempotency_key=idempotency_key)
        except ValidationError as e:
            raise IdempotencyValidationError(str(e), errors=e.errors()) from e

    async def get(self, operation: str, idempotency_key: str) -> IdempotencyRecord | None:
        """Retrieve record from Redis.

        Args:
            operation: Name of the operation
            idempotency_key: Unique key for the operation

        Returns:
            IdempotencyRecord if found and not expired, None otherwise

        Raises:
            IdempotencyValidationError: If operation or key is invalid
            IdempotencyStorageError: If Redis operation fails
            IdempotencyError: If data is corrupted
        """
        start = time.perf_counter()
        try:
            self._validate_inputs(operation, idempotency_key)
            key = self._make_key(operation, idempotency_key)
            try:
                data = await self._redis.get(key)
            except Exception as e:
                self._metrics.record_error(operation, type(e).__name__)
                logger.exception(
                    "Redis error during get",
                    extra={"operation": operation, "key": idempotency_key},
                )
                raise IdempotencyStorageError(
                    f"Redis storage failure during get for {operation}",
                    operation=operation,
                    original_error=e,
                ) from e

            if not data:
                self._metrics.record_miss(operation)
                return None

            record = self._deserialize_record(data, operation, idempotency_key)
            if record is None:
                self._metrics.record_miss(operation)
                logger.warning(
                    "Found expired record in Redis (TTL mismatch). Deleting it.",
                    extra={"operation": operation, "key": idempotency_key},
                )
                try:
                    await self._redis.delete(key)
                except Exception:
                    logger.warning(
                        "Failed to delete expired record from Redis",
                        extra={"operation": operation, "key": idempotency_key},
                        exc_info=True,
                    )
                return None

            self._metrics.record_hit(operation)
            return record
        finally:
            self._metrics.record_latency(operation, "get", time.perf_counter() - start)

    async def save(
        self,
        record: IdempotencyRecord,
    ) -> None:
        """Save record to Redis with NX (set if not exists).

        Args:
            record: Idempotency record to save

        Raises:
            IdempotencyKeyCollisionError: If key already exists in Redis
            IdempotencyValidationError: If record is invalid or already expired
            IdempotencyStorageError: If Redis operation fails
            IdempotencyError: If serialization fails
        """
        start = time.perf_counter()
        operation = record.operation
        try:
            self._validate_inputs(operation, record.idempotency_key)
            key = self._make_key(operation, record.idempotency_key)

            # Calculate TTL
            ttl_seconds = math.ceil(record.ttl_seconds)

            if ttl_seconds <= 0:
                self._metrics.record_error(operation, "validation_error")
                logger.warning(
                    "Attempted to save already expired record",
                    extra={"operation": operation, "key": record.idempotency_key},
                )
                raise IdempotencyValidationError("Cannot save already expired record")

            # Serialize record
            try:
                data = orjson.dumps(record.model_dump(mode="json")) if _HAS_ORJSON else record.model_dump_json()
            except Exception as e:
                self._metrics.record_error(operation, "serialization_error")
                logger.exception(
                    "Failed to serialize record",
                    extra={"operation": operation, "key": record.idempotency_key},
                )
                raise IdempotencyError("Serialization failed") from e

            # Use SET with NX (only if key doesn't exist) and EX (expiration)
            try:
                was_set = await self._redis.set(key, data, ex=ttl_seconds, nx=True)
            except Exception as e:
                self._metrics.record_error(operation, type(e).__name__)
                logger.exception(
                    "Redis error during save",
                    extra={"operation": operation, "key": record.idempotency_key},
                )
                # In case of Redis error, we cannot guarantee idempotency.
                raise IdempotencyStorageError(
                    "Redis storage failure during save",
                    operation=operation,
                    original_error=e,
                ) from e

            if not was_set:
                self._metrics.record_collision(operation)
                raise IdempotencyKeyCollisionError(operation, record.idempotency_key)

            logger.debug(
                "Saved idempotency record",
                extra={"operation": operation, "key": record.idempotency_key, "ttl_seconds": ttl_seconds},
            )
        finally:
            self._metrics.record_latency(operation, "save", time.perf_counter() - start)

    async def delete(self, operation: str, idempotency_key: str) -> bool:
        """Delete record from Redis.

        Args:
            operation: Name of the operation
            idempotency_key: Unique key for the operation

        Returns:
            True if record was deleted, False if it didn't exist

        Raises:
            IdempotencyValidationError: If operation or key is invalid
            IdempotencyStorageError: If Redis operation fails
        """
        start = time.perf_counter()
        try:
            self._validate_inputs(operation, idempotency_key)
            key = self._make_key(operation, idempotency_key)
            try:
                deleted = await self._redis.delete(key)
            except Exception as e:
                self._metrics.record_error(operation, type(e).__name__)
                logger.exception(
                    "Redis error during delete",
                    extra={"operation": operation, "key": idempotency_key},
                )
                raise IdempotencyStorageError(
                    "Redis storage failure during delete",
                    operation=operation,
                    original_error=e,
                ) from e
            else:
                return bool(deleted > 0)
        finally:
            self._metrics.record_latency(operation, "delete", time.perf_counter() - start)

    async def get_many(self, operation: str, idempotency_keys: list[str]) -> dict[str, IdempotencyRecord]:
        """Retrieve multiple records from Redis using MGET.

        Args:
            operation: Name of the operation
            idempotency_keys: List of unique keys

        Returns:
            Dictionary mapping keys to found and valid IdempotencyRecords

        Raises:
            IdempotencyValidationError: If operation or any key is invalid
            IdempotencyStorageError: If Redis operation fails
            IdempotencyError: If data corruption is found
        """
        start = time.perf_counter()
        try:
            if not idempotency_keys:
                return {}

            for key in idempotency_keys:
                self._validate_inputs(operation, key)

            keys = [self._make_key(operation, key) for key in idempotency_keys]
            try:
                values = await self._redis.mget(keys)
            except Exception as e:
                self._metrics.record_error(operation, type(e).__name__)
                logger.exception(
                    "Redis error during mget",
                    extra={"operation": operation, "keys_count": len(idempotency_keys)},
                )
                raise IdempotencyStorageError(
                    "Redis storage failure during mget",
                    operation=operation,
                    original_error=e,
                ) from e

            results: dict[str, IdempotencyRecord] = {}
            expired_keys: list[str] = []
            hits = 0
            misses = 0

            for key, value in zip(idempotency_keys, values, strict=True):
                if value is None:
                    misses += 1
                    continue

                record = self._deserialize_record(value, operation, key)
                if record is not None:
                    hits += 1
                    results[key] = record
                else:
                    misses += 1
                    expired_keys.append(self._make_key(operation, key))
                    logger.warning(
                        "Found expired record during get_many (TTL mismatch).",
                        extra={"operation": operation, "key": key},
                    )

            # Record bulk metrics
            if hits > 0:
                self._metrics.record_bulk_hit(operation, hits)
            if misses > 0:
                self._metrics.record_bulk_miss(operation, misses)

            # Batch delete expired keys
            if expired_keys:
                try:
                    await self._redis.delete(*expired_keys)
                except Exception:
                    logger.warning(
                        "Failed to batch delete expired records from Redis during get_many",
                        extra={"operation": operation, "expired_count": len(expired_keys)},
                        exc_info=True,
                    )

            return results
        finally:
            self._metrics.record_latency(operation, "get_many", time.perf_counter() - start)

    def _prepare_records_for_batch(
        self, records: list[IdempotencyRecord], batch_operation: str
    ) -> tuple[list[IdempotencyRecord], list[bytes], list[str], list[IdempotencyValidationError], bool]:
        """Validate and prepare records for pipeline."""
        errors: list[str] = []
        validation_errors: list[IdempotencyValidationError] = []
        valid_records: list[IdempotencyRecord] = []
        prepared_data: list[bytes] = []
        other_errors_occurred = False

        for record in records:
            if record.operation != batch_operation:
                err = IdempotencyValidationError("All records in batch must have the same operation")
                self._metrics.record_error(record.operation, "validation_error")
                validation_errors.append(err)
                errors.append(str(err))
                continue

            # Check TTL before try-except to avoid TRY301
            ttl_seconds = math.ceil(record.ttl_seconds)
            if ttl_seconds <= 0:
                err = IdempotencyValidationError(f"Cannot save already expired record: {record.idempotency_key}")
                self._metrics.record_error(record.operation, "validation_error")
                validation_errors.append(err)
                errors.append(str(err))
                continue

            try:
                self._validate_inputs(record.operation, record.idempotency_key)
                data = (
                    orjson.dumps(record.model_dump(mode="json"))
                    if _HAS_ORJSON
                    else record.model_dump_json().encode("utf-8")
                )
                valid_records.append(record)
                prepared_data.append(data)
            except IdempotencyValidationError as e:
                self._metrics.record_error(record.operation, "validation_error")
                validation_errors.append(e)
                errors.append(str(e))
            except Exception as e:
                self._metrics.record_error(record.operation, type(e).__name__)
                logger.exception(
                    "Failed to process record in save_many",
                    extra={"operation": record.operation, "key": record.idempotency_key},
                )
                errors.append(f"{record.operation}:{record.idempotency_key}: {e}")
                other_errors_occurred = True

        return valid_records, prepared_data, errors, validation_errors, other_errors_occurred

    async def _execute_batch_save(
        self, valid_records: list[IdempotencyRecord], prepared_data: list[bytes]
    ) -> list[Any]:
        """Execute pipeline for multiple records."""
        async with self._redis.pipeline(transaction=False) as pipe:
            for record, data in zip(valid_records, prepared_data, strict=True):
                key = self._make_key(record.operation, record.idempotency_key)
                ttl_seconds = math.ceil(record.ttl_seconds)
                pipe.set(key, data, ex=ttl_seconds, nx=True)
            results: list[Any] = await pipe.execute()
            return results

    def _process_batch_results(
        self, valid_records: list[IdempotencyRecord], results: list[Any]
    ) -> tuple[bool, list[str], list[str]]:
        """Process results from pipeline execution."""
        collision_found = False
        colliding_keys: list[str] = []
        saved_keys: list[str] = []

        for record, was_set in zip(valid_records, results, strict=True):
            if not was_set:
                self._metrics.record_collision(record.operation)
                collision_found = True
                colliding_keys.append(record.idempotency_key)
                logger.warning(
                    "Idempotency key collision in save_many",
                    extra={"operation": record.operation, "key": record.idempotency_key},
                )
            else:
                saved_keys.append(self._make_key(record.operation, record.idempotency_key))
                logger.debug(
                    "Saved idempotency record in batch",
                    extra={
                        "operation": record.operation,
                        "key": record.idempotency_key,
                        "ttl_seconds": math.ceil(record.ttl_seconds),
                    },
                )
        return collision_found, colliding_keys, saved_keys

    async def _handle_batch_rollback(self, batch_operation: str, saved_keys: list[str]) -> None:
        """Rollback successful writes in case of error."""
        try:
            await self._redis.delete(*saved_keys)
            logger.info(
                "Rolled back successful writes in save_many due to error",
                extra={"operation": batch_operation, "count": len(saved_keys)},
            )
        except Exception:
            logger.exception(
                "Failed to rollback saved keys in save_many",
                extra={"operation": batch_operation, "count": len(saved_keys)},
            )

    def _raise_batch_errors(
        self,
        errors: list[str],
        validation_errors: list[IdempotencyValidationError],
        other_errors_occurred: bool,
        collision_found: bool = False,
        colliding_keys: list[str] | None = None,
        batch_operation: str | None = None,
    ) -> None:
        """Analyze errors and raise appropriate exception."""
        # If there was a collision, it takes priority over other errors
        if collision_found and batch_operation and colliding_keys:
            raise IdempotencyKeyCollisionError(batch_operation, colliding_keys)

        # If only validation errors occurred, raise a specific validation error
        if validation_errors and not other_errors_occurred:
            if len(validation_errors) == 1:
                raise validation_errors[0]
            raise IdempotencyValidationError(f"Bulk validation failed: {'; '.join(errors)}")

        # Otherwise raise generic IdempotencyError with combined message
        if errors:
            if other_errors_occurred:
                raise IdempotencyError(f"Bulk save completed with issues. Errors: {'; '.join(errors)}")
            if validation_errors:
                raise IdempotencyValidationError(f"Bulk validation failed: {'; '.join(errors)}")

            raise IdempotencyError(f"Bulk save failed: {'; '.join(errors)}")

    async def save_many(self, records: list[IdempotencyRecord], *, rollback_on_error: bool = False) -> None:
        """Save multiple records to Redis using pipeline with NX.

        Note: This operation is NOT atomic by default. If rollback_on_error is True,
        successfully written records will be deleted if any error occurs.

        Args:
            records: List of records to save
            rollback_on_error: Whether to delete successfully saved records on error

        Raises:
            IdempotencyKeyCollisionError: If any of the keys already exist in Redis
            IdempotencyValidationError: If validation fails for any record
            IdempotencyStorageError: If Redis operation fails
            IdempotencyError: If serialization or other internal error occurs
        """
        if not records:
            return

        start = time.perf_counter()
        batch_operation = records[0].operation

        try:
            # 1. Prepare records for batch
            valid_records, prepared_data, errors, validation_errors, other_errors_occurred = (
                self._prepare_records_for_batch(records, batch_operation)
            )

            if not valid_records:
                self._raise_batch_errors(errors, validation_errors, other_errors_occurred)
                return

            # 2. Execute pipeline and check results
            results = await self._execute_batch_save(valid_records, prepared_data)

            # 3. Results correspond to pipe.set calls
            collision_found, colliding_keys, saved_keys = self._process_batch_results(valid_records, results)

            if errors or collision_found:
                # 4. Handle rollback if requested
                if rollback_on_error and saved_keys:
                    await self._handle_batch_rollback(batch_operation, saved_keys)

                self._raise_batch_errors(
                    errors,
                    validation_errors,
                    other_errors_occurred,
                    collision_found,
                    colliding_keys,
                    batch_operation,
                )

        except (IdempotencyError, IdempotencyKeyCollisionError):
            # Re-raise known idempotency errors
            raise
        except Exception as e:
            self._metrics.record_error(batch_operation, type(e).__name__)
            logger.exception(
                "Redis error during save_many",
                extra={"records_count": len(records)},
            )
            raise IdempotencyStorageError("Bulk save failure", operation=batch_operation, original_error=e) from e
        finally:
            self._metrics.record_latency(batch_operation, "save_many", time.perf_counter() - start)

    async def delete_many(self, operation: str, idempotency_keys: list[str]) -> int:
        """Delete multiple records from Redis.

        Args:
            operation: Name of the operation
            idempotency_keys: List of unique keys

        Returns:
            Number of records actually deleted

        Raises:
            IdempotencyValidationError: If operation or any key is invalid
            IdempotencyStorageError: If Redis operation fails
        """
        start = time.perf_counter()
        try:
            if not idempotency_keys:
                return 0

            for key in idempotency_keys:
                self._validate_inputs(operation, key)

            keys = [self._make_key(operation, key) for key in idempotency_keys]
            try:
                deleted_count = await self._redis.delete(*keys)
            except Exception as e:
                self._metrics.record_error(operation, type(e).__name__)
                logger.exception(
                    "Redis error during delete_many",
                    extra={"operation": operation, "keys_count": len(idempotency_keys)},
                )
                raise IdempotencyStorageError(
                    "Redis storage failure during delete_many",
                    operation=operation,
                    original_error=e,
                ) from e
            else:
                return int(deleted_count)
        finally:
            self._metrics.record_latency(operation, "delete_many", time.perf_counter() - start)
