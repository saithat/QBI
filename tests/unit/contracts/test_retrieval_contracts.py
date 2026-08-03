from __future__ import annotations

from uuid import uuid4

import pytest
from hiveblot_contracts import (
    ArtifactVisibility,
    EvidenceIndexDocument,
    RetrievalEvaluationQuery,
    RetrievalRelevanceJudgment,
)
from pydantic import ValidationError

from tests.fakes.search import evidence_document


def test_index_document_is_strict_scoped_and_citation_complete() -> None:
    document = evidence_document(statement="Nutlin-3 increases p53 abundance.")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        EvidenceIndexDocument.model_validate(
            {**document.model_dump(mode="python"), "unknown": "rejected"}
        )

    with pytest.raises(ValidationError, match="organization-private"):
        EvidenceIndexDocument.model_validate(
            {
                **document.model_dump(mode="python"),
                "visibility": ArtifactVisibility.ORGANIZATION_PRIVATE,
                "organization_id": None,
            }
        )

    with pytest.raises(ValidationError, match="at least 1"):
        EvidenceIndexDocument.model_validate(
            {
                **document.model_dump(mode="python"),
                "citations": (),
            }
        )


def test_retrieval_judgments_reject_relevant_hard_negative_overlap() -> None:
    document_id = uuid4()

    with pytest.raises(ValidationError, match="disjoint"):
        RetrievalEvaluationQuery(
            query_id="p53-nutlin",
            query="p53 after Nutlin-3",
            relevant=(RetrievalRelevanceJudgment(document_id=document_id, relevance=3),),
            hard_negative_document_ids=(document_id,),
        )
