"""The request fingerprint: what makes two calls under one key the same request."""

import hashlib
import json
from typing import Any

from pydantic_core import to_jsonable_python


def fingerprint_of(**values: Any) -> str:
    """Hash the named values into the fingerprint of a request.

    Pydantic models, dataclasses, UUIDs, datetimes, Decimals and the JSON types are turned
    into JSON-able Python first, keys are sorted, and the SHA-256 hex digest is returned, so
    two equal payloads built in different orders agree. This is what ``async_idempotent``
    computes from ``fingerprint_params``; a caller of ``coordinate()`` uses it directly.

    Raises:
        pydantic_core.PydanticSerializationError: a value has no JSON form
    """
    canonical = json.dumps(to_jsonable_python(values), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
