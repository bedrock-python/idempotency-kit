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
result on a repeat call, hold the key while the first caller's action runs — a second caller
that arrives with the same key in that window waits for the first one's result, or is
refused, instead of running the action too — and, given a fingerprint of the request, refuse
a key that comes back for a different request instead of replaying the first one's result.
It ships a Redis repository, a metrics protocol with a Prometheus implementation, a decorator
that hides the whole flow, and Dishka providers that wire the pieces together.

**It does not** derive the key — the caller supplies it, and the request body is not part of
it unless you name the parameters that fingerprint the request; it does not roll anything
back; it does not cache failures; it does not retry; it has no sync API; and it stores
nothing but JSON. The reservation is a lease, not a lock: an action
that outlives its lease can run twice, and when storage is down the action runs unreserved.
It is a result cache with an in-flight reservation, not a distributed transaction.

## Mental model

Four nouns and one flow.

* **`IdempotencyRecord`** — a frozen Pydantic model: `operation`, `idempotency_key`, the
  JSON `result`, `created_at`, `expires_at`, a `status` that is `"completed"` for a stored
  result and `"pending"` for an in-flight reservation, and an optional `fingerprint` of the
  request the record was made for. `expires_at` is what decides whether a record is still a
  hit; on a pending record it is the lease.
* **`AsyncIdempotencyRepository`** — the storage protocol: `get` / `save` / `replace` /
  `delete` and the bulk twins of `get`, `save` and `delete`. `save` is a write that fails if
  the key is already there; that failure, `IdempotencyKeyCollisionError`, is how a
  reservation is contested. `replace` writes whether or not the key is there; it is how a
  reservation becomes a result. `RedisAsyncIdempotencyRepository` is the only
  implementation that ships.
* **`ResultAdapter`** — `encode` a return value into JSON, `decode` it back. Three ship:
  `PydanticResultAdapter(model_class)`, `JsonResultAdapter()`, `VoidResultAdapter()`.
* **`AsyncIdempotencyCoordinator`** — the flow: reserve the key by writing a pending
  record under `SET NX` with the lease as its TTL. If that succeeds, run the action, encode
  the result and `replace` the reservation with the completed record. If it fails, read
  what holds the key: a record whose `fingerprint` differs from the caller's raises
  `IdempotencyKeyReuseError`, pending or not; a completed record is decoded and returned; a
  pending one means another caller is in flight, and `in_flight` decides — `"wait"` polls
  until the record arrives, `"raise"` raises `IdempotencyInProgressError`, `"run"` is the
  old flow of read, run, `SET NX` and adopt the winner's result on a collision. A record whose `expires_at`
  has passed is a miss even if the repository handed it back; an expired lease is an
  abandoned reservation. An action that raises deletes its reservation. Storage and decode
  failures are swallowed and the action runs, which is the deliberate trade of exactly-once
  for availability.

`IdempotencyDomainService` sits between the coordinator and the record: it applies the TTL
bounds, builds the pending record for a reservation, and turns Pydantic validation errors
into `IdempotencyValidationError`.
`@async_idempotent` is the same flow as a decorator — it finds the key in the call's
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
        fingerprint_params=("dto",),
    )
    async def execute(self, dto: CreateOrderDTO, *, idempotency_key: str | None = None) -> OrderDTO:
        order = await self._orders.create(dto)
        return OrderDTO.from_entity(order)
