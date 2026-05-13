"""Idempotency Kit - Production-ready idempotency for microservices."""

from .core.adapters import (
    JsonResultAdapter,
    PydanticResultAdapter,
    VoidResultAdapter,
)
from .core.decorators.aio.idempotent import async_idempotent
from .core.exceptions import (
    IdempotencyError,
    IdempotencyInvalidTTLError,
    IdempotencyKeyCollisionError,
    IdempotencyRecordExpiredError,
    IdempotencyStorageError,
    IdempotencyValidationError,
)
from .core.models.entities import IdempotencyIdentifiers, IdempotencyRecord
from .core.protocols.adapter import ResultAdapter
from .core.protocols.aio.repository import AsyncIdempotencyRepository
from .core.protocols.metrics import IdempotencyMetricsProtocol, NoOpIdempotencyMetrics
from .core.services.aio.coordinator import AsyncIdempotencyCoordinator
from .core.services.domain import IdempotencyDomainService

__all__ = [
    "AsyncIdempotencyCoordinator",
    "AsyncIdempotencyRepository",
    "IdempotencyDomainService",
    "IdempotencyError",
    "IdempotencyIdentifiers",
    "IdempotencyInvalidTTLError",
    "IdempotencyKeyCollisionError",
    "IdempotencyMetricsProtocol",
    "IdempotencyRecord",
    "IdempotencyRecordExpiredError",
    "IdempotencyStorageError",
    "IdempotencyValidationError",
    "JsonResultAdapter",
    "NoOpIdempotencyMetrics",
    "PydanticResultAdapter",
    "ResultAdapter",
    "VoidResultAdapter",
    "async_idempotent",
]
