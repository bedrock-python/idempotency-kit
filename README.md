# idempotency-kit

Production-ready idempotency library for async Python applications.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## Features

- 🏗️ **Clean Architecture** - Core domain separated from infrastructure
- 🔌 **Protocol-Based** - Easy to add new storage backends (Redis, Postgres, etc.)
- ✅ **Type-Safe** - Full type hints with Pydantic validation
- ⚡ **Async First** - Built for modern asyncio applications
- 🛡️ **Production-Ready** - Graceful degradation, error handling, TTL management
- 📊 **Observability** - Built-in metrics (hits, misses, collisions, latency)
- 📦 **Bulk Operations** - Efficient `get_many`, `save_many`, `delete_many`
- 🔗 **Redis Cluster Compatible** - Non-transactional pipelines for distributed environments

## Requirements

- **Python**: 3.11+
- **Redis**: 6+ (for Redis backend)

## Installation

```bash
# Core only (Pydantic models and protocols)
pip install idempotency-kit

# With Redis support
pip install idempotency-kit[redis]

# With Dishka DI + Settings support
pip install idempotency-kit[dishka,redis,settings]

# With Prometheus metrics
pip install idempotency-kit[redis,prometheus]
```

## Quick Start

```python
from redis.asyncio import Redis
from idempotency_kit import IdempotencyDomainService, IdempotencyError, IdempotencyKeyCollisionError
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

# 1. Initialize
redis = Redis.from_url("redis://localhost:6379")
repo = RedisAsyncIdempotencyRepository(redis)
service = IdempotencyDomainService()

# 2. Use in your use case
class CreateUserUseCase:
    async def execute(self, dto: CreateUserDTO, idempotency_key: str | None = None) -> UserDTO:
        operation = "user.create"

        # Check cache (graceful degradation on failure)
        if idempotency_key:
            try:
                cached = await repo.get(operation, idempotency_key)
                if cached:
                    return UserDTO(**cached.result)
            except IdempotencyError:
                pass  # Log and proceed

        # Execute business logic
        user = await self._create_user(dto)
        result = UserDTO.from_entity(user)

        # Save to cache
        if idempotency_key:
            record = service.create_record(
                operation=operation,
                idempotency_key=idempotency_key,
                result=result.model_dump(mode="json"),
            )
            try:
                await repo.save(record)
            except IdempotencyKeyCollisionError:
                # Concurrent request finished first - use its result
                cached = await repo.get(operation, idempotency_key)
                if cached:
                    return UserDTO(**cached.result)
            except IdempotencyError:
                pass  # Log and continue

        return result
```

## Documentation

📚 **[Full Documentation](docs/)**

| Guide | Description |
|-------|-------------|
| **[Quick Start](docs/quickstart.md)** | Get started in 5 minutes |
| **[User Guide](docs/user_guide.md)** | Detailed usage examples and best practices |
| **[Architecture](docs/architecture.md)** | Design principles and request flows |
| **[API Reference](docs/api_reference.md)** | Complete API documentation |
| **[Testing](docs/testing_conventions.md)** | Testing guidelines and patterns |

## Key Concepts

### Idempotency Pattern

1. Client provides unique `idempotency_key` for an operation
2. Server checks if result exists in cache
3. **If found** → return cached result (idempotent ✅)
4. **If not found** → execute, cache result, return it

### Error Handling

| Exception | Meaning | Action |
|-----------|---------|--------|
| `IdempotencyKeyCollisionError` | Duplicate key (concurrent/retry) | Return winner's result |
| `IdempotencyStorageError` | Redis unavailable | Graceful degradation |
| `IdempotencyValidationError` | Invalid input | Return 400 Bad Request |
| `IdempotencyRecordExpiredError` | Record expired | Re-execute operation |

### Graceful Degradation

If Redis is unavailable:
- ✅ Operation succeeds (high availability)
- ⚠️ Result not cached (at-least-once delivery)
- 📝 Error logged for monitoring

This ensures **high availability** over strict exactly-once semantics.

## Advanced Features

### Bulk Operations

```python
# Save multiple records (atomic with rollback)
records = [
    service.create_record("op", "key1", {"v": 1}),
    service.create_record("op", "key2", {"v": 2}),
]
await repo.save_many(records, rollback_on_error=True)

# Retrieve multiple
results = await repo.get_many("op", ["key1", "key2"])  # dict[str, IdempotencyRecord]

# Delete multiple
deleted_count = await repo.delete_many("op", ["key1", "key2"])
```

### Custom TTL

```python
# Default TTL: 30 minutes
record = service.create_record("op", "key", result)

# Custom TTL: 24 hours
record = service.create_record("op", "key", result, ttl_minutes=1440)

# Per-operation TTL via settings
settings = BaseIdempotencySettings(
    default_ttl_minutes=30,
    operation_ttls={"user.create": 60, "order.process": 1440}
)
```

### Metrics

```python
from idempotency_kit.core.protocols.metrics import IdempotencyMetricsProtocol

class PrometheusMetrics(IdempotencyMetricsProtocol):
    def record_hit(self, operation: str) -> None:
        CACHE_HITS.labels(operation=operation).inc()
    
    def record_collision(self, operation: str) -> None:
        COLLISIONS.labels(operation=operation).inc()
    
    # ... implement other methods

repo = RedisAsyncIdempotencyRepository(redis, metrics=PrometheusMetrics())
```

## Dependency Injection (Dishka)

```python
from dishka import make_async_container, Provider, Scope, provide
from redis.asyncio import Redis
from idempotency_kit.settings import BaseIdempotencySettings
from idempotency_kit.dishka.common import IdempotencyProvider
from idempotency_kit.dishka.aio import AsyncRedisIdempotencyProvider

settings = BaseIdempotencySettings(
    key_prefix="idempotency:",
    default_ttl_minutes=60,
    operation_ttls={"user.create": 3600},
)

class RedisProvider(Provider):
    @provide(scope=Scope.APP)
    async def get_redis(self) -> Redis:
        return Redis.from_url("redis://localhost:6379")

container = make_async_container(
    RedisProvider(),
    IdempotencyProvider(),
    AsyncRedisIdempotencyProvider(),
)
```

See [User Guide](docs/user_guide.md) for complete Dishka integration examples.

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development setup
- Code style guidelines
- Testing conventions
- Pull request process

## License

[Apache 2.0](LICENSE) - see LICENSE file for details.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history and migration guides.
