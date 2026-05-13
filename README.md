# idempotency-kit

Production-ready idempotency library for async Python applications.

[![PyPI](https://img.shields.io/pypi/v/idempotency-kit?color=blue)](https://pypi.org/project/idempotency-kit/)
[![Python](https://img.shields.io/pypi/pyversions/idempotency-kit)](https://pypi.org/project/idempotency-kit/)
[![License](https://img.shields.io/github/license/bedrock-python/idempotency-kit)](LICENSE)
[![CI](https://github.com/bedrock-python/idempotency-kit/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/bedrock-python/idempotency-kit/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/bedrock-python/idempotency-kit/graph/badge.svg)](https://codecov.io/gh/bedrock-python/idempotency-kit)
[![Docs](https://img.shields.io/badge/docs-online-blue)](https://bedrock-python.github.io/idempotency-kit/)

Ensure operations execute exactly once, even when called multiple times with the same idempotency key. Built for production microservices with graceful degradation, collision handling, and observability.

## Features

- **Clean Architecture** — core domain separated from infrastructure
- **Protocol-Based** — easy to swap storage backends (Redis, custom)
- **Type-Safe** — full type hints with Pydantic validation
- **Async First** — built for asyncio applications
- **Graceful Degradation** — high availability over strict exactly-once
- **Collision Handling** — automatic resolution of concurrent requests
- **Observability** — built-in metrics (hits, misses, collisions, latency)
- **Bulk Operations** — efficient `get_many`, `save_many`, `delete_many`
- **Redis Cluster Compatible** — non-transactional pipelines
- **Decorator Pattern** — `@async_idempotent` for zero-boilerplate integration

## Installation

```bash
pip install idempotency-kit

# With Redis support (recommended)
pip install idempotency-kit[redis]

# With Dishka DI
pip install idempotency-kit[dishka,redis]
```

**Requirements:** Python 3.11+, Redis 6+

## Quick start

```python
from idempotency_kit import AsyncIdempotencyCoordinator, PydanticResultAdapter, async_idempotent

class CreateOrderUseCase:
    def __init__(self, uow: AsyncUnitOfWork, coordinator: AsyncIdempotencyCoordinator):
        self._uow = uow
        self.coordinator = coordinator

    @async_idempotent(
        operation="order.create",
        adapter=PydanticResultAdapter(OrderDTO),
    )
    async def execute(
        self,
        dto: CreateOrderDTO,
        idempotency_key: str | None = None,
    ) -> OrderDTO:
        """Create order - idempotency handled automatically."""
        async with self._uow.transaction() as tx:
            # Your business logic - no idempotency code needed!
            order = await tx.orders.create(dto.items, dto.total)
            await tx.outbox.create(OrderCreatedEvent(order_id=order.id))
            
            return OrderDTO.from_entity(order)
```

Pass `idempotency_key` to downstream services for distributed idempotency:

```python
# Orchestrate multiple services with same key
await identity_service.create_user(..., idempotency_key=idempotency_key)
await payment_service.charge(..., idempotency_key=idempotency_key)
```

## How it works

### 1. Client provides key

```
POST /api/orders
Idempotency-Key: abc-123-def
```

### 2. Server checks cache

```python
cached = await repo.get("order.create", "abc-123-def")
if cached:
    return cached.result  # Return immediately ✅
```

### 3. Server executes (if not cached)

```python
order = await create_order(dto)
await repo.save(record)  # Cache result for future requests
return order
```

### 4. Concurrent requests handled

If two requests arrive simultaneously:

- First request: cache miss → execute → save ✅
- Second request: collision on save → fetch first result → return ✅

Both requests get the **same result** - idempotency guaranteed!

## Use cases

- **HTTP APIs** — ensure POST/PUT requests are idempotent
- **Background Jobs** — prevent duplicate processing on retries
- **Event Consumers** — handle duplicate events gracefully
- **Message Queues** — at-most-once message processing

## Documentation

📚 **[Full Documentation](https://bedrock-python.github.io/idempotency-kit/)**

- [Quick Start](https://bedrock-python.github.io/idempotency-kit/quickstart/) — get started in 5 minutes
- [User Guide](https://bedrock-python.github.io/idempotency-kit/user_guide/) — detailed usage and patterns
- [Architecture](https://bedrock-python.github.io/idempotency-kit/architecture/) — design principles
- [API Reference](https://bedrock-python.github.io/idempotency-kit/api_reference/) — complete API docs

## Development

```bash
make install          # uv sync --group dev
make check            # ruff + mypy
make test-unit        # unit tests (no Docker)
make test-integration # integration tests (Docker required)
make test             # all tests with coverage
make docs-serve       # local docs preview
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

## License

[Apache 2.0](LICENSE)
