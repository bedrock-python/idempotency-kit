"""Core constants for idempotency."""

from typing import Literal

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

# In-flight handling
# What the coordinator does with a second caller that arrives while the first one's
# action is still running under the same key: wait for the first caller's result, raise
# IdempotencyInProgressError at once, or run the action too.
InFlightMode = Literal["wait", "raise", "run"]

# Wait by default: a second caller in the retry window gets the first caller's result
# instead of producing a second one.
DEFAULT_IN_FLIGHT_MODE: InFlightMode = "wait"

# How long a reservation is held before it counts as abandoned (30 seconds). It has to
# outlive the action; a waiting caller gives up after the same span.
DEFAULT_IN_FLIGHT_LEASE_SECONDS: int = 30

# How often a waiting caller re-reads the key.
IN_FLIGHT_POLL_INTERVAL_SECONDS: float = 0.05
