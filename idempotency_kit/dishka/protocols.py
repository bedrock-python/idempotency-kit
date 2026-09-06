"""Protocols for Dishka providers."""

from typing import Protocol, runtime_checkable

from idempotency_kit.core.constants import InFlightMode


@runtime_checkable
class IdempotencySettingsProtocol(Protocol):
    """Protocol for idempotency settings."""

    @property
    def enabled(self) -> bool:
        """Whether the shipped coordinator applies idempotency at all.

        A settings object without the attribute is read as enabled.
        """
        ...

    @property
    def key_prefix(self) -> str:
        """Key prefix for Redis."""
        ...

    @property
    def metrics_enabled(self) -> bool:
        """Whether the shipped providers wire ``PrometheusIdempotencyMetrics``
        (needs the ``prometheus`` extra); a no-op collector otherwise.
        """
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

    @property
    def in_flight(self) -> InFlightMode:
        """What a second caller gets while the first one's action is still running.

        A settings object without the attribute is read as ``"wait"``.
        """
        ...

    @property
    def in_flight_lease_seconds(self) -> int:
        """How long an in-flight reservation is held before it counts as abandoned.

        A settings object without the attribute is read as the default lease.
        """
        ...
