import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Generic, TypeVar

from idempotency_kit.core.constants import (
    DEFAULT_IN_FLIGHT_LEASE_SECONDS,
    DEFAULT_IN_FLIGHT_MODE,
    IN_FLIGHT_POLL_INTERVAL_SECONDS,
    InFlightMode,
)
from idempotency_kit.core.exceptions import (
    IdempotencyInProgressError,
    IdempotencyInvalidTTLError,
    IdempotencyKeyCollisionError,
    IdempotencyKeyReuseError,
    IdempotencyRecordExpiredError,
    IdempotencyValidationError,
)
from idempotency_kit.core.models.entities import IdempotencyRecord
from idempotency_kit.core.protocols.adapter import ResultAdapter
from idempotency_kit.core.protocols.aio.repository import AsyncIdempotencyRepository
from idempotency_kit.core.protocols.metrics import IdempotencyMetricsProtocol, NoOpIdempotencyMetrics
from idempotency_kit.core.services.domain import IdempotencyDomainService

T = TypeVar("T")

logger = logging.getLogger(__name__)

_IN_FLIGHT_MODES: tuple[InFlightMode, ...] = ("wait", "raise", "run")


@dataclass(frozen=True)
class _Hit(Generic[T]):
    """A decoded cached result.

    Wrapping it keeps hit/miss a property of the *record*: an adapter may
    legitimately decode to ``None`` (``VoidResultAdapter``, a stored JSON
    ``null``), which a bare ``None`` sentinel would misread as a miss.
    """

    value: T


class _Lookup(Enum):
    """What a read found under the key when it was not a usable result."""

    # No record, or an expired one: the key is free.
    ABSENT = auto()
    # A pending record: another caller holds the key and its action has not finished.
    IN_FLIGHT = auto()
    # A record this adapter cannot decode, or a read that failed: run the action.
    UNUSABLE = auto()


