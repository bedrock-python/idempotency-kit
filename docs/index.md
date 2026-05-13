# idempotency-kit Documentation

Production-ready idempotency library for async Python applications.

## What is idempotency-kit?

**idempotency-kit** provides production-ready idempotency for microservices. It ensures that operations are executed exactly once, even when called multiple times with the same idempotency key.

### Key Features

- 🏗️ **Clean Architecture** - Core domain separated from infrastructure
- 🔌 **Protocol-Based** - Easy to swap storage backends
- ✅ **Type-Safe** - Full type hints with Pydantic validation
- ⚡ **Async First** - Built for asyncio applications
- 🛡️ **Production-Ready** - Graceful degradation and error handling
- 📊 **Observable** - Built-in metrics support
- 📦 **Bulk Operations** - Efficient batch processing
- 🔗 **Redis Cluster** - Compatible with distributed Redis

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
            # Your business logic - no idempotency code needed!
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

Ensure at-most-once processing of messages.

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
    return cached.result  # Return immediately (idempotent ✅)
```

### 3. Server Executes (if not cached)

```python
order = await create_order(dto)
await repo.save(record)  # Cache result for future requests
return order
```

### 4. Concurrent Requests Handled

If two requests arrive simultaneously:

- First request: cache miss → execute → save ✅
- Second request: collision on save → fetch first result → return ✅

Both requests get the **same result** - idempotency guaranteed!

## Why idempotency-kit?

### Without idempotency-kit

```python
# ❌ Duplicate requests create duplicate orders
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
# ✅ Duplicate requests return same result
@app.post("/orders")
async def create_order(dto, idempotency_key: str | None = None):
    result = await use_case.execute(dto, idempotency_key)
    return result
```

**Benefits:**
- Automatic deduplication
- Handles concurrent requests
- Production-ready error handling
- Observable with metrics

## Community

- **GitHub**: [bedrock-python/idempotency-kit](https://github.com/bedrock-python/idempotency-kit)
- **Issues**: [Report bugs or request features](https://github.com/bedrock-python/idempotency-kit/issues)
- **Changelog**: [Version history](changelog.md)

## License

Apache 2.0 - see [LICENSE](https://github.com/bedrock-python/idempotency-kit/blob/master/LICENSE) for details.
