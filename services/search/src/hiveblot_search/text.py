"""Canonical search text, normalized terms, and content-derived identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence

from hiveblot_contracts import (
    EvidenceIndexDocument,
    RetrievalEvaluationQuery,
    SearchIndexConfigurationRecord,
)


def document_search_text(document: EvidenceIndexDocument) -> str:
    values: list[str] = [document.paper_id]
    values.extend(
        value
        for value in (
            document.paper_title,
            document.figure_label,
            document.experiment_label,
        )
        if value is not None
    )
    values.extend(document.proteins)
    values.extend(document.biological_systems)
    values.extend(document.treatments)
    values.extend(document.conditions)
    for observation in document.observations:
        values.append(observation.statement)
        values.extend(
            value
            for value in (
                observation.protein,
                observation.biological_system,
                observation.treatment,
                observation.condition,
            )
            if value is not None
        )
    values.extend(claim.statement for claim in document.claims)
    return "\n".join(value.strip() for value in values if value.strip())


def normalized_terms(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value.strip().casefold() for value in values if value.strip()}))


def canonical_sha256(value: object) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def configuration_sha256(
    record: SearchIndexConfigurationRecord,
) -> str:
    payload = record.model_dump(
        mode="json",
        exclude={"configuration_id", "configuration_sha256", "created_by", "created_at"},
    )
    return canonical_sha256(payload)


def index_manifest_sha256(
    configuration: SearchIndexConfigurationRecord,
    documents: Sequence[EvidenceIndexDocument],
) -> str:
    payload = {
        "configuration_sha256": configuration.configuration_sha256,
        "documents": [
            document.model_dump(mode="json")
            for document in sorted(documents, key=lambda item: str(item.document_id))
        ],
    }
    return canonical_sha256(payload)


def retrieval_dataset_sha256(queries: Sequence[RetrievalEvaluationQuery]) -> str:
    return canonical_sha256(
        [query.model_dump(mode="json") for query in sorted(queries, key=lambda item: item.query_id)]
    )
