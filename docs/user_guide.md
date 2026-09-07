# User Guide

This guide provides detailed usage examples and best practices for `idempotency-kit`.

## Basic Usage

### 1. Initialize Dependencies

```python
from redis.asyncio import Redis
from idempotency_kit import IdempotencyDomainService
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

# Initialize Redis client
redis = Redis.from_url("redis://localhost:6379")

# Initialize library
idempotency_repo = RedisAsyncIdempotencyRepository(redis)
idempotency_service = IdempotencyDomainService()
```

**Using redis-client-kit (instrumented client)**:

```python
from redis_client_kit import create_async_redis_client
from idempotency_kit import IdempotencyDomainService
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

# Redis with metrics and tracing
redis = create_async_redis_client(settings.redis)

# Works with both official and instrumented clients
idempotency_repo = RedisAsyncIdempotencyRepository(redis)
idempotency_service = IdempotencyDomainService()
```

### 2. Manual Check in Use Case
The most common way to use the library is to manually check the cache in your Use Case class.

```python
from idempotency_kit import IdempotencyError, IdempotencyKeyCollisionError

class CreateOrderUseCase:
    def __init__(self, repo, idempotency_repo, idempotency_service):
        self._repo = repo
        self._idempotency_repo = idempotency_repo
        self._idempotency_service = idempotency_service

    async def execute(self, dto, idempotency_key: str | None = None):
        operation = "order.create"

        # 1. Check Cache
        if idempotency_key:
            try:
                cached = await self._idempotency_repo.get(operation, idempotency_key)
                if cached:
                    return OrderDTO(**cached.result)
            except IdempotencyError:
                # Log and proceed if storage is unavailable (graceful degradation)
                pass

        # 2. Execute Logic
        order = await self._repo.save(dto)
        result = OrderDTO.from_entity(order)

        # 3. Save to Cache
        if idempotency_key:
            record = self._idempotency_service.create_record(
                operation=operation,
                idempotency_key=idempotency_key,
                result=result.model_dump(mode="json"),
                ttl_minutes=60
            )
            try:
                await self._idempotency_repo.save(record)
            except IdempotencyKeyCollisionError:
                # Concurrent request finished first - get its result
                cached = await self._idempotency_repo.get(operation, idempotency_key)
                if cached:
                    return OrderDTO(**cached.result)
            except IdempotencyError:
                # Storage failure - proceed with returning new result
                pass

        return result
```

