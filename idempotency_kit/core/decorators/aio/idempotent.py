import functools
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from idempotency_kit.core.protocols.adapter import ResultAdapter
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator

T = TypeVar("T")


def async_idempotent(
    operation: str,
    adapter: ResultAdapter[T],
    ttl_seconds: int | None = None,
    key_param: str = "idempotency_key",
    infra_param: str | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for asynchronous idempotent operations.

    Can be used on methods (finding coordinator in 'self') or standalone functions
    (finding coordinator in arguments).

    Args:
        operation: Unique operation name.
        adapter: Result adapter for encoding/decoding.
        ttl_seconds: Optional TTL for idempotency record in seconds.
            If not provided, uses value from coordinator settings or global default.
        key_param: Name of the argument containing the idempotency key.
        infra_param: Optional name of the argument or attribute containing AsyncIdempotencyCoordinator.
            If not provided, searches for AsyncIdempotencyCoordinator by type.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # 1. Resolve idempotency key
            idempotency_key = kwargs.get(key_param)
            if not idempotency_key:
                return await func(*args, **kwargs)

            # 2. Resolve coordinator
            coordinator: AsyncIdempotencyCoordinator | None = None

            # 2.1. Try to find by name if infra_param is provided
            if infra_param:
                if infra_param in kwargs:
                    coordinator = kwargs[infra_param]
                elif args and hasattr(args[0], infra_param):
                    coordinator = getattr(args[0], infra_param)

            # 2.2. Try to find by type if not found or infra_param is None
            if not coordinator:
                # Search in kwargs
                for val in kwargs.values():
                    if isinstance(val, AsyncIdempotencyCoordinator):
                        coordinator = val
                        break

                # Search in args (skipping self if it was already checked)
                if not coordinator:
                    for arg in args:
                        if isinstance(arg, AsyncIdempotencyCoordinator):
                            coordinator = arg
                            break
                        # Also check self attributes if arg is 'self'
                        # We do this because DI often injects into attributes
                        if hasattr(arg, "__dict__"):
                            for attr_val in vars(arg).values():
                                if isinstance(attr_val, AsyncIdempotencyCoordinator):
                                    coordinator = attr_val
                                    break
                        if coordinator:
                            break

            if not coordinator:
                # If no coordinator found but key is present, we might want to fail or proceed
                # Proceeding without idempotency is safer but should probably be logged
                return await func(*args, **kwargs)

            # 3. Delegate to coordinator
            return await coordinator.coordinate(
                operation,
                idempotency_key,
                ttl_seconds,
                adapter,
                func,
                *args,
                **kwargs,
            )

        return wrapper

    return decorator
