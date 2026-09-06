"""Settings for idempotency kit."""

from pydantic import BaseModel, Field

from .core.constants import DEFAULT_TTL_MINUTES, MAX_TTL_SECONDS, MIN_TTL_SECONDS


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