This hand-rolled flow reads, executes and then writes with `SET NX`, so two requests with the
same key that overlap both execute; only their responses are deduplicated.
`AsyncIdempotencyCoordinator` reserves the key before executing, which is why the decorator
and the coordinator are the recommended shape — see [In-flight requests](#in-flight-requests).

## In-flight requests

The case an idempotency key exists for is a client that timed out and retried while the
original request is still being processed. The coordinator handles it by reserving the key
before the action runs: a pending record goes in under `SET NX` with a lease as its TTL, the
action runs, and the completed record is written over the reservation. What a second caller
gets while the key is reserved is the coordinator's `in_flight` mode:

| `in_flight` | The second caller... | Use it when |
|---|---|---|
| `"wait"` (default) | polls the key every 50 ms and returns the first caller's result when it lands | the client wants an answer, not an error |
| `"raise"` | raises `IdempotencyInProgressError` at once | the client can retry later; map it to HTTP 409 |
| `"run"` | runs the action too and adopts the first caller's result when its own write collides | the action is genuinely safe to repeat and you want the pre-reservation flow |

```python
coordinator = AsyncIdempotencyCoordinator(
    repo,
    IdempotencyDomainService(),
    in_flight="raise",
    in_flight_lease_seconds=60,
)
```

```python
@app.post("/charges")
async def charge(dto: ChargeDTO, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    try:
        return await use_case.execute(dto, idempotency_key=idempotency_key)
    except IdempotencyInProgressError:
        raise HTTPException(409, detail="a request with this Idempotency-Key is still being processed")
```

**The lease.** `in_flight_lease_seconds` (default 30) is how long the reservation is held.
A pending record past its lease counts as absent, so a worker that crashed mid-action cannot
wedge the key; it is also how long a waiting caller waits before it gives up with
`IdempotencyInProgressError`. The flip side is that the lease has to be longer than the action
can ever take, timeouts and internal retries included: a reservation that expires while the
action is still running lets the next caller run it again.

**Failures.** An action that raises, or is cancelled, deletes its reservation before the
exception propagates, so the retry runs the action again — failures are not cached, and
neither is the reservation of a failed action. The same happens when the result cannot be
stored (an out-of-range TTL, a result the adapter cannot encode): the caller still gets the
result, and the key is freed rather than holding retries for a record that never comes.

**Storage trouble.** A reservation that cannot be written is logged and counted as
`storage_reserve_error`, and the action runs unreserved — the same trade of exactly-once for
availability the coordinator makes everywhere else. `IdempotencyInProgressError` is the one
exception `coordinate()` and the decorator do raise: it is about the caller's request, not
about storage.

**Metrics.** The first caller is a miss; the second caller records a collision when it finds
the reservation, then a hit when it gets the record (`"wait"`) or nothing more (`"raise"`,
the exception is the signal). The reservation is timed under `method="reserve"`.

### Upgrading

Records written before this change carry no `status` and read as completed, so nothing has
to be migrated. Two things to know before turning the default on across a fleet:

- **A custom repository needs `replace`** — `save` without `NX`. The coordinator raises
  `TypeError` at construction when the repository lacks it and `in_flight` is not `"run"`.
- **During a rolling upgrade**, an instance still on a version without `status` reads a
  pending record as a completed one with a `null` result. With `PydanticResultAdapter` that
  is a decode failure and the action runs, which is the old behaviour; with
  `JsonResultAdapter` or `VoidResultAdapter` it replays as `None`. Roll out with
  `in_flight="run"` and switch to `"wait"` once every instance is on the new version, or
  accept that window.

## Key reuse and fingerprints

The key is the identity, and the request body is not part of it: two calls with the same
`operation` and `idempotency_key` and different payloads replay the first result. For a
well-behaved client that never happens. It happens with a client that reuses keys by mistake
— a key derived from the order id, then a second, different operation on the same order — and
without a fingerprint the library answers such a request with a result for a different
request, and nothing says so.

Name the parameters that identify the request and the decorator fingerprints them:

```python
from idempotency_kit import IdempotencyKeyReuseError, PydanticResultAdapter, async_idempotent

class ChargeCard:
    @async_idempotent(
        operation="payment.charge",
        adapter=PydanticResultAdapter(ChargeDTO),
        fingerprint_params=("dto",),
    )
    async def execute(self, dto: ChargeRequest, *, idempotency_key: str | None = None) -> ChargeDTO:
        return await self._psp.charge(dto)
```

The named values are bound to the call with defaults applied, turned into their JSON form —
Pydantic models, dataclasses, UUIDs, datetimes and Decimals included — serialised with sorted
keys, and hashed with SHA-256. The fingerprint is stored with the record. The same key coming
back with a different fingerprint raises `IdempotencyKeyReuseError` before the action runs,
whether the record is completed or still pending under an in-flight reservation, so a
retry with another payload is refused at once rather than handed someone else's result after
waiting. The error carries `operation`, `key`, `stored_fingerprint` and `fingerprint`; map
it to HTTP 422, which is what Stripe does:

```python
@app.post("/charges")
async def charge(dto: ChargeRequest, idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    try:
        return await use_case.execute(dto, idempotency_key=idempotency_key)
    except IdempotencyKeyReuseError:
        raise HTTPException(422, detail="this Idempotency-Key was already used for a different request")
```

Without the decorator, compute the fingerprint with `fingerprint_of` — the same function the
decorator uses, so both paths agree — and pass it to `coordinate()` as its one keyword:

```python
from idempotency_kit import fingerprint_of

result = await coordinator.coordinate(
    "payment.charge",
    idempotency_key,
    3600,
    PydanticResultAdapter(ChargeDTO),
    self._psp.charge,
    dto,
    idempotency_fingerprint=fingerprint_of(dto=dto),
)
```

**Either side missing means no comparison.** A record without a fingerprint — written by a
caller that passed none, or before the field existed — never raises, and a caller without a
fingerprint gets the key-only behaviour. Nothing changes for anyone who does not opt in, and
no record needs migrating.

**Fingerprint what identifies the request, not what varies between retries.** A timestamp,
a trace id or `self` in `fingerprint_params` turns every honest retry into a key reuse. A
name the function does not have raises `TypeError` at decoration time; a value with no JSON
form raises `PydanticSerializationError` when the call is made.

**With `in_flight="run"`** the check is on the read, before the action. If the mismatch only
shows up on the collision after both callers ran, it is logged and counted as `key_reuse`
and the caller keeps its own result: raising then would make the caller retry a completed
operation. Every refusal is counted under `record_error(operation, "key_reuse")`; a client
that reuses keys is worth an alert.

## Advanced Use Cases

### Custom TTL
By default, records live for 30 minutes. You can customize this when creating the record:

```python
# Assuming result is a Pydantic model
result_dict = result.model_dump(mode="json")

record = service.create_record(
    operation="long_lived_op",
    idempotency_key=key,
    result=result_dict,
    ttl_minutes=1440  # 24 hours
)
```

## Bulk Operations

The library provides bulk operations to handle multiple idempotency records efficiently. This is useful for batch processing or processing multiple related operations in one go.

```python
# Retrieve multiple records
results = await idempotency_repo.get_many(operation="user.create", idempotency_keys=["key1", "key2"])
# Returns a dict: {"key1": record1, "key2": record2}

# Save multiple records (non-atomic by default)
records = [
    idempotency_service.create_record("op", "key1", {"res": 1}),
    idempotency_service.create_record("op", "key2", {"res": 2}),
]
try:
    await idempotency_repo.save_many(records)
except IdempotencyError as e:
    # Some records might have failed to save (e.g. collision or serialization error)
    # Check error message for details. Successfully saved records remain in Redis!
    logger.error(f"Bulk save failed: {e}")

# Save multiple records with rollback on error
try:
    await idempotency_repo.save_many(records, rollback_on_error=True)
except IdempotencyError:
    # If any record fails (e.g. collision on key2), successfully saved records (key1)
    # will be automatically deleted from Redis to maintain atomicity.
    pass
```

## Deleting Records

You can manually delete records when needed:

```python
# Delete single record
deleted = await idempotency_repo.delete("user.create", idempotency_key)

# Delete multiple records
deleted_count = await idempotency_repo.delete_many("user.create", [key1, key2, key3])
```

## Error Handling Patterns

Distinguishing between different error types allows for robust integration.

### Graceful Degradation (IdempotencyStorageError)

If Redis is down, you might want to proceed with the operation anyway.

```python
try:
    cached = await repo.get(op, key)
except IdempotencyStorageError as e:
    # Log the infrastructure error
    logger.error(f"Idempotency storage unavailable: {e}")
    # Proceed to execute business logic (at-least-once)
    cached = None
```

### Client Errors (IdempotencyValidationError)

These represent bugs in the integration or invalid input from the client.

```python
try:
    record = service.create_record(op, key, result)
except IdempotencyValidationError as e:
    # This might have detailed errors from Pydantic
    print(f"Validation failed: {e.errors}")
    # This usually should result in a 400 Bad Request
    raise
```

## Redis Cluster Compatibility

The `RedisAsyncIdempotencyRepository` is fully compatible with Redis Cluster. It uses non-transactional pipelines (`transaction=False`) for bulk operations, allowing keys to be distributed across different hash slots.

**Note on Atomicity**: Since non-transactional pipelines are used, `save_many` is **not atomic** by default. If an error occurs, some records might remain in Redis. Use `rollback_on_error=True` if you need to ensure that either all records are saved or none (the library will manually delete successfully saved records if a failure occurs).

## Metrics and Observability

You can inject a metrics collector into the repository and the coordinator to track hits, misses, collisions, errors and latency. Each metric has one owner, so the same collector goes to both without double counting: the coordinator records hit, miss, collision and the latency of `get`, `reserve` and `save`; the repository records errors, the bulk hit and miss counts of `get_many`, and the latency of `delete` and `get_many`. A collision is two callers on one key at the same time — the second one found the first one's reservation, or, with `in_flight="run"`, its own save collided.

```python
from idempotency_kit.core.protocols.metrics import IdempotencyMetricsProtocol

class PrometheusMetrics(IdempotencyMetricsProtocol):
    def record_hit(self, operation: str) -> None:
        # Increment hit counter
        pass

    def record_miss(self, operation: str) -> None:
        # Increment miss counter
        pass

    def record_collision(self, operation: str) -> None:
        # Increment collision counter
        pass

    def record_error(self, operation: str, error_type: str) -> None:
        # Increment error counter with error_type label
        pass

    def record_latency(self, operation: str, method: str, duration_seconds: float) -> None:
        # Record duration in histogram with method label
        pass

    def record_bulk_hit(self, operation: str, count: int) -> None:
        # Increment bulk hit counter
        pass

    def record_bulk_miss(self, operation: str, count: int) -> None:
        # Increment bulk miss counter
        pass

metrics = PrometheusMetrics()
repo = RedisAsyncIdempotencyRepository(redis, metrics=metrics)
coordinator = AsyncIdempotencyCoordinator(repo, IdempotencyDomainService(), metrics=metrics)
```

## Configuration Reference

### IdempotencyDomainService

```python
service = IdempotencyDomainService(
    default_ttl_minutes=60,    # Default: 60 minutes
    min_ttl_seconds=60,        # Default: 60 seconds (1 min)
    max_ttl_seconds=2592000    # Default: 2592000 seconds (30 days)
)
```

### RedisAsyncIdempotencyRepository

```python
repo = RedisAsyncIdempotencyRepository(
    redis=redis_client,
    key_prefix="idempotency:",  # Default prefix for Redis keys
    metrics=custom_metrics      # Optional metrics collector
)
```

### AsyncIdempotencyCoordinator

```python
coordinator = AsyncIdempotencyCoordinator(
    repo,
    service,
    operation_ttls={"order.create": 3600},  # Per-operation TTLs in seconds; win over the decorator
    metrics=custom_metrics,                 # Optional metrics collector
    enabled=True,                           # False runs the action and nothing else
    in_flight="wait",                       # "wait" | "raise" | "run", see In-flight requests
    in_flight_lease_seconds=30,             # How long a reservation is held; must outlive the action
)
```

### Constants

`idempotency_kit.core.constants` is the single source for the TTL defaults, and
`BaseIdempotencySettings` takes its field defaults from it — build the service by hand or
from settings and you get the same numbers.

| Constant | Value | What it bounds |
|---|---|---|
| `DEFAULT_TTL_MINUTES` | 60 | how long a record is kept when no TTL is given |
| `MIN_TTL_SECONDS` | 60 | the floor every TTL is raised to |
| `MAX_TTL_SECONDS` | 2592000 | the ceiling, thirty days |
| `DEFAULT_IN_FLIGHT_MODE` | `"wait"` | what a second caller gets while the first is in flight |
| `DEFAULT_IN_FLIGHT_LEASE_SECONDS` | 30 | how long a reservation is held, and the longest a caller waits |
| `IN_FLIGHT_POLL_INTERVAL_SECONDS` | 0.05 | how often a waiting caller re-reads the key |

`MAX_KEY_LENGTH`, `MAX_OPERATION_LENGTH` and the `InFlightMode` type live beside them.

#### Upgrading

These numbers used to depend on how the service was built: `IdempotencyDomainService`
defaulted to 30 minutes with a 24-hour ceiling, while `BaseIdempotencySettings` shipped 60
minutes and 30 days. They agree now, and the wider pair won — narrowing would have started
rejecting TTLs that work today, and an out-of-range TTL is swallowed, so those operations
would have gone quietly uncached rather than failing loudly.

So a service built with no arguments now keeps records for an hour rather than half of one.
Pass `default_ttl_minutes=30` and `max_ttl_seconds=86400` explicitly to keep the old
behaviour.

### Key Scope and Format
The same `idempotency_key` can be used for different operations (e.g., `user.create` and `identifier.attach`) because the repository prefixes the key with the operation name.

**Redis Key Format:**
`{key_prefix}{operation}:{idempotency_key}`

**Constraints:**
- Neither `operation` nor `idempotency_key` may contain the colon (`:`) character.
- Both are stripped of leading/trailing whitespace.
- Max lengths: operation (100), key (255).

### Error Handling

The library defines several exceptions to handle various idempotency scenarios:

- **`IdempotencyKeyCollisionError`**: Raised by `repository.save()` when you try to save a result for a key that already exists. This typically means another identical request is either being processed or has already finished.
- **`IdempotencyInProgressError`**: Raised by `coordinator.coordinate()` and the decorator when another call with the same key is still running its action — at once with `in_flight="raise"`, after a whole lease of waiting with `in_flight="wait"`. Map it to HTTP 409.
- **`IdempotencyKeyReuseError`**: Raised by `coordinator.coordinate()` and the decorator when the record under the key was made for a request with a different fingerprint. Carries both fingerprints. Map it to HTTP 422.
- **`IdempotencyRecordExpiredError`**: Raised by `service.validate_record()` if the record exists but its TTL has passed.
- **`IdempotencyInvalidTTLError`**: Raised by `service.create_record()` if the requested TTL is outside the allowed range (configured in `IdempotencyDomainService`).
- **`IdempotencyValidationError`**: Raised by `service.create_record()` if validation of `operation` or `idempotency_key` fails (e.g., empty string or too long).
- **`IdempotencyStorageError`**: Raised for critical infrastructure failures like Redis connection errors. This allows you to distinguish between a cache miss and a storage backend failure.
- **`IdempotencyError`**: Base exception for all library errors. Also raised for data corruption during deserialization.

## Best Practices

1. **Natural Keys**: Use natural unique identifiers as idempotency keys if possible (e.g., `order_id`, `message_id`).
2. **Atomic Operations**: Always save the result to the cache *after* the business logic has successfully completed.
3. **Lease Longer Than the Action**: Set `in_flight_lease_seconds` above the longest the action can take, timeouts and retries included; a reservation that expires mid-run lets the next caller run the action again.
4. **Fingerprint the Request**: Name the parameters that identify the request in `fingerprint_params`, so a key reused by mistake for a different request is refused with `IdempotencyKeyReuseError` rather than answered with the first result.
5. **Pydantic Support**: The library works best with Pydantic models. Use `model_dump(mode="json")` when saving and `**cached.result` when restoring.
6. **Graceful Degradation**: Decide whether your service should fail if idempotency storage is down. For most high-availability services, it's better to log an error and proceed (at-least-once delivery) than to crash (exactly-once requirement).

## Production Examples

Real-world examples from production microservices using idempotency-kit.

### Example 1: Simple Create User (identity-service)

This example shows the basic pattern with decorator usage.

```python
from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    PydanticResultAdapter,
    async_idempotent,
)

class CreateUserUseCase:
    """Create user with idempotency protection."""
    
    def __init__(
        self,
        uow: AsyncUnitOfWork,
        user_domain_service: UserDomainService,
        idempotency_coordinator: AsyncIdempotencyCoordinator,
    ):
        self._uow = uow
        self._user_domain_service = user_domain_service
        self._idempotency_coordinator = idempotency_coordinator

    @async_idempotent(
        operation="user.create",
        adapter=PydanticResultAdapter(UserDTO),
    )
    async def execute(
        self,
        dto: CreateUserDTO,
        idempotency_key: str | None = None,
    ) -> UserDTO:
        """Create user and return DTO."""
        async with self._uow.transaction() as tx:
            # Check uniqueness
            existing_user = await tx.users.get_by_username(dto.username)
            if existing_user:
                raise UserAlreadyExistsError(dto.username)

            # Create user
            user = self._user_domain_service.create_new_user(dto.username)
            created_user = await tx.users.create(user)

            # Emit outbox event
            await tx.outbox.create(
                UserCreatedEvent(
                    user_id=created_user.id,
                    username=created_user.username,
                )
            )

            return UserDTO.from_entity(created_user)
```

**Key Points:**
- `@async_idempotent` decorator handles all idempotency logic
- `PydanticResultAdapter` serializes/deserializes Pydantic models automatically; `JsonResultAdapter` stores any JSON value as is, and `VoidResultAdapter` is for operations that return `None` — the record stores `null` and replays it without re-executing
- `idempotency_key` parameter is optional - clients can omit it for non-idempotent calls
- Coordinator is injected via DI (Dishka)

### Example 2: Complex Orchestration (auth-orchestrator)

This example shows idempotency in a multi-step orchestration workflow with external service calls.

```python
from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    PydanticResultAdapter,
    async_idempotent,
)

class ConfirmSignupUseCase:
    """Orchestrate signup confirmation across multiple services."""

    def __init__(
        self,
        uow: AsyncUnitOfWork,
        identity_client: IdentityClientProtocol,
        verification_client: VerificationClientProtocol,
        password_vault: PasswordVaultClientProtocol,
        idempotency_coordinator: AsyncIdempotencyCoordinator,
    ):
        self._uow = uow
        self._identity = identity_client
        self._verification = verification_client
        self._password_vault = password_vault
        self.idempotency_coordinator = idempotency_coordinator

    @async_idempotent(
        operation="signup.confirm",
        adapter=PydanticResultAdapter(ConfirmSignupResultDTO),
    )
    async def execute(
        self,
        dto: ConfirmSignupDTO,
        idempotency_key: str | None = None,
    ) -> ConfirmSignupResultDTO:
        """Confirm signup with idempotency across service boundaries.
        
        Orchestrates:
        1. Verify code in verification-service
        2. Create user in identity-service
        3. Set password in credential-service
        4. Attach identifier in identity-service
        5. Create assertion locally
        """
        # 1. Get session
        async with self._uow.transaction() as tx:
            session = await tx.sessions.get(dto.session_id)
            if not session:
                raise SessionNotFoundError(dto.session_id)

        # 2. Verify code (external service)
        await self._verification.v1.verify_code(
            challenge_id=session.challenge_id,
            code=dto.code,
        )

        # 3. Create user (external service with idempotency)
        response = await self._identity.v1.create_user(
            username=dto.username,
            idempotency_key=idempotency_key,  # Pass through!
        )
        user_id = response.user.id

        # 4. Set password (external service with idempotency)
        await self._password_vault.v1.set_password(
            user_id=user_id,
            password=dto.password,
            idempotency_key=idempotency_key,  # Pass through!
        )

        # 5. Attach identifier (external service with idempotency)
        await self._identity.v1.attach_verified_identifier(
            user_id=user_id,
            identifier_type=session.identifier_type,
            identifier_value=session.identifier_value,
            idempotency_key=idempotency_key,  # Pass through!
        )

        # 6. Create assertion locally
        async with self._uow.transaction() as tx:
            assertion = self._assertion_svc.create_assertion(
                session_id=session.session_id,
                user_id=user_id,
            )
            await tx.assertions.create(assertion)
            
            return ConfirmSignupResultDTO(assertion_id=assertion.assertion_id)
```

**Key Points:**
- Idempotency key is passed through to downstream services
- If the orchestrator fails mid-process, retry will skip already completed steps
- Each downstream service has its own idempotency protection
- Natural composition of idempotent operations

### Example 3: Upsert Pattern (credential-service)

This example shows idempotency with upsert logic (create or replace).

```python
from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    PydanticResultAdapter,
    async_idempotent,
)

class SetPasswordUseCase:
    """Set password with upsert semantics."""

    def __init__(
        self,
        uow: AsyncUnitOfWork,
        password_domain_service: PasswordDomainService,
        idempotency_coordinator: AsyncIdempotencyCoordinator,
    ):
        self._uow = uow
        self._password_domain_service = password_domain_service
        self.idempotency_coordinator = idempotency_coordinator

    @async_idempotent(
        operation="password.set",
        adapter=PydanticResultAdapter(PasswordDTO),
    )
    async def execute(
        self,
        dto: SetPasswordDTO,
        idempotency_key: str | None = None,
    ) -> PasswordDTO:
        """Create or replace password credential."""
        async with self._uow.transaction() as tx:
            # Create password entity
            credential = await self._password_domain_service.create_password(
                user_id=dto.user_id,
                password=dto.password.get_secret_value(),
                must_change=dto.must_change,
            )

            # Upsert: returns (entity, was_created)
            created_credential, created = await tx.passwords.upsert(credential)

            # Emit different events based on whether it was created or updated
            event_type = "password.set" if created else "password.changed"
            await tx.outbox.create(
                PasswordEvent(
                    user_id=created_credential.user_id,
                    event_type=event_type,
                )
            )

            return PasswordDTO.from_entity(created_credential)
```

**Key Points:**
- Works with upsert operations (idempotent at both app and DB level)
- Same result returned whether password was created or updated
- Idempotency key ensures at-most-once semantics even for upserts

### DI Setup with Dishka

All examples use dependency injection to get the `AsyncIdempotencyCoordinator`:

```python
# infra/di/providers/use_cases.py
from dishka import Provider, Scope, provide
from idempotency_kit import AsyncIdempotencyCoordinator

class UseCaseProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def get_create_user_use_case(
        self,
        uow: AsyncUnitOfWork,
        user_domain_service: UserDomainService,
        idempotency_coordinator: AsyncIdempotencyCoordinator,
    ) -> CreateUserUseCase:
        return CreateUserUseCase(
            uow=uow,
            user_domain_service=user_domain_service,
            idempotency_coordinator=idempotency_coordinator,
        )
```

The coordinator itself is provided by the providers shipped in `idempotency_kit.dishka`:

```python
# infra/di/containers/api.py
from dishka import make_async_container
from idempotency_kit.dishka import (
    AsyncIdempotencyCoordinatorProvider,
    AsyncRedisIdempotencyProvider,
    IdempotencyProvider,
)

def create_api_container(settings: Settings) -> AsyncContainer:
    return make_async_container(
        DatabaseProvider(),
        RedisProvider(),
        IdempotencyProvider(),  # Provides domain service and metrics collector
        AsyncRedisIdempotencyProvider(),  # Provides repository
        AsyncIdempotencyCoordinatorProvider(),  # Provides coordinator
        UseCaseProvider(),
    )
```

Your own providers supply the `Redis` client and the settings object; the settings object must satisfy `IdempotencySettingsProtocol`, which `BaseIdempotencySettings` does. `IdempotencyProvider` also provides the metrics collector: `PrometheusIdempotencyMetrics` when `metrics_enabled` is true (install the `prometheus` extra), a no-op collector otherwise. To use another metrics backend, provide `IdempotencyMetricsProtocol` yourself with `@provide(override=True)` in a provider listed after `IdempotencyProvider()`.

`enabled` on the settings object is the kill switch: with `enabled=False` the coordinator these providers build runs the action and nothing else — no read, no write, no metric. `in_flight` and `in_flight_lease_seconds` reach the coordinator the same way. A settings object that predates these fields is read as enabled, `"wait"` and 30 seconds.

## Migration Guide

### From older versions or other libraries
When migrating to `idempotency-kit`, ensure that your `operation` names are consistent. If you need to keep existing cached results, you may need to adjust the `key_prefix` in `RedisAsyncIdempotencyRepository` to match your previous naming scheme.
