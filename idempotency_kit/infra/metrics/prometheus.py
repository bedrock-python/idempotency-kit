"""Prometheus-based idempotency metrics."""

from __future__ import annotations

try:
    from prometheus_client import Counter, Histogram

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

from ...core.protocols.metrics import IdempotencyMetricsProtocol


class PrometheusIdempotencyMetrics(IdempotencyMetricsProtocol):
    """Prometheus implementation of idempotency metrics.

    Instantiate once per process (e.g. via DI) — duplicate instances with the same
    metric names will conflict with prometheus_client registration.

    Metrics:
        - idempotency_operations_total: Total number of idempotent operations.
        - idempotency_operation_duration_seconds: Latency of idempotency checks and saves.

    Labels:
        - operation: Name of the business operation.
        - status: Result of idempotency check (hit, miss, collision, error).
        - method: Internal method (get, save, delete, etc).
    """

    def __init__(self, prefix: str | None = None) -> None:
        """Initialize metrics.

        Args:
            prefix: Optional prefix for metric names.
        """
        if not _HAS_PROMETHEUS:
            raise ImportError(
                "PrometheusIdempotencyMetrics requires prometheus-client. "
                "Install it with: pip install idempotency-kit[prometheus]"
            )

        metric_prefix = f"{prefix}_" if prefix else ""

        self._operations_total = Counter(
            f"{metric_prefix}idempotency_operations_total",
            "Total number of idempotent operations",
            ["operation", "status"],
        )
        self._duration_seconds = Histogram(
            f"{metric_prefix}idempotency_operation_duration_seconds",
            "Latency of idempotency operations",
            ["operation", "method"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
        )

    def record_hit(self, operation: str) -> None:
        """Record a cache hit."""
        self._operations_total.labels(operation=operation, status="hit").inc()

    def record_miss(self, operation: str) -> None:
        """Record a cache miss."""
        self._operations_total.labels(operation=operation, status="miss").inc()

    def record_collision(self, operation: str) -> None:
        """Record a key collision."""
        self._operations_total.labels(operation=operation, status="collision").inc()

    def record_error(self, operation: str, error_type: str) -> None:
        """Record an error."""
        self._operations_total.labels(operation=operation, status=f"error_{error_type}").inc()

    def record_latency(self, operation: str, method: str, duration_seconds: float) -> None:
        """Record operation latency."""
        self._duration_seconds.labels(operation=operation, method=method).observe(duration_seconds)

    def record_bulk_hit(self, operation: str, count: int) -> None:
        """Record multiple cache hits."""
        self._operations_total.labels(operation=operation, status="hit").inc(count)

    def record_bulk_miss(self, operation: str, count: int) -> None:
        """Record multiple cache misses."""
        self._operations_total.labels(operation=operation, status="miss").inc(count)
