"""Dishka provider for async idempotency coordinator."""

from dishka import Provider, Scope, provide

from idempotency_kit import (
    AsyncIdempotencyCoordinator,
    AsyncIdempotencyRepository,
    IdempotencyDomainService,
    IdempotencyMetricsProtocol,
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
        metrics: IdempotencyMetricsProtocol | None = None,
    ) -> AsyncIdempotencyCoordinator:
        """Provide idempotency coordinator."""
        return AsyncIdempotencyCoordinator(
            repository=repository,
            domain_service=domain_service,
            operation_ttls=settings.operation_ttls,
            metrics=metrics,
        )
