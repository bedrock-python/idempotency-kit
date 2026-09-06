# idempotency-kit for AI agents

> One page holding everything a coding assistant needs to wire idempotency-kit into an
> async service and get the semantics right, plus a map of where the rest of the
> documentation keeps the details it leaves out. Give an agent this page rather than the
> whole site.

| | |
|---|---|
| Package | `idempotency-kit` on PyPI, import root `idempotency_kit` |
| Requires | Python 3.11+, Pydantic 2; Redis 6+ for the shipped backend |
| Install | `pip install idempotency-kit` · extras: `redis`, `prometheus`, `dishka` |
| Async | `idempotency_kit` — `async_idempotent`, `AsyncIdempotencyCoordinator` |
| Sync | none; the library is asyncio only |
| Source | <https://github.com/bedrock-python/idempotency-kit> |

## How to read this page

Every page of this site is also served as raw Markdown at its own URL with `.md` in place
of the trailing slash — this page is `/agents.md`, the user guide is `/user_guide.md` — so
anything the map below points at can be fetched as plain text rather than scraped out of
HTML. The **Copy page** control at the top of a page does the same thing for a human with a
chat window open. Every page here is hand-written Markdown, the API reference included, so
every `.md` twin reads as the page does.

Top to bottom before writing code. [Rules that hold or break the code](#rules-that-hold-or-break-the-code)
is the section correctness lives in — those are the things the library will not save you
from. Every name used below is in the public API; if you need something not listed here,
fetch the page the [documentation map](#documentation-map) points at rather than guessing a
method that sounds plausible.

## Scope

**It does** cache the result of an async operation under a caller-supplied key, replay that
result on a repeat call, and resolve the race between two callers that arrive with the same
key: the loser of the write reads the winner's record and returns it. It ships a Redis
repository, a metrics protocol with a Prometheus implementation, a decorator that hides the
whole flow, and Dishka providers that wire the pieces together.

**It does not** derive the key — the caller supplies it, and the request body is not part of
it; it does not lock, reserve or otherwise prevent two concurrent callers from both running
your business logic; it does not roll anything back; it does not cache failures; it does not
retry; it has no sync API; and it stores nothing but JSON. It is a result cache with
race-aware writes, not a distributed transaction.

## Mental model

Four nouns and one flow.

* **`IdempotencyRecord`** — a frozen Pydantic model: `operation`, `idempotency_key`, the
  JSON `result`, `created_at`, `expires_at`. `expires_at` is what decides whether a record
  is still a hit.
* **`AsyncIdempotencyRepository`** — the storage protocol: `get` / `save` / `delete` and
  their bulk twins. `save` is a write that fails if the key is already there; that failure,
  `IdempotencyKeyCollisionError`, is the whole concurrency mechanism.
  `RedisAsyncIdempotencyRepository` is the only implementation that ships.
* **`ResultAdapter`** — `encode` a return value into JSON, `decode` it back. Three ship:
  `PydanticResultAdapter(model_class)`, `JsonResultAdapter()`, `VoidResultAdapter()`.
* **`AsyncIdempotencyCoordinator`** — the flow: read the record, decode it, return it if it
  is there; otherwise run the action, encode the result, write it under `SET NX`, and on a
  collision re-read and return the winner's result instead. It swallows every storage and
  decode failure and degrades to running the action, which is the deliberate trade of
  exactly-once for availability.

`IdempotencyDomainService` sits between the coordinator and the record: it applies the TTL
bounds and turns Pydantic validation errors into `IdempotencyValidationError`.
`@async_idempotent` is the same flow as a decorator — it finds the key in the call's keyword
arguments and the coordinator in the call's arguments or on `self`, then delegates.

The storage key is `{key_prefix}{operation}:{idempotency_key}`, which is why neither part
may contain a colon and why the same key is free to be reused under a different operation.

## Wiring

```python
from redis.asyncio import Redis

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    IdempotencyDomainService,
    PydanticResultAdapter,
    async_idempotent,
)
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository

redis = Redis.from_url("redis://localhost:6379")

coordinator = AsyncIdempotencyCoordinator(
    RedisAsyncIdempotencyRepository(redis, key_prefix="idempotency:"),
    IdempotencyDomainService(default_ttl_minutes=30),
)


class CreateOrder:
    def __init__(self, coordinator: AsyncIdempotencyCoordinator) -> None:
        self.coordinator = coordinator

    @async_idempotent(
        operation="order.create",
        adapter=PydanticResultAdapter(OrderDTO),
        ttl_seconds=3600,
        infra_param="coordinator",
    )
    async def execute(self, dto: CreateOrderDTO, *, idempotency_key: str | None = None) -> OrderDTO:
        order = await self._orders.create(dto)
        return OrderDTO.from_entity(order)
```

`idempotency_key` is keyword-only on purpose: the decorator reads it out of `**kwargs` and
nowhere else. `infra_param="coordinator"` names the attribute rather than leaving the
decorator to find a coordinator by type.

The same call without the decorator — the five leading arguments are positional-only, and
everything after them is forwarded to the action:

```python
result = await coordinator.coordinate(
    "order.create",                      # operation
    idempotency_key,                     # None or "" means: just run the action
    3600,                                # ttl_seconds, or None for the service default
    PydanticResultAdapter(OrderDTO),
    self.execute_uncached,               # the action
    dto,                                 # *args and **kwargs go to the action
)
```

## The API

### Exported from `idempotency_kit`

| Name | Signature | What it is |
|---|---|---|
| `async_idempotent` | `(operation, adapter, ttl_seconds=None, key_param="idempotency_key", infra_param=None)` | decorator for an async function or method |
| `AsyncIdempotencyCoordinator` | `(repository, domain_service, operation_ttls=None, metrics=None)` | the flow; `operation_ttls` is `dict[str, int]` in seconds |
| `IdempotencyDomainService` | `(*, default_ttl_minutes=30, min_ttl_seconds=60, max_ttl_seconds=86400)` | record factory and TTL bounds; keyword-only |
| `IdempotencyRecord` | frozen Pydantic model | the cached result |
| `IdempotencyIdentifiers` | Pydantic model | `operation` + `idempotency_key`, and the rules they obey |
| `AsyncIdempotencyRepository` | runtime-checkable `Protocol` | storage contract |
| `ResultAdapter` | `Protocol[T]` | `encode(value) -> Any`, `decode(data) -> T` |
| `PydanticResultAdapter` | `(model_class)` | `model_dump(mode="json")` out, `model_validate` back |
| `JsonResultAdapter` | `()` | passes the value through untouched |
| `VoidResultAdapter` | `()` | stores JSON `null`, decodes back to `None` |
| `IdempotencyMetricsProtocol` | runtime-checkable `Protocol` | metrics contract |
| `NoOpIdempotencyMetrics` | `()` | the default collector |
| `IdempotencyError` and its five subclasses | | see [Errors](#errors) |

### Not exported from the root

| Name | Import from |
|---|---|
| `RedisAsyncIdempotencyRepository` | `idempotency_kit.infra.storage.redis.aio` |
| `PrometheusIdempotencyMetrics` | `idempotency_kit.infra.metrics.prometheus` |
| `BaseIdempotencySettings` | `idempotency_kit.settings` |
| `IdempotencyProvider`, `AsyncIdempotencyCoordinatorProvider`, `AsyncRedisIdempotencyProvider`, `IdempotencySettingsProtocol` | `idempotency_kit.dishka` |
| `MAX_KEY_LENGTH`, `MAX_OPERATION_LENGTH`, `DEFAULT_TTL_MINUTES`, `MIN_TTL_SECONDS`, `MAX_TTL_SECONDS` | `idempotency_kit.core.constants` |

### Coordinator and domain service

| Method | Returns | Notes |
|---|---|---|
| `AsyncIdempotencyCoordinator.coordinate(operation, idempotency_key, ttl_seconds, adapter, action, /, *args, **kwargs)` | `T` | never raises for storage or decode trouble |
| `IdempotencyDomainService.create_record(operation, idempotency_key, result, *, ttl_minutes=None)` | `IdempotencyRecord` | raises `IdempotencyInvalidTTLError`, `IdempotencyValidationError` |
| `IdempotencyDomainService.validate_record(record)` | `None` | raises `IdempotencyRecordExpiredError`; nothing in the library calls it |

### Record

| Member | Type | Notes |
|---|---|---|
| `operation` | `str` | 1-100 chars, stripped, no `:` |
| `idempotency_key` | `str` | 1-255 chars, stripped, no `:` |
| `result` | `JsonValue` | whatever the adapter encoded; `null` for a void result |
| `created_at` / `expires_at` | `datetime` | UTC, set by `create` |
| `IdempotencyRecord.create(operation, idempotency_key, result, ttl_seconds)` | `IdempotencyRecord` | classmethod; `ttl_seconds` is a `float` here |
| `.is_expired` | `bool` | `now >= expires_at` |
| `.ttl_seconds` | `float` | remaining, `0.0` once expired |

### Repository protocol

| Method | Returns | Raises |
|---|---|---|
| `get(operation, idempotency_key)` | `IdempotencyRecord \| None` | `IdempotencyValidationError`, `IdempotencyStorageError`, `IdempotencyError` |
| `save(record)` | `None` | `IdempotencyKeyCollisionError`, `IdempotencyValidationError`, `IdempotencyStorageError`, `IdempotencyError` |
| `delete(operation, idempotency_key)` | `bool` | `IdempotencyValidationError`, `IdempotencyStorageError` |
| `get_many(operation, idempotency_keys)` | `dict[str, IdempotencyRecord]` | as `get`; only found keys appear |
| `save_many(records, *, rollback_on_error=False)` | `None` | as `save`; the collision carries `list[str]` |
| `delete_many(operation, idempotency_keys)` | `int` | as `delete` |

`RedisAsyncIdempotencyRepository(redis, *, key_prefix="idempotency:", metrics=None)` is the
implementation: `SET key value EX ceil(record.ttl_seconds) NX` for `save`, `GET` for `get`,
`MGET` for `get_many`, and a non-transactional pipeline for `save_many` so it works on Redis
Cluster. It deletes any record it reads back expired and reports that as a miss.

### Metrics

`IdempotencyMetricsProtocol` is `record_hit(operation)`, `record_miss(operation)`,
`record_collision(operation)`, `record_error(operation, error_type)`,
`record_latency(operation, method, duration_seconds)`, `record_bulk_hit(operation, count)`
and `record_bulk_miss(operation, count)`. `PrometheusIdempotencyMetrics(prefix=None)` emits
`idempotency_operations_total{operation,status}` and
`idempotency_operation_duration_seconds{operation,method}`.

### Settings and Dishka

`BaseIdempotencySettings` is a plain Pydantic model with `enabled=True`, `key_prefix`
(required, no default), `metrics_enabled=False`, `default_ttl_minutes=60`,
`min_ttl_seconds=1`, `max_ttl_seconds=2592000` and `operation_ttls={}`. It satisfies
`IdempotencySettingsProtocol`, which is what the providers ask for.

```python
from dishka import make_async_container

from idempotency_kit.dishka import (
    AsyncIdempotencyCoordinatorProvider,
    AsyncRedisIdempotencyProvider,
    IdempotencyProvider,
)

container = make_async_container(
    MyRedisProvider(),          # provides redis.asyncio.Redis
    MySettingsProvider(),       # provides IdempotencySettingsProtocol
    IdempotencyProvider(),      # IdempotencyDomainService + IdempotencyMetricsProtocol
    AsyncRedisIdempotencyProvider(),
    AsyncIdempotencyCoordinatorProvider(),
)
```

All three are `Scope.APP`. `IdempotencyProvider` gives one metrics collector to the whole
process — `PrometheusIdempotencyMetrics` when `settings.metrics_enabled`, a no-op otherwise.
Another backend goes in with `@provide(override=True)` in a provider listed after it.

## Rules that hold or break the code

1. **The key is the whole identity; the arguments are not.** Nothing hashes the request
   body. Two calls with the same `operation` and `idempotency_key` and different payloads
   replay the first result. A key must be unique per intended effect, and one client request
   must not reuse a key across two different operations' worth of work.
2. **Two concurrent callers both run your business logic.** There is no lock and no
   in-flight reservation: the check is a `GET`, the write is `SET NX`. Both miss, both
   execute, the loser's `save` collides, and it re-reads and returns the winner's record.
   The callers see one result; the side effects happened twice. Make the action safe to run
   twice — a database upsert, an outbox row keyed by the same key — or take your own lock.
3. **The decorator reads the key from keyword arguments only.** `kwargs.get(key_param)`.
   Passed positionally, the key is invisible, the function runs unprotected, and nothing is
   logged. Declare the parameter keyword-only.
4. **A falsy key means no idempotency.** `None` and `""` both short-circuit straight to the
   action, in the decorator and in `coordinate()`.
5. **A coordinator the decorator cannot find means no idempotency, silently.** It looks for
   `infra_param` by name in `kwargs` then as an attribute of the first positional argument,
   then for an `AsyncIdempotencyCoordinator` by type in `kwargs`, in `args`, and in the
   instance dictionary of every positional argument. Finding none, it just calls the
   function — no exception, no log line. Pass `infra_param=` so a renamed attribute fails
   loudly in review instead of quietly at runtime.
6. **`coordinate()` never raises for storage trouble.** A Redis failure on read is counted
   as `storage_get_error` and treated as a miss; a failure on write is `storage_save_error`
   and the fresh result is returned uncached. Availability over exactly-once, deliberately.
   Repository methods called directly do raise — only the coordinator and the decorator
   swallow.
7. **A decode failure is a miss, every time.** If the adapter cannot decode a stored record,
   the coordinator logs and runs the action again. Changing the adapter or the DTO's shape
   while records are live re-executes the operation for every caller until those records
   expire.
8. **Failures are not cached.** An exception from the action propagates and nothing is
   written, so the next call with the same key runs the action again.
9. **TTLs are given in seconds and truncated to whole minutes, with a floor of one.**
   `max(1, ttl_seconds // 60)`. `ttl_seconds=3600` is an hour; `ttl_seconds=90` is one
   minute; `ttl_seconds=30` is one minute. There is no sub-minute record.
10. **`operation_ttls` wins over the decorator, and a zero there is not a value.** The
    coordinator resolves `self._operation_ttls.get(operation) or ttl_seconds`, so an entry
    of `0` falls through to the decorator's number rather than meaning "no TTL".
11. **The domain service's bounds decide what is storable, and the shipped settings widen
    them.** `IdempotencyDomainService()` alone is 30 minutes by default, floor 60 seconds,
    ceiling 86400. `BaseIdempotencySettings` defaults to 60 minutes, floor 1 second, ceiling
    30 days, and the Dishka provider builds the service from those. Out of range raises
    `IdempotencyInvalidTTLError`, which the coordinator catches: the operation is simply not
    cached, and the caller gets its result anyway.
12. **Neither identifier may contain a colon**, both are stripped of surrounding whitespace,
    and the lengths are 100 for `operation` and 255 for `idempotency_key`. The Redis
    repository re-validates on every call, so an over-long key raises there too.
13. **`PydanticResultAdapter` cannot represent an absent result.** `encode` returns `None`
    for a falsy value and `decode` raises on a falsy payload, so a function that may return
    `None` stores `null` and then fails to decode it forever — rule 7, permanently. Use
    `VoidResultAdapter` when the action returns `None` and `JsonResultAdapter` when it may.
14. **What is stored has to be a JSON value.** `IdempotencyRecord.result` is Pydantic's
    `JsonValue` and `JsonResultAdapter` passes the value through untouched, so a `datetime`,
    a `Decimal` or a `set` fails record validation, is logged as
    `record_validation_error`, and the operation goes uncached without raising.
15. **The Redis backend needs `redis` and `orjson` together** — that is the `redis` extra.
    The constructor raises `ImportError` when either is missing.
16. **`save_many` is one operation's batch and is not atomic.** Every record must carry the
    same `operation` as `records[0]`, the pipeline is non-transactional for cluster
    compatibility, and partial writes stay unless `rollback_on_error=True`. A collision
    anywhere raises `IdempotencyKeyCollisionError` whose `key` is a `list[str]`.
17. **`PrometheusIdempotencyMetrics` is one instance per process.** It registers its
    collectors in the constructor; a second instance with the same prefix raises from
    `prometheus_client`.
18. **Both layers count.** The repository and the coordinator each record hit, miss and
    latency, so one collector shared between them — which is what the Dishka providers wire
    — counts every coordinator-driven get twice.
19. **Async only.** There is no sync mirror, and no `__init__.py` name that gives you one.

## Common mistakes

```python
# WRONG — the key arrives positionally, so the decorator never sees it and the
# function runs unprotected on every call
@async_idempotent(operation="order.create", adapter=PydanticResultAdapter(OrderDTO))
async def execute(self, dto: CreateOrderDTO, idempotency_key: str | None = None) -> OrderDTO: ...

await use_case.execute(dto, key)

# RIGHT — keyword-only, so it cannot be passed any other way
@async_idempotent(operation="order.create", adapter=PydanticResultAdapter(OrderDTO))
async def execute(self, dto: CreateOrderDTO, *, idempotency_key: str | None = None) -> OrderDTO: ...

await use_case.execute(dto, idempotency_key=key)
```

```python
# WRONG — a Pydantic adapter on an action that may return nothing: the record
# stores null, decode raises, and the action re-runs on every replay
@async_idempotent(operation="user.deactivate", adapter=PydanticResultAdapter(UserDTO))
async def deactivate(self, *, idempotency_key: str | None = None) -> UserDTO | None: ...

# RIGHT — pick the adapter for the shape the action actually returns
@async_idempotent(operation="user.deactivate", adapter=VoidResultAdapter())
async def deactivate(self, *, idempotency_key: str | None = None) -> None: ...

@async_idempotent(operation="user.find", adapter=JsonResultAdapter())
async def find(self, *, idempotency_key: str | None = None) -> dict | None: ...
```

```python
# WRONG — treating the decorator as a mutex: both concurrent callers reach this body
@async_idempotent(operation="payment.charge", adapter=PydanticResultAdapter(ChargeDTO))
async def charge(self, dto, *, idempotency_key: str | None = None) -> ChargeDTO:
    return await self._psp.charge(dto)            # charged twice

# RIGHT — the action is safe to run twice, or the provider deduplicates on the same key
async def charge(self, dto, *, idempotency_key: str | None = None) -> ChargeDTO:
    return await self._psp.charge(dto, idempotency_key=idempotency_key)
```

```python
# WRONG — expecting the coordinator to tell you Redis is down
try:
    result = await coordinator.coordinate("order.create", key, 3600, adapter, action, dto)
except IdempotencyStorageError:
    ...                                            # never reached

# RIGHT — the coordinator degrades to running the action; watch the metrics and the
# logs for it, or call the repository yourself when you need the failure
record = await repository.get("order.create", key)  # this one raises
```

```python
# WRONG — seconds that quietly become one minute
@async_idempotent(operation="otp.send", adapter=VoidResultAdapter(), ttl_seconds=30)

# RIGHT — the floor is a minute; say so
@async_idempotent(operation="otp.send", adapter=VoidResultAdapter(), ttl_seconds=300)
```

```python
# WRONG — not exported from the root
from idempotency_kit import RedisAsyncIdempotencyRepository, PrometheusIdempotencyMetrics

# RIGHT
from idempotency_kit.infra.storage.redis.aio import RedisAsyncIdempotencyRepository
from idempotency_kit.infra.metrics.prometheus import PrometheusIdempotencyMetrics
```

## Errors

All derive from `IdempotencyError`, which is exported alongside them.

| Exception | Constructed as | Means |
|---|---|---|
| `IdempotencyError` | `(message)` | the base, and what a corrupted stored payload raises |
| `IdempotencyKeyCollisionError` | `(operation, key)` | `save` found the key already there; `key` is a `str`, or a `list[str]` from `save_many`. Carries `.operation` and `.key` |
| `IdempotencyRecordExpiredError` | `(operation, key)` | `validate_record` was given an expired record. Nothing in the library calls it — it is for your own code |
| `IdempotencyStorageError` | `(message, operation=None, original_error=None)` | the backend failed. Carries `.operation` and `.original_error` |
| `IdempotencyValidationError` | `(message, errors=None)` | an identifier or a result failed validation. `.errors` holds the Pydantic error list when there is one |
| `IdempotencyInvalidTTLError` | `(ttl_seconds, min_ttl, max_ttl)` | the TTL is outside the domain service's range. Carries all three |

Through `coordinate()` and the decorator none of these reach the caller: the collision is
resolved into the winner's result, and every other one is logged, counted and swallowed.
They are the contract of the repository and the domain service, which is where you meet them
if you drive those directly.

## Documentation map

Fetch a page when the task is the one named beside it.

| Page | Read it when |
|---|---|
| [Home](index.md) | placing the library — what it is for, the four shapes of caller |
| [Quick Start](quickstart.md) | the first integration, and what makes a good key |
| [User Guide](user_guide.md) | bulk operations, graceful degradation, Dishka wiring, worked services |
| [Architecture](architecture.md) | the layers, the request-flow diagrams, the cluster reasoning |
| [API Reference](api_reference.md) | an exact field, default or constructor argument |
| [Testing](testing_conventions.md) | writing tests against this library, or contributing to it |
| [Changelog](changelog.md) | what changed between versions |
