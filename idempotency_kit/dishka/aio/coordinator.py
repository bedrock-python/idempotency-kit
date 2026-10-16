"""Dishka provider for async idempotency coordinator."""

from dishka import Provider, Scope, provide

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    AsyncIdempotencyRepository,
    IdempotencyDomainService,
    IdempotencyMetricsProtocol,
)
from idempotency_kit.core.constants import (
    DEFAULT_IN_FLIGHT_LEASE_SECONDS,
    DEFAULT_IN_FLIGHT_MODE,
    DEFAULT_ON_STORAGE_ERROR,
)

from ..protocols import IdempotencySettingsProtocol


class AsyncIdempotencyCoordinatorProvider(Provider):
    """Provider for async idempotency coordinator."""

    scope = Scope.APP

    @provide
    def get_coordinator(
        self,
        repository: AsyncIdempotencyRepository,
        domain_service: IdempotencyDomainService,
        settings: IdempotencySettingsProtocol,
        metrics: IdempotencyMetricsProtocol,
    ) -> AsyncIdempotencyCoordinator:
        """Provide idempotency coordinator."""
        return AsyncIdempotencyCoordinator(
            repository=repository,
            domain_service=domain_service,
            operation_ttls=settings.operation_ttls,
            metrics=metrics,
            # Read defensively: settings objects written against the protocol before
            # these fields were part of it stay valid, and they mean the defaults.
            enabled=getattr(settings, "enabled", True),
            in_flight=getattr(settings, "in_flight", DEFAULT_IN_FLIGHT_MODE),
            in_flight_lease_seconds=getattr(settings, "in_flight_lease_seconds", DEFAULT_IN_FLIGHT_LEASE_SECONDS),
            on_storage_error=getattr(settings, "on_storage_error", DEFAULT_ON_STORAGE_ERROR),
        )
