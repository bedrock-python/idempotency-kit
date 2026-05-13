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

You can inject a metrics collector into the repository to track hits, misses, collisions, and latency.

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

repo = RedisAsyncIdempotencyRepository(redis, metrics=PrometheusMetrics())
```

## Configuration Reference

### IdempotencyDomainService

```python
service = IdempotencyDomainService(
    default_ttl_minutes=30,  # Default: 30 minutes
    min_ttl_seconds=60,      # Default: 60 seconds (1 min)
    max_ttl_seconds=86400    # Default: 86400 seconds (24 hours)
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

### Constants

See `idempotency_kit.core.constants` for:
- `MAX_KEY_LENGTH`, `MAX_OPERATION_LENGTH`
- `DEFAULT_TTL_MINUTES`, `MIN_TTL_SECONDS`, `MAX_TTL_SECONDS`

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
- **`IdempotencyRecordExpiredError`**: Raised by `service.validate_record()` if the record exists but its TTL has passed.
- **`IdempotencyInvalidTTLError`**: Raised by `service.create_record()` if the requested TTL is outside the allowed range (configured in `IdempotencyDomainService`).
- **`IdempotencyValidationError`**: Raised by `service.create_record()` if validation of `operation` or `idempotency_key` fails (e.g., empty string or too long).
- **`IdempotencyStorageError`**: Raised for critical infrastructure failures like Redis connection errors. This allows you to distinguish between a cache miss and a storage backend failure.
- **`IdempotencyError`**: Base exception for all library errors. Also raised for data corruption during deserialization.

## Best Practices

1. **Natural Keys**: Use natural unique identifiers as idempotency keys if possible (e.g., `order_id`, `message_id`).
2. **Atomic Operations**: Always save the result to the cache *after* the business logic has successfully completed.
3. **Pydantic Support**: The library works best with Pydantic models. Use `model_dump(mode="json")` when saving and `**cached.result` when restoring.
4. **Graceful Degradation**: Decide whether your service should fail if idempotency storage is down. For most high-availability services, it's better to log an error and proceed (at-least-once delivery) than to crash (exactly-once requirement).

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
- `PydanticResultAdapter` serializes/deserializes Pydantic models automatically
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

The coordinator itself is provided by `dishka-providers`:

```python
# infra/di/containers/api.py
from dishka import make_async_container
from dishka_providers.idempotency.aio.coordinator import AsyncIdempotencyCoordinatorProvider
from dishka_providers.idempotency.aio.redis import AsyncRedisIdempotencyProvider

def create_api_container(settings: Settings) -> AsyncContainer:
    return make_async_container(
        DatabaseProvider(),
        RedisProvider(),
        AsyncRedisIdempotencyProvider(),  # Provides repository
        AsyncIdempotencyCoordinatorProvider(),  # Provides coordinator
        UseCaseProvider(),
    )
```

## Migration Guide

### From older versions or other libraries
When migrating to `idempotency-kit`, ensure that your `operation` names are consistent. If you need to keep existing cached results, you may need to adjust the `key_prefix` in `RedisAsyncIdempotencyRepository` to match your previous naming scheme.
