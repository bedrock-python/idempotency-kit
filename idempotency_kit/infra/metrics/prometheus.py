"""Prometheus-based idempotency metrics."""

from __future__ import annotations

try:
    from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

from ...core.protocols.metrics import IdempotencyMetricsProtocol


class PrometheusIdempotencyMetrics(IdempotencyMetricsProtocol):
    """Prometheus implementation of idempotency metrics.

    Prometheus registers a metric name once per registry, so a second instance with the same
    prefix on the same registry raises from prometheus_client. `get_idempotency_metrics` is
    the one to call for the default registry: it caches one instance per prefix, and it is
    what the Dishka provider calls. A test that wants isolation passes its own ``registry``.

    Metrics:
        - idempotency_operations_total: Total number of idempotent operations.
        - idempotency_operation_duration_seconds: Latency of idempotency checks and saves.

    Labels:
        - operation: Name of the business operation.
        - status: Result of idempotency check (hit, miss, collision, error).
        - method: Internal method (get, save, delete, etc).
    """

    def __init__(self, prefix: str | None = None, registry: CollectorRegistry | None = None) -> None:
        """Initialize metrics.

        Args:
            prefix: Optional prefix for metric names.
            registry: The registry to register with; the process-wide default when None.
        """
        if not _HAS_PROMETHEUS:
            raise ImportError(
                "PrometheusIdempotencyMetrics requires prometheus-client. "
                "Install it with: pip install idempotency-kit[prometheus]"
            )

        metric_prefix = f"{prefix}_" if prefix else ""
        reg = registry if registry is not None else REGISTRY

        self._operations_total = Counter(
            f"{metric_prefix}idempotency_operations_total",
            "Total number of idempotent operations",
            ["operation", "status"],
            registry=reg,
        )
        self._duration_seconds = Histogram(
            f"{metric_prefix}idempotency_operation_duration_seconds",
            "Latency of idempotency operations",
            ["operation", "method"],
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=reg,
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


_METRICS_CACHE: dict[str | None, PrometheusIdempotencyMetrics] = {}


def get_idempotency_metrics(prefix: str | None = None) -> PrometheusIdempotencyMetrics:
    """Get (or lazily create) the cached ``PrometheusIdempotencyMetrics`` for a prefix, on the default registry.

    Caching by prefix is what lets a container be rebuilt — a test suite does it per test —
    without Prometheus refusing the second registration of the same series.

    Args:
        prefix: The metric name prefix the instance was, or is, created with.

    Returns:
        The one instance for that prefix.
    """
    key = prefix or None
    cached = _METRICS_CACHE.get(key)
    if cached is None:
        cached = _METRICS_CACHE[key] = PrometheusIdempotencyMetrics(prefix=key)
    return cached
