"""Deterministic local vector embeddings and optional lexical reranking."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable
from typing import Protocol

from hiveblot_contracts import ModelIdentifier, ToolIdentifier

TOKEN_PATTERN = re.compile(r"[a-z0-9][a-z0-9+_.-]*")
DEFAULT_EMBEDDING_MODEL = ModelIdentifier(
    provider="hiveblot",
    name="deterministic-scientific-hashing",
    version="1.0.0",
)
DEFAULT_RERANKER = ToolIdentifier(
    name="token-overlap-reranker",
    version="1.0.0",
)

_SYNONYMS = {
    "westernblot": "western-blot",
    "immunoblot": "western-blot",
    "immunoblotting": "western-blot",
    "wb": "western-blot",
    "vehicle-treated": "control",
    "untreated": "control",
}


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> ModelIdentifier: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, text: str) -> tuple[float, ...]: ...


class EvidenceReranker(Protocol):
    @property
    def tool(self) -> ToolIdentifier: ...

    def score(self, query: str, document_text: str) -> float: ...


class DeterministicScientificEmbedder:
    """Offline token and character-ngram vectors with a stable model identity.

    This provider is intentionally reproducible and dependency-free. The interface is also the
    boundary for replacing it with a biomedical embedding model without changing stored contracts.
    """

    def __init__(self, *, dimensions: int = 256) -> None:
        if not 8 <= dimensions <= 4096:
            raise ValueError("embedding dimensions must be between 8 and 4096")
        self._dimensions = dimensions

    @property
    def model(self) -> ModelIdentifier:
        return DEFAULT_EMBEDDING_MODEL

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> tuple[float, ...]:
        vector = [0.0] * self._dimensions
        tokens = normalized_tokens(text)
        for token in tokens:
            _accumulate(vector, f"token:{token}", weight=2.0)
            padded = f"^{token}$"
            for index in range(max(1, len(padded) - 2)):
                _accumulate(vector, f"tri:{padded[index : index + 3]}", weight=0.35)
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class TokenOverlapReranker:
    @property
    def tool(self) -> ToolIdentifier:
        return DEFAULT_RERANKER

    def score(self, query: str, document_text: str) -> float:
        query_tokens = set(normalized_tokens(query))
        document_tokens = set(normalized_tokens(document_text))
        if not query_tokens:
            return 0.0
        return len(query_tokens & document_tokens) / len(query_tokens)


def normalized_tokens(text: str) -> tuple[str, ...]:
    return tuple(
        _SYNONYMS.get(token, token)
        for token in TOKEN_PATTERN.findall(text.casefold().replace("western blot", "western-blot"))
    )


def cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = tuple(left)
    right_values = tuple(right)
    if len(left_values) != len(right_values):
        raise ValueError("embedding dimensions do not match")
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    similarity = sum(a * b for a, b in zip(left_values, right_values, strict=True)) / (
        left_norm * right_norm
    )
    return max(0.0, min(1.0, similarity))


def _accumulate(vector: list[float], feature: str, *, weight: float) -> None:
    digest = hashlib.sha256(feature.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % len(vector)
    sign = 1.0 if digest[8] & 1 else -1.0
    vector[bucket] += weight * sign
