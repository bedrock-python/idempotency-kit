"""Common Dishka providers for idempotency."""

from dishka import Provider, Scope, provide

from idempotency_kit import IdempotencyDomainService

from .protocols import IdempotencySettingsProtocol


class IdempotencyProvider(Provider):
    """Provider for idempotency domain service (sync / framework-agnostic)."""

    scope = Scope.APP

    @provide
    def get_service(self, settings: IdempotencySettingsProtocol) -> IdempotencyDomainService:
        """Provide idempotency domain service."""
        return IdempotencyDomainService(
            default_ttl_minutes=settings.default_ttl_minutes,
            min_ttl_seconds=settings.min_ttl_seconds,
            max_ttl_seconds=settings.max_ttl_seconds,
        )
