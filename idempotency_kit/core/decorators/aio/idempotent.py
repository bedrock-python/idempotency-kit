import functools
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from idempotency_kit.core.fingerprint import fingerprint_of
from idempotency_kit.core.protocols.adapter import ResultAdapter
from idempotency_kit.core.services.aio.coordinator import AsyncIdempotencyCoordinator

T = TypeVar("T")

logger = logging.getLogger(__name__)

_POSITIONAL_KINDS = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)


def _positional_index(func: Callable[..., Any], key_param: str) -> int | None:
    """Index at which ``key_param`` can arrive positionally, or ``None`` if it cannot."""
    try:
        parameters = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return None
    for index, parameter in enumerate(parameters):
        if parameter.name == key_param and parameter.kind in _POSITIONAL_KINDS:
            return index
    return None


def _fingerprint_resolver(
    func: Callable[..., Any], fingerprint_params: tuple[str, ...] | None
) -> Callable[[tuple[Any, ...], dict[str, Any]], str] | None:
    """Build the function that fingerprints a call from the named parameters, or ``None`` when there are none.

    Resolved at decoration time so that a parameter the function does not have, or a
    signature ``inspect`` cannot describe, fails at import rather than on the first call.
    """
    if not fingerprint_params:
        return None
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError) as e:
        raise TypeError(f"fingerprint_params needs a signature inspect can describe; {func!r} has none") from e
    unknown = [name for name in fingerprint_params if name not in signature.parameters]
    if unknown:
        raise TypeError(f"fingerprint_params names parameters {func.__qualname__} does not have: {unknown}")

    def resolve(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        # Defaults applied, so an argument passed at its default and one left out agree.
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return fingerprint_of(**{name: bound.arguments[name] for name in fingerprint_params})

    return resolve


def async_idempotent(
    operation: str,
    adapter: ResultAdapter[T],
    ttl_seconds: int | None = None,
    key_param: str = "idempotency_key",
    infra_param: str | None = None,
    fingerprint_params: tuple[str, ...] | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for asynchronous idempotent operations.

    Can be used on methods (finding coordinator in 'self') or standalone functions
    (finding coordinator in arguments).

    Args:
        operation: Unique operation name.
        adapter: Result adapter for encoding/decoding.
        ttl_seconds: Optional TTL for idempotency record in seconds.
            If not provided, uses value from coordinator settings or global default.
        key_param: Name of the argument containing the idempotency key. Read from the
            keyword arguments, or from the positional arguments when the parameter can
            be passed positionally.
        infra_param: Optional name of the argument or attribute containing AsyncIdempotencyCoordinator.
            If not provided, searches for AsyncIdempotencyCoordinator by type.
        fingerprint_params: Names of the parameters whose values identify the request. Their
            bound values, defaults applied, are hashed with ``fingerprint_of`` and stored with
            the record; a later call under the same key with a different fingerprint raises
            ``IdempotencyKeyReuseError``. ``None`` means the key alone is the identity.
    """

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        key_index = _positional_index(func, key_param)
        fingerprint = _fingerprint_resolver(func, fingerprint_params)

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # 1. Resolve idempotency key
            idempotency_key = _resolve_key(args, kwargs)
            if not idempotency_key:
                return await func(*args, **kwargs)

            # 2. Resolve coordinator
            coordinator = _resolve_coordinator(args, kwargs)

            if coordinator is None:
                # Proceeding without idempotency keeps the operation available, but it is
                # never what the decorator was put there for: say so loudly enough to be
                # caught by whoever renamed the attribute or forgot the argument.
                logger.warning(
                    "No idempotency coordinator found; running the operation without idempotency",
                    extra={
                        "operation": operation,
                        "idempotency_key": idempotency_key,
                        "infra_param": infra_param,
                    },
                )
                return await func(*args, **kwargs)

            # 3. Delegate to coordinator
            if fingerprint is not None:
                kwargs = {"idempotency_fingerprint": fingerprint(args, kwargs), **kwargs}
            return await coordinator.coordinate(
                operation,
                idempotency_key,
                ttl_seconds,
                adapter,
                func,
                *args,
                **kwargs,
            )

        def _resolve_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            if key_param in kwargs:
                return kwargs[key_param]
            if key_index is not None and key_index < len(args):
                return args[key_index]
            return None

        def _resolve_coordinator(args: tuple[Any, ...], kwargs: dict[str, Any]) -> AsyncIdempotencyCoordinator | None:
            # By name, if infra_param is provided
            if infra_param:
                named: AsyncIdempotencyCoordinator | None = kwargs.get(infra_param)
                if named is None and args:
                    named = getattr(args[0], infra_param, None)
                if named is not None:
                    return named

            # By type, in the keyword arguments
            for val in kwargs.values():
                if isinstance(val, AsyncIdempotencyCoordinator):
                    return val

            # By type, in the positional arguments, then in their attributes:
            # DI often injects the coordinator into an attribute of 'self'.
            for arg in args:
                if isinstance(arg, AsyncIdempotencyCoordinator):
                    return arg
                if hasattr(arg, "__dict__"):
                    for attr_val in vars(arg).values():
                        if isinstance(attr_val, AsyncIdempotencyCoordinator):
                            return attr_val
            return None

        return wrapper

    return decorator
