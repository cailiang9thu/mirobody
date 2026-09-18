"""The routers' response envelope: `{"code", "msg", "data"}`.

Every FastAPI router under `server/routers` answers with this shape: `code`
0 on success, an HTTP-like code on failure, `msg` a sentence for a human,
`data` the payload (always an object, empty on failure, so a client can read
`data.x` without a null check). It used to be defined three times: two
pydantic models in `public_router.py` that `indicator_router.py` imported
from there, and a `_ok`/`_err` pair of dict builders in `sharing_router.py`.
The dicts and the models serialized identically except that the dict error
carried `"data": {}` and the model error did not; the model now carries it
too, which is the only observable change.

`utils/http.py:json_response_with_code` answers with the same three keys.
It used to add `success` and to omit `data` when there was none, which is why
this docstring once said the two shapes were different and had to stay that
way: the web client was believed to depend on the Starlette-era one. It did
not — its single response handler reads `code === 0 || success` — so the two
are one shape now, and a client can read `data.x` off either without knowing
which router answered.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class StandardResponse(BaseModel):
    """A successful answer."""

    code: int = Field(default=0, description="0 on success")
    msg: str = Field(default="ok", description="Response message")
    data: dict[str, Any] = Field(default_factory=dict, description="Response data")


class ErrorResponse(BaseModel):
    """A failed answer. `code` is HTTP-like; the HTTP status itself stays 200
    for these routers, which is what the web client was written against."""

    code: int = Field(default=500, description="Error code")
    msg: str = Field(..., description="Error details")
    data: dict[str, Any] = Field(default_factory=dict, description="Always empty on failure")


def ok(data: dict[str, Any] | None = None, msg: str = "ok") -> StandardResponse:
    return StandardResponse(msg=msg, data=data if data is not None else {})


def err(code: int, msg: str) -> ErrorResponse:
    return ErrorResponse(code=code, msg=msg)
