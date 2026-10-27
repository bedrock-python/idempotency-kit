# idempotency-kit Documentation

Idempotency keys for async Python: one execution per key, and the stored result for every retry.

## What is idempotency-kit?

**idempotency-kit** makes a repeated call with the same idempotency key return the stored result instead of running again, and makes a retry that arrives while the first call is still running wait for it. It is a result cache with an in-flight reservation, not a distributed transaction: one execution per key while Redis is up, at-least-once when it is down unless you fail closed. See [Architecture](architecture.md) for the exact guarantee.

### Key Features

- **Clean Architecture**: core domain separated from infrastructure
- **Protocol-Based**: storage backends are swappable
- **Type-Safe**: full type hints with Pydantic validation
- **Async First**: built for asyncio applications
- **Fails open or closed**: by default a Redis outage runs the action unreserved; `on_storage_error="raise"` refuses instead
- **Observable**: built-in metrics
- **Bulk Operations**: batch get, save and delete
- **Redis Cluster**: compatible with distributed Redis

## When to use it, and when not

idempotency-kit fits an async Python 3.11+ service with Redis that wants idempotency at the use-case level, so the same guard covers an HTTP handler, a background job and a message consumer: retries that arrive while the first call is still running collapse into one execution, and a key reused for a different request is refused instead of replayed.

Something else is probably the better fit when:

- **You only need `Idempotency-Key` on a few HTTP endpoints.** A middleware that caches the response by header is less to wire. There are several for ASGI frameworks, and [django-idempotency-key](https://github.com/yoyowallet/django-idempotency-key) for Django.
- **The write already has a natural key.** If the effect can be an `INSERT ... ON CONFLICT DO NOTHING` on a unique constraint, the database is your idempotency layer and a second store adds nothing.
- **A repeat is unacceptable even when Redis is down.** The reservation is a lease, and by default a store outage runs the action unreserved. For a charge or an email that nothing downstream deduplicates, set `on_storage_error="raise"`, or keep the idempotency record in the same database transaction as the effect.
- **Your code is synchronous, or your store is not Redis.** There is no sync API, and another store means implementing the repository protocol.

## Installation

```bash
# Core only
pip install idempotency-kit

# With Redis support (recommended)
pip install idempotency-kit[redis]

# With Dishka DI
pip install idempotency-kit[dishka,redis]
```

**Requirements:** Python 3.11+, Redis 6+

## Quick Example

Protect your use cases with a single decorator:

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
            # Business logic only; the decorator handles the key
            order = await tx.orders.create(dto.items, dto.total)
            await tx.outbox.create(OrderCreatedEvent(order_id=order.id))
            
            return OrderDTO.from_entity(order)
```

**That's it!** The decorator handles cache check, collision detection, and result storage automatically.

Pass `idempotency_key` to downstream services for distributed idempotency:

```python
# Orchestrate multiple services with same key
await identity_service.create_user(..., idempotency_key=idempotency_key)
await payment_service.charge(..., idempotency_key=idempotency_key)
```

## Documentation

| Guide | Description |
|-------|-------------|
| **[Quick Start](quickstart.md)** | Get started in 5 minutes |
| **[User Guide](user_guide.md)** | Detailed usage examples and patterns |
| **[Architecture](architecture.md)** | Design principles and request flows |
| **[API Reference](api_reference.md)** | Complete API documentation |
| **[Testing](testing_conventions.md)** | Testing guidelines and patterns |

## Use Cases

### HTTP APIs

Ensure POST/PUT requests are idempotent using client-provided `Idempotency-Key` header.

```python
@app.post("/orders")
async def create_order(
    dto: CreateOrderDTO,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key")
):
    result = await use_case.execute(dto, idempotency_key)
    return result
```

### Background Jobs

Prevent duplicate processing when jobs are retried.

```python
async def process_job(job_data):
    idempotency_key = job_data["job_id"]
    result = await use_case.execute(job_data, idempotency_key)
    return result
```

### Event-Driven Systems

Handle duplicate events gracefully in event consumers.

```python
async def handle_event(event: Event):
    idempotency_key = event.id
    result = await handler.execute(event, idempotency_key)
    return result
```

### Message Queue Consumers

A redelivered message gets the stored result instead of a second run.

```python
async def consume_message(message):
    idempotency_key = message.message_id
    await process(message.body, idempotency_key)
```

## How It Works

### 1. Client Provides Key

```
POST /api/orders
Idempotency-Key: abc-123-def
```

### 2. Server Checks Cache

```python
cached = await repo.get("order.create", "abc-123-def")
if cached:
    return cached.result  # Return immediately
```

### 3. Server Executes (if not cached)

```python
order = await create_order(dto)
await repo.save(record)  # Cache result for future requests
return order
```

### 4. Concurrent Requests Handled

If two requests arrive while the first is still executing:

- First request: reserves the key, executes, writes the result over the reservation
- Second request: finds the reservation, waits for the first result and returns it (or gets a 409 with `in_flight="raise"`)

Both requests get the **same result**, and the business logic ran once.

## Why idempotency-kit?

### Without idempotency-kit

```python
# Duplicate requests create duplicate orders
@app.post("/orders")
async def create_order(dto):
    order = await db.save(Order(**dto))  # Duplicate on retry!
    return order
```

**Problems:**
- Network retries create duplicates
- No protection against concurrent requests
- Manual cache management is complex

### With idempotency-kit

```python
# Duplicate requests return the same result
@app.post("/orders")
async def create_order(dto, idempotency_key: str | None = None):
    result = await use_case.execute(dto, idempotency_key)
    return result
```

**Benefits:**
- Automatic deduplication
- Handles concurrent requests
- Explicit behaviour when Redis is down
- Observable with metrics

## Community

- **GitHub**: [bedrock-python/idempotency-kit](https://github.com/bedrock-python/idempotency-kit)
- **Issues**: [Report bugs or request features](https://github.com/bedrock-python/idempotency-kit/issues)
- **Changelog**: [Version history](changelog.md)

## License

Apache 2.0 - see [LICENSE](https://github.com/bedrock-python/idempotency-kit/blob/master/LICENSE) for details.
