from typing import Any, TypeVar

from pydantic import BaseModel

from idempotency_kit.core.protocols.adapter import ResultAdapter

T = TypeVar("T", bound=BaseModel)


class PydanticResultAdapter(ResultAdapter[T]):
    """Adapter for Pydantic models."""

    def __init__(self, model_class: type[T]) -> None:
        self.model_class = model_class

    def encode(self, value: T) -> Any:
        return value.model_dump(mode="json") if value else None

    def decode(self, data: Any) -> T:
        if not data:
            msg = "cannot decode empty idempotency payload"
            raise ValueError(msg)
        return self.model_class.model_validate(data)


class JsonResultAdapter(ResultAdapter[Any]):
    """Adapter for JSON-serializable types (dict, list, etc.)."""

    def encode(self, value: Any) -> Any:
        return value

    def decode(self, data: Any) -> Any:
        return data


class VoidResultAdapter(ResultAdapter[None]):
    """Adapter for functions that return None."""

    def encode(self, value: None) -> Any:
        return None

    def decode(self, data: Any) -> None:
        return None
