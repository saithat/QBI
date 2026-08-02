"""Shared primitives for strict, versioned HiveBlot contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

type SchemaVersion = Literal["1.0"]
SCHEMA_VERSION: SchemaVersion = "1.0"

type Identifier = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200),
]
type Sha256Digest = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^[a-f0-9]{64}$"),
]
type MediaType = Annotated[
    str,
    StringConstraints(
        strict=True,
        pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
    ),
]


class ContractModel(BaseModel):
    """Base for immutable data that crosses a process or persistence boundary."""

    model_config = ConfigDict(
        strict=True,
        extra="forbid",
        frozen=True,
        validate_default=True,
    )

    schema_version: SchemaVersion = SCHEMA_VERSION
