from __future__ import annotations

from uuid import uuid4

import pytest
from hiveblot_contracts import (
    RetrievalEvaluationQuery,
    RetrievalRelevanceJudgment,
)
from pydantic import ValidationError


def test_retrieval_judgments_reject_relevant_hard_negative_overlap() -> None:
    document_id = uuid4()

    with pytest.raises(ValidationError, match="disjoint"):
        RetrievalEvaluationQuery(
            query_id="p53-nutlin",
            query="p53 after Nutlin-3",
            relevant=(RetrievalRelevanceJudgment(document_id=document_id, relevance=3),),
            hard_negative_document_ids=(document_id,),
        )
