"""Core constants for idempotency."""

# Key constraints
# Maximum allowed length for idempotency key.
MAX_KEY_LENGTH: int = 255

# Maximum allowed length for operation name.
MAX_OPERATION_LENGTH: int = 100

# TTL defaults
# Default TTL in minutes when not explicitly specified.
DEFAULT_TTL_MINUTES: int = 30

# Minimum allowed TTL in seconds (1 minute).
MIN_TTL_SECONDS: int = 60

# Maximum allowed TTL in seconds (24 hours).
MAX_TTL_SECONDS: int = 86400  # 24 hours
