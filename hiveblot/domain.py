"""Small internal domain values independent of HTTP, persistence, and model output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecordSearchCriteria:
    target: str | None = None
    sample: str | None = None
    condition: str | None = None
