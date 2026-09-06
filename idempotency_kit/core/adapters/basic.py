from typing import Any, TypeVar

from pydantic import BaseModel

from idempotency_kit.core.exceptions import IdempotencyValidationError
from idempotency_kit.core.protocols.adapter import ResultAdapter

T = TypeVar("T", bound=BaseModel)


class PydanticResultAdapter(ResultAdapter[T]):
    """Adapter for Pydantic models.

    It has no representation for an absent result: an action that may return ``None``
    wants ``VoidResultAdapter`` or ``JsonResultAdapter`` instead.
    """

    def __init__(self, model_class: type[T]) -> None:
        self.model_class = model_class

    def encode(self, value: T) -> Any:
        # Encoding None to null would store a record this adapter can never decode again,
        # turning every later replay into a miss. Refusing it leaves the operation
        # uncached and says why, which the coordinator reports and swallows.
        model: BaseModel | None = value
        if model is None:
            msg = (
                f"{type(self).__name__}({self.model_class.__name__}) cannot encode None. "
                "Use VoidResultAdapter for an action that returns None, or JsonResultAdapter "
                "for one that may."
            )
            raise IdempotencyValidationError(msg)
        return model.model_dump(mode="json")

    def decode(self, data: Any) -> T:
        # Only a null payload is undecodable: an empty mapping or list is a model that
        # happens to dump to nothing.
        if data is None:
            msg = "cannot decode a null idempotency payload"
            raise ValueError(msg)
        return self.model_class.model_validate(data)


class JsonResultAdapter(ResultAdapter[Any]):
    """Adapter for results that already are JSON values (dict, list, str, number, bool or ``None``)."""

    def encode(self, value: Any) -> Any:
        return value

    def decode(self, data: Any) -> Any:
        return data


class VoidResultAdapter(ResultAdapter[None]):
    """Adapter for functions that return ``None``: the record stores JSON ``null`` and decodes back to ``None``."""

    def encode(self, value: None) -> Any:
        return None

    def decode(self, data: Any) -> None:
        return None
