"""Idempotency domain service."""

from pydantic import JsonValue, ValidationError

from ..constants import DEFAULT_TTL_MINUTES, MAX_TTL_SECONDS, MIN_TTL_SECONDS
from ..exceptions import IdempotencyInvalidTTLError, IdempotencyRecordExpiredError, IdempotencyValidationError
from ..models.entities import IdempotencyRecord


class IdempotencyDomainService:
    """Domain service for creating and validating idempotency records.

    Provides standardized way to create records with validation and TTL management.
    """

    def __init__(
        self,
        *,
        default_ttl_minutes: int = DEFAULT_TTL_MINUTES,
        min_ttl_seconds: int = MIN_TTL_SECONDS,
        max_ttl_seconds: int = MAX_TTL_SECONDS,
    ) -> None:
        """Initialize domain service.

        Args:
            default_ttl_minutes: Default TTL for records when not specified
            min_ttl_seconds: Minimum allowed TTL
            max_ttl_seconds: Maximum allowed TTL
        """
        if default_ttl_minutes < 1:
            raise IdempotencyValidationError(f"default_ttl_minutes must be >= 1, got {default_ttl_minutes}")
        if min_ttl_seconds < 1:
            raise IdempotencyValidationError(f"min_ttl_seconds must be >= 1, got {min_ttl_seconds}")
        if max_ttl_seconds < min_ttl_seconds:
            raise IdempotencyValidationError(
                f"max_ttl_seconds ({max_ttl_seconds}) must be >= min_ttl_seconds ({min_ttl_seconds})"
            )

        # Ensure default TTL is within bounds
        default_ttl_seconds = float(default_ttl_minutes * 60)
        if default_ttl_seconds < min_ttl_seconds or default_ttl_seconds > max_ttl_seconds:
            raise IdempotencyValidationError(
                f"Default TTL ({default_ttl_seconds}s) is outside allowed range "
                f"[{min_ttl_seconds}s, {max_ttl_seconds}s]"
            )

        self.default_ttl_minutes = default_ttl_minutes
        self.min_ttl_seconds = min_ttl_seconds
        self.max_ttl_seconds = max_ttl_seconds

    def create_record(
        self,
        operation: str,
        idempotency_key: str,
        result: dict[str, JsonValue],
        *,
        ttl_minutes: int | None = None,
    ) -> IdempotencyRecord:
        """Create a new idempotency record.

        Args:
            operation: Operation name (e.g., 'user.create')
            idempotency_key: Unique key for this operation
            result: Operation result to cache
            ttl_minutes: Custom TTL in minutes (uses default if None)

        Returns:
            IdempotencyRecord ready to be saved

        Raises:
            IdempotencyValidationError: If validation fails
            IdempotencyInvalidTTLError: If TTL is outside allowed range
        """
        # Calculate TTL
        ttl_mins = ttl_minutes if ttl_minutes is not None else self.default_ttl_minutes
        ttl_seconds = float(ttl_mins * 60)

        if ttl_seconds < self.min_ttl_seconds or ttl_seconds > self.max_ttl_seconds:
            raise IdempotencyInvalidTTLError(ttl_seconds, float(self.min_ttl_seconds), float(self.max_ttl_seconds))

        try:
            return IdempotencyRecord.create(
                operation=operation,
                idempotency_key=idempotency_key,
                result=result,
                ttl_seconds=ttl_seconds,
            )
        except ValidationError as e:
            # Re-map Pydantic validation error to domain validation error with detailed errors
            raise IdempotencyValidationError(str(e), errors=e.errors()) from e

    def validate_record(self, record: IdempotencyRecord) -> None:
        """Validate that record is still usable.

        Args:
            record: Record to validate

        Raises:
            IdempotencyRecordExpiredError: If record has expired
        """
        if record.is_expired:
            raise IdempotencyRecordExpiredError(record.operation, record.idempotency_key)