class AsyncIdempotencyCoordinator:
    """Coordinator for asynchronous idempotent operations.

    Args:
        repository: Storage for idempotency records.
        domain_service: Record factory and TTL bounds.
        operation_ttls: Per-operation TTL overrides in seconds; wins over the decorator.
        metrics: Metrics collector for hits, misses, collisions, errors and latency.
        enabled: Set to False to make every call a pass-through to the action.
        in_flight: What a second caller gets while the first one's action is still running
            under the same key. ``"wait"`` (the default) waits for the first caller's result,
            ``"raise"`` raises ``IdempotencyInProgressError`` at once, and ``"run"`` runs the
            action too, as the coordinator did before reservations existed.
        in_flight_lease_seconds: How long a reservation is held before it counts as
            abandoned, and the longest a waiting caller waits. It has to outlive the action.
    """

    def __init__(
        self,
        repository: AsyncIdempotencyRepository,
        domain_service: IdempotencyDomainService,
        operation_ttls: dict[str, int] | None = None,
        metrics: IdempotencyMetricsProtocol | None = None,
        enabled: bool = True,
        in_flight: InFlightMode = DEFAULT_IN_FLIGHT_MODE,
        in_flight_lease_seconds: int = DEFAULT_IN_FLIGHT_LEASE_SECONDS,
    ) -> None:
        if in_flight not in _IN_FLIGHT_MODES:
            raise IdempotencyValidationError(f"in_flight must be one of {_IN_FLIGHT_MODES}, got {in_flight!r}")
        if in_flight_lease_seconds < 1:
            raise IdempotencyValidationError(f"in_flight_lease_seconds must be >= 1, got {in_flight_lease_seconds}")
        if in_flight != "run" and not callable(getattr(repository, "replace", None)):
            # A repository written before reservations existed would take the pending
            # record through save() and never get it replaced, so every retry within the
            # lease would wait or be refused. Fail at construction instead.
            raise TypeError(
                f"{type(repository).__name__} has no replace(); in_flight={in_flight!r} completes a reservation "
                "with it. Add the method, or pass in_flight='run'."
            )
        self._repo = repository
        self._svc = domain_service
        self._operation_ttls = operation_ttls or {}
        self._metrics = metrics or NoOpIdempotencyMetrics()
        self._enabled = enabled
        self._in_flight = in_flight
        self._in_flight_lease_seconds = in_flight_lease_seconds

    async def coordinate(
        self,
        operation: str,
        idempotency_key: str | None,
        ttl_seconds: int | None,
        adapter: ResultAdapter[T],
        action: Callable[..., Awaitable[T]],
        /,
        *args: Any,
        idempotency_fingerprint: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Coordinate an idempotent operation.

        Everything after the five positional-only arguments goes to the action, except
        ``idempotency_fingerprint``: what the caller says the request is. It is stored with
        the record, and a record under the same key with a different fingerprint is a key
        reuse. ``None`` on either side means no comparison, which is the key-only behaviour.

        With ``enabled=False`` the action is simply run: nothing is read, nothing is
        written, and no metric is recorded.

        Storage and decode trouble never raise: the coordinator degrades to running the
        action. What does raise is ``IdempotencyInProgressError``, when another call with
        the same key is still running its action and ``in_flight`` is ``"raise"``, or
        ``"wait"`` and a whole lease has passed; and ``IdempotencyKeyReuseError``, when the
        record under the key was made for a different request.
        """
        if not self._enabled or not idempotency_key:
            return await action(*args, **kwargs)

        if self._in_flight == "run":
            return await self._coordinate_unreserved(
                operation, idempotency_key, ttl_seconds, adapter, action, idempotency_fingerprint, *args, **kwargs
            )

        claim = await self._claim(operation, idempotency_key, adapter, idempotency_fingerprint)
        if isinstance(claim, _Hit):
            return claim.value

        try:
            result = await action(*args, **kwargs)
        except BaseException:
            # Failures are not cached, so the reservation goes too and the retry runs
            # again. Cancellation counts: nothing says the action completed.
            if claim:
                await self._try_release(operation, idempotency_key)
            raise

        ttl_minutes = self._resolve_ttl_minutes(operation, ttl_seconds)
        completed = await self._try_complete(
            operation, idempotency_key, result, adapter, ttl_minutes, idempotency_fingerprint
        )
        if claim and not completed:
            # Our reservation with no result behind it would make every retry within the
            # lease wait for, or be refused over, a record that is never coming.
            await self._try_release(operation, idempotency_key)
        return result

    async def _coordinate_unreserved(
        self,
        operation: str,
        idempotency_key: str,
        ttl_seconds: int | None,
        adapter: ResultAdapter[T],
        action: Callable[..., Awaitable[T]],
        fingerprint: str | None,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """The flow without a reservation: read, run, ``SET NX``, and adopt the winner's result on a collision."""
        # 1. Try to get from storage
        hit = await self._try_get_cached(operation, idempotency_key, adapter, fingerprint)
        if isinstance(hit, _Hit):
            return hit.value

        # 2. Execute business logic
        result = await action(*args, **kwargs)

        # 3. Cache the result
        ttl_minutes = self._resolve_ttl_minutes(operation, ttl_seconds)
        return await self._try_save_result(operation, idempotency_key, result, adapter, ttl_minutes, fingerprint)

    async def _claim(
        self,
        operation: str,
        idempotency_key: str,
        adapter: ResultAdapter[T],
        fingerprint: str | None,
    ) -> _Hit[T] | bool:
        """Reserve the key, or replay the record that holds it.

        Returns the hit when a completed record is there, ``True`` when the reservation is
        ours, and ``False`` when the key holds nothing usable and the action has to run
        unreserved. Raises ``IdempotencyInProgressError`` for a key another caller holds,
        at once in ``"raise"`` mode and after a whole lease of waiting in ``"wait"`` mode,
        and ``IdempotencyKeyReuseError`` for a key that holds a different request.
        """
        deadline = time.monotonic() + self._in_flight_lease_seconds
        read = False
        waiting = False
        while True:
            reserved = await self._try_reserve(operation, idempotency_key, fingerprint)
            if reserved is None:
                return False
            if reserved:
                # A read that came before us has counted the miss already.
                if not read:
                    self._metrics.record_miss(operation)
                return True

            found = await self._try_get_cached(operation, idempotency_key, adapter, fingerprint)
            read = True
            if isinstance(found, _Hit):
                return found
            if found is _Lookup.UNUSABLE:
                return False
            if found is _Lookup.IN_FLIGHT:
                if not waiting:
                    waiting = True
                    self._metrics.record_collision(operation)
                    logger.info(
                        "Idempotency key in flight",
                        extra={
                            "operation": operation,
                            "idempotency_key": idempotency_key,
                            "in_flight": self._in_flight,
                        },
                    )
                if self._in_flight == "raise":
                    raise IdempotencyInProgressError(operation, idempotency_key)
            # ABSENT after a collision: the holder gave the key up between our write and
            # our read, or its lease ran out. Reserve again, as a waiter does when the
            # marker disappears; the deadline bounds both.
            if time.monotonic() >= deadline:
                logger.warning(
                    "Idempotency key still in flight after the lease; refusing the call",
                    extra={"operation": operation, "idempotency_key": idempotency_key},
                )
                raise IdempotencyInProgressError(operation, idempotency_key)
            if found is _Lookup.IN_FLIGHT:
                await asyncio.sleep(IN_FLIGHT_POLL_INTERVAL_SECONDS)

    def _resolve_ttl_minutes(self, operation: str, ttl_seconds: int | None) -> int | None:
        """Determine TTL in minutes based on settings and overrides."""
        effective_ttl_seconds = self._operation_ttls.get(operation) or ttl_seconds
        if effective_ttl_seconds is None:
            return None
        return max(1, effective_ttl_seconds // 60)

    async def _try_reserve(self, operation: str, idempotency_key: str, fingerprint: str | None) -> bool | None:
        """Write the pending record under ``SET NX``.

        ``True`` when the reservation is ours, ``False`` when the key is already taken,
        ``None`` when the write could not be made at all.
        """
        start_time = time.perf_counter()
        try:
            pending = self._svc.create_pending_record(
                operation,
                idempotency_key,
                lease_seconds=self._in_flight_lease_seconds,
                fingerprint=fingerprint,
            )
            await self._repo.save(pending)
        except IdempotencyKeyCollisionError:
            return False
        except IdempotencyValidationError:
            self._metrics.record_error(operation, "record_validation_error")
            logger.exception(
                "Idempotency reservation rejected; this operation will not be cached",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return None
        except Exception:
            self._metrics.record_error(operation, "storage_reserve_error")
            logger.exception(
                "Idempotency coordinator error while reserving",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return None
        else:
            return True
        finally:
            self._metrics.record_latency(operation, "reserve", time.perf_counter() - start_time)

    async def _try_release(self, operation: str, idempotency_key: str) -> None:
        """Delete our pending record so the retry runs the action again; storage trouble is logged, not raised."""
        try:
            await self._repo.delete(operation, idempotency_key)
        except Exception:
            self._metrics.record_error(operation, "storage_release_error")
            logger.exception(
                "Idempotency coordinator error while releasing a reservation",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )

    async def _try_get_cached(
        self,
        operation: str,
        idempotency_key: str,
        adapter: ResultAdapter[T],
        fingerprint: str | None,
    ) -> _Hit[T] | _Lookup:
        """Fetch and decode the record under the key.

        A read that fails is ``UNUSABLE``, never an exception; the one exception that does
        come through is ``IdempotencyKeyReuseError``, which is about the request, not storage.
        """
        start_time = time.perf_counter()
        try:
            found = await self._get_and_decode(operation, idempotency_key, adapter, fingerprint)
        except IdempotencyKeyReuseError:
            self._metrics.record_error(operation, "key_reuse")
            logger.warning(
                "Idempotency key reused for a different request",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            raise
        except Exception:
            self._metrics.record_error(operation, "storage_get_error")
            logger.exception(
                "Idempotency coordinator error while fetching",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return _Lookup.UNUSABLE
        finally:
            self._metrics.record_latency(operation, "get", time.perf_counter() - start_time)

        if isinstance(found, _Hit):
            self._metrics.record_hit(operation)
            logger.info(
                "Idempotency cache hit",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        elif found is not _Lookup.IN_FLIGHT:
            self._metrics.record_miss(operation)
        return found

    async def _try_save_result(
        self,
        operation: str,
        idempotency_key: str,
        result: T,
        adapter: ResultAdapter[T],
        ttl_minutes: int | None,
        fingerprint: str | None,
    ) -> T:
        """Try to save result to storage. Handles collisions and errors gracefully."""
        start_time = time.perf_counter()
        try:
            await self._save_to_repo(
                operation, idempotency_key, result, adapter, ttl_minutes, fingerprint, replace=False
            )
            logger.info(
                "Idempotency result saved",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        except IdempotencyKeyCollisionError:
            return await self._handle_collision(operation, idempotency_key, result, adapter, fingerprint)
        except (IdempotencyValidationError, IdempotencyInvalidTTLError):
            self._report_unstorable_record(operation, idempotency_key, adapter)
        except Exception:
            self._report_save_failure(operation, idempotency_key)
        finally:
            self._metrics.record_latency(operation, "save", time.perf_counter() - start_time)

        return result

    async def _try_complete(
        self,
        operation: str,
        idempotency_key: str,
        result: T,
        adapter: ResultAdapter[T],
        ttl_minutes: int | None,
        fingerprint: str | None,
    ) -> bool:
        """Write the result over the reservation; ``False`` when it could not be stored, never an exception."""
        start_time = time.perf_counter()
        try:
            await self._save_to_repo(
                operation, idempotency_key, result, adapter, ttl_minutes, fingerprint, replace=True
            )
        except (IdempotencyValidationError, IdempotencyInvalidTTLError):
            self._report_unstorable_record(operation, idempotency_key, adapter)
            return False
        except Exception:
            self._report_save_failure(operation, idempotency_key)
            return False
        else:
            logger.info(
                "Idempotency result saved",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return True
        finally:
            self._metrics.record_latency(operation, "save", time.perf_counter() - start_time)

    def _report_unstorable_record(self, operation: str, idempotency_key: str, adapter: ResultAdapter[T]) -> None:
        # The record itself is invalid — the adapter encoded something the
        # storage format cannot hold, or the TTL is out of range. No retry can
        # fix that, so it is reported as a contract violation rather than a
        # storage blip. The result is still returned: the action has already
        # run, and raising here would make the caller retry a completed
        # operation — the one thing an idempotency layer must never cause.
        self._metrics.record_error(operation, "record_validation_error")
        logger.exception(
            "Idempotency record rejected; this operation will not be cached",
            extra={
                "operation": operation,
                "idempotency_key": idempotency_key,
                "adapter": type(adapter).__name__,
            },
        )

    def _report_save_failure(self, operation: str, idempotency_key: str) -> None:
        self._metrics.record_error(operation, "storage_save_error")
        logger.exception(
            "Idempotency coordinator error while saving for operation",
            extra={"operation": operation, "idempotency_key": idempotency_key},
        )

    async def _get_and_decode(
        self,
        operation: str,
        idempotency_key: str,
        adapter: ResultAdapter[T],
        fingerprint: str | None,
    ) -> _Hit[T] | _Lookup:
        """Fetch record from repository and decode it safely; raises ``IdempotencyKeyReuseError`` on a mismatch."""
        cached = await self._repo.get(operation, idempotency_key)
        if cached is None:
            return _Lookup.ABSENT
        try:
            # The protocol asks a repository not to return an expired record, but expiry is
            # the domain's rule to enforce, and a backend without native expiry cannot.
            # An expired lease is an abandoned reservation, so it goes the same way.
            self._svc.validate_record(cached)
        except IdempotencyRecordExpiredError:
            logger.warning(
                "Idempotency record expired; treating it as a miss",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return _Lookup.ABSENT
        # Before the pending check on purpose: a waiter with a different payload is refused
        # now rather than handed someone else's result later.
        self._check_fingerprint(cached, fingerprint)
        if cached.is_pending:
            return _Lookup.IN_FLIGHT
        return self._decode_safely(adapter, cached.result, operation, idempotency_key)

    @staticmethod
    def _check_fingerprint(record: IdempotencyRecord, fingerprint: str | None) -> None:
        """Raise ``IdempotencyKeyReuseError`` when both sides have a fingerprint and they differ."""
        if fingerprint is None or record.fingerprint is None or record.fingerprint == fingerprint:
            return
        raise IdempotencyKeyReuseError(record.operation, record.idempotency_key, record.fingerprint, fingerprint)

    async def _save_to_repo(
        self,
        operation: str,
        idempotency_key: str,
        result: T,
        adapter: ResultAdapter[T],
        ttl_minutes: int | None,
        fingerprint: str | None,
        *,
        replace: bool,
    ) -> None:
        """Perform the actual save operation: ``SET NX``, or a plain ``SET`` over our reservation."""
        record = self._svc.create_record(
            operation=operation,
            idempotency_key=idempotency_key,
            result=adapter.encode(result),
            ttl_minutes=ttl_minutes,
            fingerprint=fingerprint,
        )
        if replace:
            await self._repo.replace(record)
        else:
            await self._repo.save(record)

    async def _handle_collision(
        self,
        operation: str,
        idempotency_key: str,
        current_result: T,
        adapter: ResultAdapter[T],
        fingerprint: str | None,
    ) -> T:
        """Handle key collision by trying to fetch the winner's result."""
        self._metrics.record_collision(operation)
        logger.info(
            "Idempotency key collision, fetching concurrent result",
            extra={"operation": operation, "idempotency_key": idempotency_key},
        )
        try:
            winner = await self._get_and_decode(operation, idempotency_key, adapter, fingerprint)
            if isinstance(winner, _Hit):
                return winner.value
        except IdempotencyKeyReuseError:
            # The action has already run, so raising would make the caller retry a
            # completed operation. The caller keeps its own result; the reuse is on record.
            self._metrics.record_error(operation, "key_reuse")
            logger.warning(
                "Idempotency key reused for a different request by a concurrent caller; keeping our own result",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        except Exception:
            logger.exception(
                "Failed to fetch concurrent result after collision",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        return current_result

    def _decode_safely(
        self,
        adapter: ResultAdapter[T],
        data: Any,
        operation: str,
        idempotency_key: str,
    ) -> _Hit[T] | _Lookup:
        """Try to decode data using adapter. Returns ``UNUSABLE`` and logs error on failure."""
        try:
            return _Hit(adapter.decode(data))
        except Exception:
            logger.exception(
                "Idempotency decode error",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return _Lookup.UNUSABLE
