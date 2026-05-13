"""Idempotency metrics protocols."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdempotencyMetricsProtocol(Protocol):
    """Protocol for idempotency metrics collection."""

    def record_hit(self, operation: str) -> None:
        """Record a cache hit."""
        ...

    def record_miss(self, operation: str) -> None:
        """Record a cache miss."""
        ...

    def record_collision(self, operation: str) -> None:
        """Record a key collision."""
        ...

    def record_error(self, operation: str, error_type: str) -> None:
        """Record an error."""
        ...

    def record_latency(self, operation: str, method: str, duration_seconds: float) -> None:
        """Record operation latency."""
        ...

    def record_bulk_hit(self, operation: str, count: int) -> None:
        """Record multiple cache hits."""
        ...

    def record_bulk_miss(self, operation: str, count: int) -> None:
        """Record multiple cache misses."""
        ...


class NoOpIdempotencyMetrics(IdempotencyMetricsProtocol):
    """No-op metrics implementation that does nothing."""

    def record_hit(self, operation: str) -> None:
        """Record a cache hit (does nothing)."""

    def record_miss(self, operation: str) -> None:
        """Record a cache miss (does nothing)."""

    def record_collision(self, operation: str) -> None:
        """Record a key collision (does nothing)."""

    def record_error(self, operation: str, error_type: str) -> None:
        """Record an error (does nothing)."""

    def record_latency(self, operation: str, method: str, duration_seconds: float) -> None:
        """Record operation latency (does nothing)."""

    def record_bulk_hit(self, operation: str, count: int) -> None:
        """Record multiple cache hits (does nothing)."""

    def record_bulk_miss(self, operation: str, count: int) -> None:
        """Record multiple cache misses (does nothing)."""
