"""Core constants for idempotency."""

# Key constraints
# Maximum allowed length for idempotency key.
MAX_KEY_LENGTH: int = 255

# Maximum allowed length for operation name.
MAX_OPERATION_LENGTH: int = 100

# TTL defaults
# The single source for both IdempotencyDomainService's own defaults and the field
# defaults of BaseIdempotencySettings, so the bounds do not depend on how the service
# was built.

# Default TTL in minutes when not explicitly specified (1 hour).
DEFAULT_TTL_MINUTES: int = 60

# Minimum allowed TTL in seconds (1 minute); the coordinator floors every TTL at a minute.
MIN_TTL_SECONDS: int = 60

# Maximum allowed TTL in seconds (30 days).
MAX_TTL_SECONDS: int = 30 * 24 * 3600