```

`idempotency_key` is keyword-only on purpose: nothing else can land in it by position, and
the call site has to name it. The decorator reads it from the keyword arguments, or from the
positional ones when the parameter can be passed that way. `infra_param="coordinator"` names
the attribute rather than leaving the decorator to find a coordinator by type.
`fingerprint_params=("dto",)` makes `dto` part of what the key identifies: the same key with
a different `dto` raises `IdempotencyKeyReuseError` instead of replaying the first order.

The same call without the decorator — the five leading arguments are positional-only, and
everything after them is forwarded to the action except `idempotency_fingerprint`:

```python
result = await coordinator.coordinate(
    "order.create",                      # operation
    idempotency_key,                     # None or "" means: just run the action
    3600,                                # ttl_seconds, or None for the service default
    PydanticResultAdapter(OrderDTO),
    self.execute_uncached,               # the action
    dto,                                 # *args and **kwargs go to the action
    idempotency_fingerprint=fingerprint_of(dto=dto),   # optional; None means key only
)
```

## The API

### Exported from `idempotency_kit`

| Name | Signature | What it is |
|---|---|---|
| `async_idempotent` | `(operation, adapter, ttl_seconds=None, key_param="idempotency_key", infra_param=None, fingerprint_params=None)` | decorator for an async function or method; `fingerprint_params` names the parameters whose values identify the request |
| `fingerprint_of` | `(**values)` | SHA-256 hex of the JSON form of the named values, keys sorted; what the decorator computes from `fingerprint_params`, for a caller of `coordinate()` |
| `AsyncIdempotencyCoordinator` | `(repository, domain_service, operation_ttls=None, metrics=None, enabled=True, in_flight="wait", in_flight_lease_seconds=30)` | the flow; `operation_ttls` is `dict[str, int]` in seconds; `enabled=False` runs the action and nothing else; `in_flight` is `"wait"`, `"raise"` or `"run"` |
| `IdempotencyDomainService` | `(*, default_ttl_minutes=60, min_ttl_seconds=60, max_ttl_seconds=2592000)` | record factory and TTL bounds; keyword-only, defaults from `core.constants` |
| `IdempotencyRecord` | frozen Pydantic model | the cached result |
| `IdempotencyIdentifiers` | Pydantic model | `operation` + `idempotency_key`, and the rules they obey |
| `AsyncIdempotencyRepository` | runtime-checkable `Protocol` | storage contract |
| `ResultAdapter` | `Protocol[T]` | `encode(value) -> Any`, `decode(data) -> T` |
| `PydanticResultAdapter` | `(model_class)` | `model_dump(mode="json")` out, `model_validate` back |
| `JsonResultAdapter` | `()` | passes the value through untouched |
| `VoidResultAdapter` | `()` | stores JSON `null`, decodes back to `None` |
| `IdempotencyMetricsProtocol` | runtime-checkable `Protocol` | metrics contract |
| `NoOpIdempotencyMetrics` | `()` | the default collector |
| `IdempotencyError` and its seven subclasses | | see [Errors](#errors) |

### Not exported from the root

| Name | Import from |
|---|---|
| `RedisAsyncIdempotencyRepository` | `idempotency_kit.infra.storage.redis.aio` |
| `PrometheusIdempotencyMetrics` | `idempotency_kit.infra.metrics.prometheus` |
| `BaseIdempotencySettings` | `idempotency_kit.settings` |
| `IdempotencyProvider`, `AsyncIdempotencyCoordinatorProvider`, `AsyncRedisIdempotencyProvider`, `IdempotencySettingsProtocol` | `idempotency_kit.dishka` |
| `MAX_KEY_LENGTH`, `MAX_OPERATION_LENGTH`, `DEFAULT_TTL_MINUTES`, `MIN_TTL_SECONDS`, `MAX_TTL_SECONDS`, `InFlightMode`, `DEFAULT_IN_FLIGHT_MODE`, `DEFAULT_IN_FLIGHT_LEASE_SECONDS`, `IN_FLIGHT_POLL_INTERVAL_SECONDS` | `idempotency_kit.core.constants` |

### Coordinator and domain service

| Method | Returns | Notes |
|---|---|---|
| `AsyncIdempotencyCoordinator.coordinate(operation, idempotency_key, ttl_seconds, adapter, action, /, *args, idempotency_fingerprint=None, **kwargs)` | `T` | never raises for storage or decode trouble; raises `IdempotencyInProgressError` when the key is in flight and `in_flight` says so, `IdempotencyKeyReuseError` when the record's fingerprint differs from `idempotency_fingerprint` |
| `IdempotencyDomainService.create_record(operation, idempotency_key, result, *, ttl_minutes=None, fingerprint=None)` | `IdempotencyRecord` | raises `IdempotencyInvalidTTLError`, `IdempotencyValidationError` |
| `IdempotencyDomainService.create_pending_record(operation, idempotency_key, *, lease_seconds, fingerprint=None)` | `IdempotencyRecord` | the reservation; not held to the TTL bounds; raises `IdempotencyValidationError` |
| `IdempotencyDomainService.validate_record(record)` | `None` | raises `IdempotencyRecordExpiredError`; the coordinator calls it on every record it reads |

### Record

| Member | Type | Notes |
|---|---|---|
| `operation` | `str` | 1-100 chars, stripped, no `:` |
| `idempotency_key` | `str` | 1-255 chars, stripped, no `:` |
| `result` | `JsonValue` | whatever the adapter encoded; `null` for a void result |
| `created_at` / `expires_at` | `datetime` | UTC, set by `create` and `pending`; on a pending record `expires_at` is the lease |
| `status` | `"pending" \| "completed"` | `"completed"` unless said otherwise, which is how a record written before the field existed reads |
| `fingerprint` | `str \| None` | what the caller said the request was; `None` — also what a record written before the field existed reads as — never raises |
| `IdempotencyRecord.create(operation, idempotency_key, result, ttl_seconds, fingerprint=None)` | `IdempotencyRecord` | classmethod; `ttl_seconds` is a `float` here |
| `IdempotencyRecord.pending(operation, idempotency_key, lease_seconds, fingerprint=None)` | `IdempotencyRecord` | classmethod; the reservation, `result` is `null` |
| `.is_pending` | `bool` | `status == "pending"` |
| `.is_expired` | `bool` | `now >= expires_at` |
| `.ttl_seconds` | `float` | remaining, `0.0` once expired |

### Repository protocol

| Method | Returns | Raises |
|---|---|---|
| `get(operation, idempotency_key)` | `IdempotencyRecord | None` | `IdempotencyValidationError`, `IdempotencyStorageError`, `IdempotencyError` |
| `save(record)` | `None` | `IdempotencyKeyCollisionError`, `IdempotencyValidationError`, `IdempotencyStorageError`, `IdempotencyError` |
| `replace(record)` | `None` | as `save` minus the collision: it writes over whatever is there |
| `delete(operation, idempotency_key)` | `bool` | `IdempotencyValidationError`, `IdempotencyStorageError` |
| `get_many(operation, idempotency_keys)` | `dict[str, IdempotencyRecord]` | as `get`; only found keys appear |
| `save_many(records, *, rollback_on_error=False)` | `None` | as `save`; the collision carries `list[str]` |
| `delete_many(operation, idempotency_keys)` | `int` | as `delete` |

`RedisAsyncIdempotencyRepository(redis, *, key_prefix="idempotency:", metrics=None)` is the
implementation: `SET key value EX ceil(record.ttl_seconds) NX` for `save`, the same without
`NX` for `replace`, `GET` for `get`, `MGET` for `get_many`, and a non-transactional pipeline
for `save_many` so it works on Redis Cluster. It deletes any record it reads back expired and
reports that as a miss. A repository of your own needs `replace` too: the coordinator raises
`TypeError` at construction without it, unless `in_flight="run"`.

### Metrics

`IdempotencyMetricsProtocol` is `record_hit(operation)`, `record_miss(operation)`,
`record_collision(operation)`, `record_error(operation, error_type)`,
`record_latency(operation, method, duration_seconds)`, `record_bulk_hit(operation, count)`
and `record_bulk_miss(operation, count)`. `PrometheusIdempotencyMetrics(prefix=None)` emits
`idempotency_operations_total{operation,status}` and
`idempotency_operation_duration_seconds{operation,method}`.

Each metric has one owner, so the same collector can go to both layers: the coordinator
records hit, miss, collision and the latency of `get`, `reserve` and `save`; the repository
records errors, the bulk hit and miss counts of `get_many`, and the latency of `delete` and
`get_many`.

A collision is two callers on one key at the same time. With a reservation it is what the
second caller records on finding the pending record: a waiter then records a hit when the
result arrives, a refused caller records nothing more and raises. In `"run"` mode it is the
loser's `SET NX` failing after both ran, as before. The error types the coordinator reports
are `storage_get_error`, `storage_reserve_error`, `storage_save_error`,
`storage_release_error`, `record_validation_error` and `key_reuse`.

### Settings and Dishka

`BaseIdempotencySettings` is a plain Pydantic model with `enabled=True`, `key_prefix`
(required, no default), `metrics_enabled=False`, `default_ttl_minutes=60`,
`min_ttl_seconds=60`, `max_ttl_seconds=2592000`, `operation_ttls={}`, `in_flight="wait"`
and `in_flight_lease_seconds=30` — the TTL and in-flight fields default to the
`core.constants` values, which is also what `IdempotencyDomainService` and the coordinator
use. It satisfies `IdempotencySettingsProtocol`, which is what the providers ask for.

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
`settings.enabled`, `settings.in_flight` and `settings.in_flight_lease_seconds` reach the
coordinator: `enabled=False` makes it a pass-through. A settings object written before those
fields existed is read as enabled, `"wait"` and 30 seconds.

## Rules that hold or break the code

1. **The key is the whole identity; the arguments are not, unless you say which ones are.**
   Nothing hashes the request body on its own: two calls with the same `operation` and
   `idempotency_key` and different payloads replay the first result. Name the parameters
   that identify the request — `fingerprint_params=("dto",)` on the decorator, or
   `idempotency_fingerprint=fingerprint_of(dto=dto)` on `coordinate()` — and the fingerprint
   is stored with the record; the same key back with a different fingerprint raises
   `IdempotencyKeyReuseError` (422 in HTTP terms) instead of replaying, from a completed
   record and from a pending one alike, before the action runs. Either side without a
   fingerprint means no comparison: a record written without one, or before the field
   existed, never raises, and a caller without one gets the key-only behaviour. A key must
   still be unique per intended effect; the fingerprint is the guard for when it is not.
2. **A second caller with the same key does not run your business logic while the first is
   in flight — unless you ask for that.** The coordinator reserves the key with a pending
   record (`SET NX`, TTL `in_flight_lease_seconds`, 30 s by default) before the action and
   writes the result over it after. A second caller that finds the reservation waits for the
   result with `in_flight="wait"`, the default — polling every 50 ms, giving up with
   `IdempotencyInProgressError` after a whole lease — or is refused at once with
   `in_flight="raise"`, which an HTTP layer maps to 409. `in_flight="run"` is the flow from
   before reservations existed: both run, the loser's `SET NX` collides and it adopts the
   winner's result; the callers see one result and the side effect happened twice, so keep
   it for actions that are genuinely safe to repeat. The reservation is a lease, not a lock:
   a pending record past its lease counts as absent and the next caller runs the action, so
   `in_flight_lease_seconds` has to be longer than the action can ever take.
3. **Declare the key keyword-only anyway.** The decorator reads `key_param` from the
   keyword arguments, and from the positional arguments when the parameter can be passed
   that way. Keyword-only is still the shape to write: nothing can land in it by position,
   and the call site has to name it.
4. **A falsy key means no idempotency.** `None` and `""` both short-circuit straight to the
   action, in the decorator and in `coordinate()`.
5. **A coordinator the decorator cannot find means no idempotency.** It looks for
   `infra_param` by name in `kwargs` then as an attribute of the first positional argument,
   then for an `AsyncIdempotencyCoordinator` by type in `kwargs`, in `args`, and in the
   instance dictionary of every positional argument. Finding none, it logs a `WARNING` from
   `idempotency_kit.core.decorators.aio.idempotent` and calls the function anyway — the
   operation stays available, unprotected. Pass `infra_param=` so a renamed attribute is one
   grep away, and alert on that warning.
6. **`coordinate()` never raises for storage trouble.** A Redis failure on read is counted
   as `storage_get_error` and treated as a miss; a failure on the reservation is
   `storage_reserve_error` and the action runs unreserved; a failure on write is
   `storage_save_error` and the fresh result is returned uncached. Availability over
   exactly-once, deliberately. Repository methods called directly do raise — only the
   coordinator and the decorator swallow. The one thing they do raise is
   `IdempotencyInProgressError`, which is about the caller's request, not about storage.
7. **A decode failure is a miss, every time.** If the adapter cannot decode a stored record,
   the coordinator logs and runs the action again. Changing the adapter or the DTO's shape
   while records are live re-executes the operation for every caller until those records
   expire.
8. **Failures are not cached.** An exception from the action propagates, nothing is
   written, and the reservation is deleted — cancellation included — so the next call with
   the same key runs the action again. If the result cannot be stored (rules 11, 13, 14) the
   reservation is deleted too, rather than holding retries for a record that never comes.
9. **TTLs are given in seconds and truncated to whole minutes, with a floor of one.**
   `max(1, ttl_seconds // 60)`. `ttl_seconds=3600` is an hour; `ttl_seconds=90` is one
   minute; `ttl_seconds=30` is one minute. There is no sub-minute record.
10. **`operation_ttls` wins over the decorator, and a zero there is not a value.** The
    coordinator resolves `self._operation_ttls.get(operation) or ttl_seconds`, so an entry
    of `0` falls through to the decorator's number rather than meaning "no TTL".
11. **The domain service's bounds decide what is storable.** `IdempotencyDomainService()`
    and `BaseIdempotencySettings` agree on them: 60 minutes by default, floor 60 seconds,
    ceiling 30 days, all three from `idempotency_kit.core.constants`. Out of range raises
    `IdempotencyInvalidTTLError`, which the coordinator catches: the operation is simply not
    cached, and the caller gets its result anyway.
12. **Neither identifier may contain a colon**, both are stripped of surrounding whitespace,
    and the lengths are 100 for `operation` and 255 for `idempotency_key`. The Redis
    repository re-validates on every call, so an over-long key raises there too.
13. **`PydanticResultAdapter` cannot represent an absent result.** `encode` raises
    `IdempotencyValidationError` when handed `None` rather than storing a `null` it could
    never decode again; the coordinator counts that as `record_validation_error`, logs it,
    and leaves the operation uncached. Use `VoidResultAdapter` when the action returns
    `None` and `JsonResultAdapter` when it may.
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
18. **Each metric has one owner.** The coordinator records hit, miss, collision and the
    latency of `get`, `reserve` and `save`; the repository records errors, the bulk hit and
    miss counts of `get_many`, and the latency of `delete` and `get_many`. One collector
    shared between them — which is what the Dishka providers wire — counts every operation
    once: a waited call is one collision and one hit, a reserved run is one miss.
19. **Async only.** There is no sync mirror, and no `__init__.py` name that gives you one.
20. **`enabled=False` switches the whole thing off.** The coordinator runs the action and
    nothing else: no read, no write, no metric. The Dishka providers pass `settings.enabled`
    through to it.
21. **A repository of your own needs `replace`.** The reservation goes in through `save`
    and comes out through `replace`; without it every retry within the lease would wait for,
    or be refused over, a result that is never written. The coordinator raises `TypeError`
    at construction when the repository lacks it and `in_flight` is not `"run"`.
22. **Finish a rolling upgrade before relying on the reservation.** An instance on a version
    without `status` reads a pending record as a completed one with a `null` result:
    `PydanticResultAdapter` cannot decode that and runs the action, which is the old
    behaviour, but `JsonResultAdapter` and `VoidResultAdapter` replay the `None`. Roll out
    with `in_flight="run"` and switch once every instance is on the new version, or accept
    the window.
23. **Fingerprint what identifies the request, not what varies between retries.** The
    decorator binds the call to the signature with defaults applied, so an argument passed
    at its default and one left out agree, turns the named values into their JSON form
    (Pydantic models, dataclasses, UUIDs, datetimes and Decimals included), sorts keys and
    hashes. A timestamp, a trace id or `self` in `fingerprint_params` makes every honest
    retry a key reuse. A name the function does not have raises `TypeError` at decoration;
    a value with no JSON form raises `PydanticSerializationError` at call time.
24. **`idempotency_fingerprint` is the coordinator's keyword, not the action's.** It is the
    one keyword `coordinate()` keeps for itself, so an action cannot have a parameter of
    that name; the decorator passes it only when `fingerprint_params` is set. In
    `in_flight="run"` mode the fingerprint is checked on the read before the action; a
    mismatch discovered on the collision after the action is logged and counted as
    `key_reuse`, and the caller keeps its own result, because the side effect has happened.

## Common mistakes

```python
# WRONG — infra_param names an attribute that no longer exists, so no coordinator is
# found: the call runs unprotected and only a WARNING says so
class CreateOrder:
    def __init__(self, idempotency: AsyncIdempotencyCoordinator) -> None:
        self._idempotency = idempotency

    @async_idempotent(operation="order.create", adapter=..., infra_param="coordinator")
    async def execute(self, dto: CreateOrderDTO, *, idempotency_key: str | None = None) -> OrderDTO: ...

# RIGHT — name the attribute that is actually there
    @async_idempotent(operation="order.create", adapter=..., infra_param="_idempotency")
    async def execute(self, dto: CreateOrderDTO, *, idempotency_key: str | None = None) -> OrderDTO: ...
```

```python
# WRONG — a Pydantic adapter on an action that may return nothing: the adapter refuses
# to encode the None, so the operation is never cached and the action re-runs every time
@async_idempotent(operation="user.deactivate", adapter=PydanticResultAdapter(UserDTO))
async def deactivate(self, *, idempotency_key: str | None = None) -> UserDTO | None: ...

# RIGHT — pick the adapter for the shape the action actually returns
@async_idempotent(operation="user.deactivate", adapter=VoidResultAdapter())
async def deactivate(self, *, idempotency_key: str | None = None) -> None: ...

@async_idempotent(operation="user.find", adapter=JsonResultAdapter())
async def find(self, *, idempotency_key: str | None = None) -> dict | None: ...
```

```python
# WRONG — in_flight="run" on an action that must not happen twice: both concurrent callers
# reach this body, and only the responses are deduplicated
coordinator = AsyncIdempotencyCoordinator(repository, service, in_flight="run")

@async_idempotent(operation="payment.charge", adapter=PydanticResultAdapter(ChargeDTO))
async def charge(self, dto, *, idempotency_key: str | None = None) -> ChargeDTO:
    return await self._psp.charge(dto)            # charged twice

# RIGHT — leave the default, and the second caller gets the first one's charge; or refuse it
coordinator = AsyncIdempotencyCoordinator(repository, service)                    # waits
coordinator = AsyncIdempotencyCoordinator(repository, service, in_flight="raise") # 409

try:
    return await use_case.charge(dto, idempotency_key=key)
except IdempotencyInProgressError:
    raise HTTPException(409, detail="a request with this Idempotency-Key is still being processed")
```

```python
# WRONG — a lease the action can outlive: the reservation expires mid-run, a retry at
# second six finds the key free, and the provider is charged twice after all
coordinator = AsyncIdempotencyCoordinator(repository, service, in_flight_lease_seconds=5)

async def charge(self, dto, *, idempotency_key: str | None = None) -> ChargeDTO:
    return await self._psp.charge(dto, timeout=20)

# RIGHT — the lease outlives the action's worst case, timeouts and retries included
coordinator = AsyncIdempotencyCoordinator(repository, service, in_flight_lease_seconds=60)
```

```python
# WRONG — a key derived from the order, reused for a second, different request on the same
# order: the first charge is replayed and nothing says so
@async_idempotent(operation="payment.charge", adapter=PydanticResultAdapter(ChargeDTO))
async def charge(self, dto: ChargeDTO, *, idempotency_key: str | None = None) -> ChargeDTO: ...

await charge(ChargeDTO(amount=1999), idempotency_key=f"order-{order.id}")
await charge(ChargeDTO(amount=5), idempotency_key=f"order-{order.id}")     # -> the 1999 charge

# RIGHT — name what identifies the request, and the second call is refused before it runs
@async_idempotent(
    operation="payment.charge", adapter=PydanticResultAdapter(ChargeDTO), fingerprint_params=("dto",)
)
async def charge(self, dto: ChargeDTO, *, idempotency_key: str | None = None) -> ChargeDTO: ...

try:
    return await use_case.charge(dto, idempotency_key=key)
except IdempotencyKeyReuseError:
    raise HTTPException(422, detail="this Idempotency-Key was already used for a different request")
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
| `IdempotencyRecordExpiredError` | `(operation, key)` | `validate_record` was given an expired record. The coordinator raises it internally and turns it into a miss; through the repository or your own call to `validate_record` you meet it directly |
| `IdempotencyInProgressError` | `(operation, key)` | another call with the same key is still running its action. `coordinate()` and the decorator raise it at once with `in_flight="raise"`, and after a whole lease of waiting with `"wait"`. Carries `.operation` and `.key` |
| `IdempotencyKeyReuseError` | `(operation, key, stored_fingerprint, fingerprint)` | the record under the key was made for a different request. `coordinate()` and the decorator raise it before the action runs, from a completed record and from a pending one. Carries all four |
| `IdempotencyStorageError` | `(message, operation=None, original_error=None)` | the backend failed. Carries `.operation` and `.original_error` |
| `IdempotencyValidationError` | `(message, errors=None)` | an identifier or a result failed validation. `.errors` holds the Pydantic error list when there is one |
| `IdempotencyInvalidTTLError` | `(ttl_seconds, min_ttl, max_ttl)` | the TTL is outside the domain service's range. Carries all three |

Through `coordinate()` and the decorator only `IdempotencyInProgressError` and
`IdempotencyKeyReuseError` reach the caller — both are about the request, not about storage:
the collision is resolved into a wait or the winner's result, and every other one is logged,
counted and swallowed. The rest are the contract of the repository and the domain
service, which is where you meet them if you drive those directly.

## Documentation map

Fetch a page when the task is the one named beside it.

| Page | Read it when |
|---|---|
| [Home](index.md) | placing the library — what it is for, the four shapes of caller |
| [Quick Start](quickstart.md) | the first integration, and what makes a good key |
| [User Guide](user_guide.md) | in-flight handling and the lease, fingerprints and key reuse, bulk operations, graceful degradation, Dishka wiring, worked services |
| [Architecture](architecture.md) | the layers, the request-flow diagrams, the cluster reasoning |
| [API Reference](api_reference.md) | an exact field, default or constructor argument |
| [Testing](testing_conventions.md) | writing tests against this library, or contributing to it |
| [Changelog](changelog.md) | what changed between versions |
