import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from idempotency_kit.core.exceptions import IdempotencyKeyCollisionError
from idempotency_kit.core.protocols.adapter import ResultAdapter
from idempotency_kit.core.protocols.aio.repository import AsyncIdempotencyRepository
from idempotency_kit.core.protocols.metrics import IdempotencyMetricsProtocol, NoOpIdempotencyMetrics
from idempotency_kit.core.services.domain import IdempotencyDomainService

T = TypeVar("T")

logger = logging.getLogger(__name__)


class AsyncIdempotencyCoordinator:
    """Coordinator for asynchronous idempotent operations."""

    def __init__(
        self,
        repository: AsyncIdempotencyRepository,
        domain_service: IdempotencyDomainService,
        operation_ttls: dict[str, int] | None = None,
        metrics: IdempotencyMetricsProtocol | None = None,
    ) -> None:
        self._repo = repository
        self._svc = domain_service
        self._operation_ttls = operation_ttls or {}
        self._metrics = metrics or NoOpIdempotencyMetrics()

    async def coordinate(
        self,
        operation: str,
        idempotency_key: str | None,
        ttl_seconds: int | None,
        adapter: ResultAdapter[T],
        action: Callable[..., Awaitable[T]],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Coordinate an idempotent operation."""
        if not idempotency_key:
            return await action(*args, **kwargs)

        # 1. Try to get from storage
        cached_result = await self._try_get_cached(operation, idempotency_key, adapter)
        if cached_result is not None:
            return cached_result

        # 2. Execute business logic
        result = await action(*args, **kwargs)

        # 3. Cache the result
        ttl_minutes = self._resolve_ttl_minutes(operation, ttl_seconds)
        return await self._try_save_result(operation, idempotency_key, result, adapter, ttl_minutes)

    def _resolve_ttl_minutes(self, operation: str, ttl_seconds: int | None) -> int | None:
        """Determine TTL in minutes based on settings and overrides."""
        effective_ttl_seconds = self._operation_ttls.get(operation) or ttl_seconds
        if effective_ttl_seconds is None:
            return None
        return max(1, effective_ttl_seconds // 60)

    async def _try_get_cached(
        self,
        operation: str,
        idempotency_key: str,
        adapter: ResultAdapter[T],
    ) -> T | None:
        """Try to fetch and decode result from storage. Returns None on miss or error."""
        start_time = time.perf_counter()
        try:
            result = await self._get_and_decode(operation, idempotency_key, adapter)
            if result is not None:
                self._metrics.record_hit(operation)
                logger.info(
                    "Idempotency cache hit",
                    extra={"operation": operation, "idempotency_key": idempotency_key},
                )
                return result
            self._metrics.record_miss(operation)
        except Exception:
            self._metrics.record_error(operation, "storage_get_error")
            logger.exception(
                "Idempotency coordinator error while fetching",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        finally:
            self._metrics.record_latency(operation, "get", time.perf_counter() - start_time)
        return None

    async def _try_save_result(
        self,
        operation: str,
        idempotency_key: str,
        result: T,
        adapter: ResultAdapter[T],
        ttl_minutes: int | None,
    ) -> T:
        """Try to save result to storage. Handles collisions and errors gracefully."""
        start_time = time.perf_counter()
        try:
            await self._save_to_repo(operation, idempotency_key, result, adapter, ttl_minutes)
            logger.info(
                "Idempotency result saved",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        except IdempotencyKeyCollisionError:
            return await self._handle_collision(operation, idempotency_key, result, adapter)
        except Exception:
            self._metrics.record_error(operation, "storage_save_error")
            logger.exception(
                "Idempotency coordinator error while saving for operation",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
        finally:
            self._metrics.record_latency(operation, "save", time.perf_counter() - start_time)

        return result

    async def _get_and_decode(
        self,
        operation: str,
        idempotency_key: str,
        adapter: ResultAdapter[T],
    ) -> T | None:
        """Fetch record from repository and decode it safely."""
        cached = await self._repo.get(operation, idempotency_key)
        if not cached:
            return None
        return self._decode_safely(adapter, cached.result, operation, idempotency_key)

    async def _save_to_repo(
        self,
        operation: str,
        idempotency_key: str,
        result: T,
        adapter: ResultAdapter[T],
        ttl_minutes: int | None,
    ) -> None:
        """Perform the actual save operation."""
        record = self._svc.create_record(
            operation=operation,
            idempotency_key=idempotency_key,
            result=adapter.encode(result),
            ttl_minutes=ttl_minutes,
        )
        await self._repo.save(record)

    async def _handle_collision(
        self,
        operation: str,
        idempotency_key: str,
        current_result: T,
        adapter: ResultAdapter[T],
    ) -> T:
        """Handle key collision by trying to fetch the winner's result."""
        self._metrics.record_collision(operation)
        logger.info(
            "Idempotency key collision, fetching concurrent result",
            extra={"operation": operation, "idempotency_key": idempotency_key},
        )
        try:
            winner_result = await self._get_and_decode(operation, idempotency_key, adapter)
            if winner_result is not None:
                return winner_result
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
    ) -> T | None:
        """Try to decode data using adapter. Returns None and logs error on failure."""
        try:
            return adapter.decode(data)
        except Exception:
            logger.exception(
                "Idempotency decode error",
                extra={"operation": operation, "idempotency_key": idempotency_key},
            )
            return None
