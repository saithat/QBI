from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    EvidenceCitation,
    EvidenceIndexDocument,
    EvidenceQuality,
    EvidenceRelation,
    IndexedEvidenceClaim,
    IndexedEvidenceObservation,
    ObservationState,
    ReviewStatus,
)

NOW = datetime(2026, 8, 2, 21, tzinfo=UTC)


def evidence_document(
    *,
    statement: str,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    visibility: ArtifactVisibility = ArtifactVisibility.PUBLIC,
    organization_id: UUID | None = None,
    protein: str = "p53",
    biological_system: str = "A549",
    treatment: str = "Nutlin-3",
    condition: str = "10 uM for 24 h",
    paper_id: str | None = None,
) -> EvidenceIndexDocument:
    document_id = uuid4()
    artifact_id = uuid4()
    citation = EvidenceCitation(
        artifact=ArtifactReference(
            artifact_id=artifact_id,
            sha256=artifact_id.hex * 2,
            media_type="image/png",
            byte_size=128,
        ),
        source_uri=f"https://example.test/source/{artifact_id}",
        label="Figure 2A",
        page_number=4,
    )
    observation = IndexedEvidenceObservation(
        observation_id=uuid4(),
        statement=statement,
        relation=relation,
        state=ObservationState.PRESENT,
        protein=protein,
        biological_system=biological_system,
        treatment=treatment,
        condition=condition,
        citation_artifact_ids=(artifact_id,),
    )
    claim = IndexedEvidenceClaim(
        claim_id=uuid4(),
        statement=statement,
        citation_artifact_ids=(artifact_id,),
    )
    return EvidenceIndexDocument(
        document_id=document_id,
        case_id=uuid4(),
        paper_id=paper_id or f"doi:10.1000/{document_id.hex[:10]}",
        paper_title="Western blot evidence in lung cancer cells",
        figure_label="Figure 2A",
        experiment_label="p53 response experiment",
        proteins=(protein,),
        biological_systems=(biological_system,),
        treatments=(treatment,),
        conditions=(condition,),
        observations=(observation,),
        claims=(claim,),
        review_status=ReviewStatus.ADJUDICATED,
        evidence_quality=EvidenceQuality.PUBLICATION_FIGURE,
        citations=(citation,),
        visibility=visibility,
        organization_id=organization_id,
        source_annotation_revision_id=uuid4(),
        created_at=NOW,
    )
