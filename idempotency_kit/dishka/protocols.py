"""Protocols for Dishka providers."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdempotencySettingsProtocol(Protocol):
    """Protocol for idempotency settings."""

    @property
    def key_prefix(self) -> str:
        """Key prefix for Redis."""
        ...

    @property
    def metrics_enabled(self) -> bool:
        """Whether idempotency metrics are enabled."""
        ...

    @property
    def default_ttl_minutes(self) -> int:
        """Default TTL in minutes."""
        ...

    @property
    def min_ttl_seconds(self) -> int:
        """Minimum TTL in seconds."""
        ...

    @property
    def max_ttl_seconds(self) -> int:
        """Maximum TTL in seconds."""
        ...

    @property
    def operation_ttls(self) -> dict[str, int]:
        """Specific TTLs for operations in seconds."""
        ...
