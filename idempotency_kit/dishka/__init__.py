"""Dishka providers for idempotency kit."""

from .common import IdempotencyProvider
from .protocols import IdempotencySettingsProtocol

try:
    from .aio import AsyncIdempotencyCoordinatorProvider, AsyncRedisIdempotencyProvider

    __all__ = [
        "AsyncIdempotencyCoordinatorProvider",
        "AsyncRedisIdempotencyProvider",
        "IdempotencyProvider",
        "IdempotencySettingsProtocol",
    ]
except ImportError:  # pragma: no cover
    __all__ = ["IdempotencyProvider", "IdempotencySettingsProtocol"]
