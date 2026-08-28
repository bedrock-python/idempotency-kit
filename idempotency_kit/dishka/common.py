"""Common Dishka providers for idempotency."""

from dishka import Provider, Scope, provide

from idempotency_kit import IdempotencyDomainService, IdempotencyMetricsProtocol, NoOpIdempotencyMetrics
from idempotency_kit.infra.metrics.prometheus import PrometheusIdempotencyMetrics

from .protocols import IdempotencySettingsProtocol


class IdempotencyProvider(Provider):
    """Provider for the idempotency domain service and the metrics collector (sync / framework-agnostic).

    The metrics collector is a single APP-scoped instance shared by the repository and the coordinator,
    which is also what ``PrometheusIdempotencyMetrics`` needs — it registers its collectors once per process.
    An application with another metrics backend overrides it with ``@provide(override=True)`` in a later provider.
    """

    scope = Scope.APP

    @provide
    def get_service(self, settings: IdempotencySettingsProtocol) -> IdempotencyDomainService:
        """Provide idempotency domain service."""
        return IdempotencyDomainService(
            default_ttl_minutes=settings.default_ttl_minutes,
            min_ttl_seconds=settings.min_ttl_seconds,
            max_ttl_seconds=settings.max_ttl_seconds,
        )

    @provide
    def get_metrics(self, settings: IdempotencySettingsProtocol) -> IdempotencyMetricsProtocol:
        """Provide the metrics collector: Prometheus when ``settings.metrics_enabled``, a no-op otherwise."""
        if settings.metrics_enabled:
            return PrometheusIdempotencyMetrics()
        return NoOpIdempotencyMetrics()
