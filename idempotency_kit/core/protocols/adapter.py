from typing import Any, Protocol, TypeVar

T = TypeVar("T")


class ResultAdapter(Protocol[T]):
    """Protocol for result adaptation (encoding/decoding)."""

    def encode(self, value: T) -> Any:
        """Encode the result to a format suitable for storage."""
        ...

    def decode(self, data: Any) -> T:
        """Decode the result from storage format back to the original type."""
        ...
