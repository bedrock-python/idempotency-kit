"""Settings for idempotency kit."""

from pydantic import BaseModel, Field

from .core.constants import (
    DEFAULT_IN_FLIGHT_LEASE_SECONDS,
    DEFAULT_IN_FLIGHT_MODE,
    DEFAULT_TTL_MINUTES,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    InFlightMode,
)


class BaseIdempotencySettings(BaseModel):
    """Common configuration for idempotency kit."""

    enabled: bool = Field(
        default=True,
        description="Whether the coordinator applies idempotency; False makes every call a pass-through",
    )
    key_prefix: str = Field(description="Redis key prefix for idempotency records")
    metrics_enabled: bool = Field(default=False, description="Whether idempotency metrics are enabled")
    default_ttl_minutes: int = Field(
        default=DEFAULT_TTL_MINUTES, description="Default TTL for records in minutes (1 hour)"
    )
    min_ttl_seconds: int = Field(default=MIN_TTL_SECONDS, description="Minimum allowed TTL in seconds (1 minute)")
    max_ttl_seconds: int = Field(default=MAX_TTL_SECONDS, description="Maximum allowed TTL in seconds (30 days)")
    operation_ttls: dict[str, int] = Field(
        default_factory=dict,
        description="Operation-specific TTLs in seconds (overrides decorator and default)",
    )
    in_flight: InFlightMode = Field(
        default=DEFAULT_IN_FLIGHT_MODE,
        description=(
            "What a second caller gets while the first one's action is still running under the same key: "
            "wait for its result, raise IdempotencyInProgressError, or run the action too"
        ),
    )
    in_flight_lease_seconds: int = Field(
        default=DEFAULT_IN_FLIGHT_LEASE_SECONDS,
        description="How long an in-flight reservation is held before it counts as abandoned (30 seconds)",
    )
