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
  - `expires_at` (datetime): When the record will expire. On a pending record this is the in-flight lease.
  - `status` (`"pending" | "completed"`, default `"completed"`): `"pending"` while the action runs under an in-flight reservation, `"completed"` once the result is stored. A record written before the field existed reads as completed.
- **Methods**:
  - `create(operation: str, idempotency_key: str, result: JsonValue, ttl_seconds: float) -> IdempotencyRecord`: Class method to create a new record.
  - `pending(operation: str, idempotency_key: str, lease_seconds: float) -> IdempotencyRecord`: Class method to create the in-flight reservation; `result` is `null`.
  - `is_pending`: Property returning `True` for an in-flight reservation.
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
  - `create_pending_record(operation, idempotency_key, *, lease_seconds)`: Creates the in-flight reservation. The lease is not held to the TTL bounds; it must be at least a second.
  - `validate_record(record)`: Validates that a record is still usable, raising `IdempotencyRecordExpiredError` if not. The coordinator calls it on every record it reads.

### AsyncIdempotencyCoordinator
The flow: reserve the key, run the action, store the result; replay a stored result; hand a second concurrent caller the first one's result.

- **Constructor**:
  - `repository` (AsyncIdempotencyRepository): Storage for records.
  - `domain_service` (IdempotencyDomainService): Record factory and TTL bounds.
  - `operation_ttls` (dict[str, int], optional): Per-operation TTLs in seconds; win over the decorator's `ttl_seconds`.
  - `metrics` (IdempotencyMetricsProtocol, optional): Metrics collector.
  - `enabled` (bool, default: True): `False` runs the action and nothing else.
  - `in_flight` (`"wait" | "raise" | "run"`, default: `"wait"`): What a second caller gets while the first one's action is still running under the same key — the first caller's result once it lands, `IdempotencyInProgressError` at once, or a run of its own with the winner's result adopted on collision.
  - `in_flight_lease_seconds` (int, default: 30): How long a reservation is held before it counts as abandoned, and the longest a waiting caller waits. Must be at least 1 and longer than the action can take.
  - *Note*: The constructor raises `TypeError` when the repository has no `replace` and `in_flight` is not `"run"`.
- **Methods**:
  - `coordinate(operation, idempotency_key, ttl_seconds, adapter, action, /, *args, **kwargs)`: Runs the flow and returns the action's result type. Never raises for storage or decode trouble; raises `IdempotencyInProgressError` when the key is in flight and `in_flight` says so.

### AsyncIdempotencyRepository (Protocol)
Interface for idempotency storage.

- **Methods**:
  - `get(operation, idempotency_key)`: Returns `IdempotencyRecord` or `None`.
  - `save(record)`: Saves a record with NX (not exists) guarantee.
  - `replace(record)`: Writes a record whether or not the key is there; how a reservation becomes a result.
  - `delete(operation, idempotency_key)`: Deletes a record. Returns `True` if deleted.
  - `get_many(operation, idempotency_keys)`: Returns `dict[str, IdempotencyRecord]`.
  - `save_many(records, *, rollback_on_error=False)`: Saves multiple records. NOT atomic unless `rollback_on_error=True`.
  - `delete_many(operation, idempotency_keys)`: Deletes multiple records. Returns count.

### IdempotencyMetricsProtocol
Interface for metrics collection.

- **Methods**:
  - `record_hit(operation)`
  - `record_miss(operation)`
  - `record_collision(operation)`: Two callers on one key at the same time.
  - `record_error(operation, error_type)`
  - `record_latency(operation, method, duration_seconds)`
  - `record_bulk_hit(operation, count)`: For bulk operations.
  - `record_bulk_miss(operation, count)`: For bulk operations.
- **Who records what**: the coordinator records hit, miss, collision and the latency of `get`, `reserve` and `save`; the repository
  records errors, the bulk hit and miss counts of `get_many`, and the latency of `delete` and `get_many`. One collector
  handed to both — which is what the Dishka providers do — therefore counts each operation once.

## Infrastructure Layer

### RedisAsyncIdempotencyRepository
Redis implementation of the repository protocol.

- **Constructor**:
  - `redis` (`redis.asyncio.Redis`): Any async Redis client, including a subclass such as an instrumented or fake one.
  - `key_prefix` (str, default: "idempotency:"): Prefix for all Redis keys. (**keyword-only**)
  - `metrics` (IdempotencyMetricsProtocol, optional): Metrics collector. (**keyword-only**)
- **Storage**: `SET key value EX ttl NX` for `save`, the same without `NX` for `replace`, `GET` for `get`.

## Exceptions

- **`IdempotencyError(message)`**: Base library exception.
- **`IdempotencyKeyCollisionError(operation, key)`**: Raised when a key already exists.
  - `key` can be a single `str` or a `list[str]` for bulk operations.
- **`IdempotencyRecordExpiredError(operation, key)`**: Raised when record exists but is expired.
- **`IdempotencyInProgressError(operation, key)`**: Raised by `coordinate()` and the decorator when another call with the same key is still running its action.
- **`IdempotencyStorageError(message, operation, original_error)`**: Raised on storage failure.
- **`IdempotencyValidationError(message, errors=None)`**: Raised for invalid input (e.g. empty key, too long string).
  - `errors` (list, optional): Detailed Pydantic validation errors.
- **`IdempotencyInvalidTTLError(ttl_seconds, min_ttl, max_ttl)`**: Raised for invalid TTL.
