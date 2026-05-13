# Quick Start Guide

Get started with idempotency-kit in 5 minutes.

## Installation

```bash
pip install idempotency-kit[redis]
```

## Basic Setup

### 1. Initialize Dependencies

```python
from redis.asyncio import Redis
from idempotency_kit import IdempotencyDomainService
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

# Initialize Redis client
redis_client = Redis.from_url("redis://localhost:6379")

# Create repository and domain service
repo = RedisAsyncIdempotencyRepository(redis_client)
service = IdempotencyDomainService()
```

### 2. Use in Your Use Case

```python
from idempotency_kit import IdempotencyError, IdempotencyKeyCollisionError

class CreateOrderUseCase:
    def __init__(self, idempotency_repo, idempotency_service):
        self._repo = idempotency_repo
        self._service = idempotency_service

    async def execute(self, dto: CreateOrderDTO, idempotency_key: str | None = None) -> OrderDTO:
        operation = "order.create"

        # 1. Check cache
        if idempotency_key:
            try:
                cached = await self._repo.get(operation, idempotency_key)
                if cached:
                    return OrderDTO(**cached.result)
            except IdempotencyError:
                pass  # Log error but proceed

        # 2. Execute business logic
        order = await self._create_order(dto)
        result = OrderDTO.from_entity(order)

        # 3. Save to cache
        if idempotency_key:
            record = self._service.create_record(
                operation=operation,
                idempotency_key=idempotency_key,
                result=result.model_dump(mode="json"),
            )
            try:
                await self._repo.save(record)
            except IdempotencyKeyCollisionError:
                # Concurrent request finished first - use its result
                cached = await self._repo.get(operation, idempotency_key)
                if cached:
                    return OrderDTO(**cached.result)
            except IdempotencyError:
                pass  # Log error

        return result

    async def _create_order(self, dto: CreateOrderDTO) -> Order:
        # Your business logic here
        ...
```

## Understanding the Flow

### Cache Hit (Idempotent Request)

```
Client --idempotency_key--> Use Case
                               |
                               v
                          Check Cache
                               |
                          Found! ✅
                               |
                               v
                     Return Cached Result
```

### Cache Miss (First Request)

```
Client --idempotency_key--> Use Case
                               |
                               v
                          Check Cache
                               |
                          Not Found
                               |
                               v
                      Execute Logic ⚙️
                               |
                               v
                         Save Result
                               |
                               v
                     Return New Result
```

### Concurrent Requests (Collision)

```
Request A --key--> Check (miss) --> Execute --> Save ✅
Request B --key--> Check (miss) --> Execute --> Save ❌ (collision)
                                                  |
                                                  v
                                             Get A's result
```

## Key Concepts

### Idempotency Key

A unique identifier provided by the client to ensure the same operation is not executed twice:

```python
# Good idempotency keys (unique per operation instance)
idempotency_key = str(uuid.uuid4())  # Client-generated UUID
idempotency_key = f"order-{order_id}"  # Natural unique identifier
idempotency_key = request.headers.get("Idempotency-Key")  # From HTTP header

# Bad idempotency keys (not unique)
idempotency_key = "create_order"  # Same for all requests
idempotency_key = str(datetime.now())  # Changes every millisecond
```

### Operation Name

A string identifying the type of operation:

```python
operation = "user.create"     # User registration
operation = "order.create"    # Order creation
operation = "payment.process"  # Payment processing

# Same key can be used for different operations
await repo.get("user.create", "abc123")   # Different record
await repo.get("order.create", "abc123")  # Different record
```

### TTL (Time To Live)

How long the cached result should be kept:

```python
# Default: 30 minutes
record = service.create_record("op", "key", result)

# Custom TTL: 1 hour
record = service.create_record("op", "key", result, ttl_minutes=60)

# Service configuration
service = IdempotencyDomainService(
    default_ttl_minutes=30,  # Default TTL
    min_ttl_seconds=60,      # Minimum: 1 minute
    max_ttl_seconds=86400    # Maximum: 24 hours
)
```

## Error Handling

The library provides different exceptions for different scenarios:

### IdempotencyKeyCollisionError

Raised when trying to save a record that already exists (concurrent requests):

```python
try:
    await repo.save(record)
except IdempotencyKeyCollisionError:
    # Another request with same key finished first
    # Fetch and return its result
    cached = await repo.get(operation, idempotency_key)
    return result_from_cached(cached)
```

### IdempotencyStorageError

Raised when Redis is unavailable:

```python
try:
    cached = await repo.get(operation, idempotency_key)
except IdempotencyStorageError as e:
    # Redis is down - gracefully degrade
    logger.error(f"Idempotency storage unavailable: {e}")
    # Proceed to execute operation (at-least-once delivery)
    cached = None
```

### IdempotencyValidationError

Raised for invalid input (empty key, too long string, etc.):

```python
try:
    record = service.create_record("", "key", result)  # Empty operation
except IdempotencyValidationError as e:
    # Client error - return 400 Bad Request
    logger.error(f"Invalid idempotency request: {e}")
    raise HTTPException(400, detail=str(e))
```

## Next Steps

- **[User Guide](user_guide.md)** - Learn advanced patterns and best practices
- **[Architecture](architecture.md)** - Understand the design and request flows
- **[API Reference](api_reference.md)** - Complete API documentation
- **[Testing](testing_conventions.md)** - Learn how to test with idempotency-kit

## Common Patterns

### HTTP API Integration

```python
from fastapi import FastAPI, Header, HTTPException

app = FastAPI()

@app.post("/orders")
async def create_order(
    dto: CreateOrderDTO,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    use_case: CreateOrderUseCase = Depends(get_use_case)
):
    try:
        result = await use_case.execute(dto, idempotency_key)
        return result
    except IdempotencyValidationError as e:
        raise HTTPException(400, detail=str(e))
```

### Background Job Processing

```python
async def process_job(job_data: dict):
    # Use job ID as idempotency key
    idempotency_key = job_data["job_id"]
    
    # Process will be idempotent even if job is retried
    result = await use_case.execute(job_data, idempotency_key)
    return result
```

### Event-Driven Architecture

```python
async def handle_event(event: Event):
    # Use event ID as idempotency key
    idempotency_key = event.id
    
    # Handler is idempotent - duplicate events are safe
    result = await event_handler.execute(event, idempotency_key)
    return result
```
