"""Settings for idempotency kit."""

from pydantic import BaseModel, Field


class BaseIdempotencySettings(BaseModel):
    """Common configuration for idempotency kit."""

    enabled: bool = Field(default=True, description="Whether idempotency is enabled")
    key_prefix: str = Field(description="Redis key prefix for idempotency records")
    metrics_enabled: bool = Field(default=False, description="Whether idempotency metrics are enabled")
    default_ttl_minutes: int = Field(default=60, description="Default TTL for records in minutes")
    min_ttl_seconds: int = Field(default=1, description="Minimum allowed TTL in seconds")
    max_ttl_seconds: int = Field(default=30 * 24 * 3600, description="Maximum allowed TTL in seconds (30 days)")
    operation_ttls: dict[str, int] = Field(
        default_factory=dict,
        description="Operation-specific TTLs in seconds (overrides decorator and default)",
    )
