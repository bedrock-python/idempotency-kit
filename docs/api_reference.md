# API Reference

This document provides a detailed reference for all public classes and methods in `idempotency-kit`.

## Core Layer

### IdempotencyIdentifiers
Validation model for idempotency identifiers.

- **Attributes**:
  - `operation` (str): Name of the operation. Max 100 chars. Cannot contain ':'.
  - `idempotency_key` (str): Unique key for this instance. Max 255 chars. Cannot contain ':'.

### IdempotencyRecord
A frozen Pydantic model representing an idempotency result. Inherits from `IdempotencyIdentifiers`.

- **Attributes**:
  - `operation` (str): Name of the operation.
  - `idempotency_key` (str): Unique key for this instance.
  - `result` (JsonValue): The cached result — any JSON value (mapping, list, scalar); `null` for a void result.
  - `created_at` (datetime): When the record was created.
  - `expires_at` (datetime): When the record will expire.
- **Methods**:
  - `create(operation: str, idempotency_key: str, result: JsonValue, ttl_seconds: float) -> IdempotencyRecord`: Class method to create a new record.
  - `is_expired`: Property returning `True` if current time is after `expires_at`.
  - `ttl_seconds`: Property returning remaining TTL in seconds.

### IdempotencyDomainService
Service for creating and validating records.

- **Constructor** (Parameters are **keyword-only**):
  - `default_ttl_minutes` (int, default: 60): Default TTL in minutes. Must be >= 1.
  - `min_ttl_seconds` (int, default: 60): Minimum allowed TTL. Must be >= 1.
  - `max_ttl_seconds` (int, default: 2592000): Maximum allowed TTL (30 days). Must be >= `min_ttl_seconds`.
  - *Note*: These three defaults live in `idempotency_kit.core.constants` and are also the field defaults of `BaseIdempotencySettings`.
  - *Note*: Constructor validates that `default_ttl_minutes` (converted to seconds) is within the `[min_ttl_seconds, max_ttl_seconds]` range.
- **Methods**:
  - `create_record(operation, idempotency_key, result, *, ttl_minutes=None)`: Creates a new `IdempotencyRecord` with validation and TTL management.
  - `validate_record(record)`: Validates that a record is still usable, raising `IdempotencyRecordExpiredError` if not. The coordinator calls it on every record it reads.

### AsyncIdempotencyRepository (Protocol)
Interface for idempotency storage.

- **Methods**:
  - `get(operation, idempotency_key)`: Returns `IdempotencyRecord` or `None`.
  - `save(record)`: Saves a record with NX (not exists) guarantee.
  - `delete(operation, idempotency_key)`: Deletes a record. Returns `True` if deleted.
  - `get_many(operation, idempotency_keys)`: Returns `dict[str, IdempotencyRecord]`.
  - `save_many(records, *, rollback_on_error=False)`: Saves multiple records. NOT atomic unless `rollback_on_error=True`.
  - `delete_many(operation, idempotency_keys)`: Deletes multiple records. Returns count.

### IdempotencyMetricsProtocol
Interface for metrics collection.

- **Methods**:
  - `record_hit(operation)`
  - `record_miss(operation)`
  - `record_collision(operation)`
  - `record_error(operation, error_type)`
  - `record_latency(operation, method, duration_seconds)`
  - `record_bulk_hit(operation, count)`: For bulk operations.
  - `record_bulk_miss(operation, count)`: For bulk operations.
- **Who records what**: the coordinator records hit, miss, collision and the latency of `get` and `save`; the repository
  records errors, the bulk hit and miss counts of `get_many`, and the latency of `delete` and `get_many`. One collector
  handed to both — which is what the Dishka providers do — therefore counts each operation once.

## Infrastructure Layer

### RedisAsyncIdempotencyRepository
Redis implementation of the repository protocol.

- **Constructor**:
  - `redis` (`redis.asyncio.Redis`): Any async Redis client, including a subclass such as an instrumented or fake one.
  - `key_prefix` (str, default: "idempotency:"): Prefix for all Redis keys. (**keyword-only**)
  - `metrics` (IdempotencyMetricsProtocol, optional): Metrics collector. (**keyword-only**)

## Exceptions

- **`IdempotencyError(message)`**: Base library exception.
- **`IdempotencyKeyCollisionError(operation, key)`**: Raised when a key already exists.
  - `key` can be a single `str` or a `list[str]` for bulk operations.
- **`IdempotencyRecordExpiredError(operation, key)`**: Raised when record exists but is expired.
- **`IdempotencyStorageError(message, operation, original_error)`**: Raised on storage failure.
- **`IdempotencyValidationError(message, errors=None)`**: Raised for invalid input (e.g. empty key, too long string).
  - `errors` (list, optional): Detailed Pydantic validation errors.
- **`IdempotencyInvalidTTLError(ttl_seconds, min_ttl, max_ttl)`**: Raised for invalid TTL.
