"""Provenance-aware evidence indexing, retrieval, and frozen evaluation APIs."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    BoundingRegion,
    EvidenceCitation,
    EvidenceIndexDocument,
    EvidenceQuality,
    EvidenceRelation,
    EvidenceSearchFilters,
    EvidenceSearchQuery,
    IndexedEvidenceClaim,
    IndexedEvidenceObservation,
    ModelIdentifier,
    ObservationState,
    ResourceScope,
    RetrievalEvaluationConfiguration,
    RetrievalEvaluationQuery,
    RetrievalMetricAtK,
    RetrievalMode,
    RetrievalRelevanceJudgment,
    ReviewStatus,
    SearchIndexConfigurationRecord,
    SearchIndexVersionRecord,
    ToolIdentifier,
)
from hiveblot_search import (
    DuplicateEvidenceSearchRecord,
    EvidenceSearchError,
    EvidenceSearchNotFound,
    EvidenceSearchSecurityInvariant,
    EvidenceSearchService,
    InvalidEvidenceSearchState,
)
from hiveblot_storage import ArtifactNotFound, ArtifactService, ArtifactStorageError
from pydantic import ValidationError

from .artifact_dependencies import get_artifact_service
from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import require_resource, require_scope
from .evaluation_schemas import BoundingRegionResponse
from .search_dependencies import get_evidence_search_service
from .search_schemas import (
    ArtifactReferenceResponse,
    BuildEvidenceIndexRequest,
    CreateSearchIndexConfigurationRequest,
    EvidenceCitationResponse,
    EvidenceIndexDocumentInput,
    EvidenceIndexDocumentResponse,
    EvidenceSearchFiltersInput,
    EvidenceSearchFiltersResponse,
    EvidenceSearchHitResponse,
    EvidenceSearchRequest,
    EvidenceSearchResponse,
    FreezeRetrievalDatasetRequest,
    IndexedClaimResponse,
    IndexedObservationResponse,
    RetrievalAggregateMetricsResponse,
    RetrievalEvaluationConfigurationInput,
    RetrievalEvaluationConfigurationResponse,
    RetrievalEvaluationDatasetResponse,
    RetrievalEvaluationQueryInput,
    RetrievalEvaluationQueryResponse,
    RetrievalEvaluationRunResponse,
    RetrievalJudgmentResponse,
    RetrievalMetricAtKResponse,
    RetrievalQueryMetricResponse,
    RunRetrievalEvaluationRequest,
    SearchIndexConfigurationListResponse,
    SearchIndexConfigurationResponse,
    SearchIndexVersionListResponse,
    SearchIndexVersionResponse,
    SearchModelResponse,
    SearchToolResponse,
)

router = APIRouter(prefix="/api/v1", tags=["evidence search"])
SearchServiceDependency = Annotated[EvidenceSearchService, Depends(get_evidence_search_service)]
ArtifactServiceDependency = Annotated[ArtifactService, Depends(get_artifact_service)]


@router.post(
    "/evidence-index-configurations",
    response_model=SearchIndexConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_index_configuration(
    request: CreateSearchIndexConfigurationRequest,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SearchIndexConfigurationResponse:
    _require_platform_search_management(authorization, principal, request_id)
    try:
        record = service.create_configuration(
            index_name=request.index_name,
            configuration_version=request.configuration_version,
            embedding_model=ModelIdentifier(
                provider=request.embedding_model.provider,
                name=request.embedding_model.name,
                version=request.embedding_model.version,
            ),
            embedding_dimensions=request.embedding_dimensions,
            lexical_weight=request.lexical_weight,
            semantic_weight=request.semantic_weight,
            candidate_limit=request.candidate_limit,
            reranker=(
                ToolIdentifier(name=request.reranker.name, version=request.reranker.version)
                if request.reranker is not None
                else None
            ),
            reranker_weight=request.reranker_weight,
            created_by=principal.user_id,
        )
    except (EvidenceSearchError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _configuration_response(record)


@router.get(
    "/evidence-index-configurations",
    response_model=SearchIndexConfigurationListResponse,
)
def list_index_configurations(
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    index_name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchIndexConfigurationListResponse:
    _require_search(authorization, principal, request_id, target_type="evidence_index_config")
    try:
        records = service.list_configurations(index_name=index_name, limit=limit)
    except EvidenceSearchError as exc:
        _raise_http(exc)
    return SearchIndexConfigurationListResponse(
        configurations=tuple(_configuration_response(item) for item in records)
    )


@router.get(
    "/evidence-index-configurations/{configuration_id}",
    response_model=SearchIndexConfigurationResponse,
)
def get_index_configuration(
    configuration_id: UUID,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SearchIndexConfigurationResponse:
    _require_search(
        authorization,
        principal,
        request_id,
        target_type="evidence_index_config",
        target_id=configuration_id,
    )
    try:
        return _configuration_response(service.get_configuration(configuration_id))
    except EvidenceSearchError as exc:
        _raise_http(exc)


@router.post(
    "/evidence-index-versions",
    response_model=SearchIndexVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
def build_index_version(
    request: BuildEvidenceIndexRequest,
    service: SearchServiceDependency,
    artifacts: ArtifactServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SearchIndexVersionResponse:
    _require_platform_search_management(authorization, principal, request_id)
    documents = tuple(_document_input(item) for item in request.documents)
    for document in documents:
        _authorize_index_document(
            document,
            artifacts=artifacts,
            principal=principal,
            authorization=authorization,
            request_id=request_id,
        )
    try:
        record = service.build_version(
            configuration_id=request.configuration_id,
            index_version=request.index_version,
            documents=documents,
            created_by=principal.user_id,
        )
    except (EvidenceSearchError, ArtifactStorageError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _version_response(record)


@router.get("/evidence-index-versions", response_model=SearchIndexVersionListResponse)
def list_index_versions(
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
    index_name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SearchIndexVersionListResponse:
    _require_search(authorization, principal, request_id, target_type="evidence_index_version")
    try:
        records = service.list_versions(index_name=index_name, limit=limit)
    except EvidenceSearchError as exc:
        _raise_http(exc)
    return SearchIndexVersionListResponse(
        versions=tuple(_version_response(item) for item in records)
    )


@router.get(
    "/evidence-index-versions/{index_version_id}",
    response_model=SearchIndexVersionResponse,
)
def get_index_version(
    index_version_id: UUID,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SearchIndexVersionResponse:
    _require_search(
        authorization,
        principal,
        request_id,
        target_type="evidence_index_version",
        target_id=index_version_id,
    )
    try:
        return _version_response(service.get_version(index_version_id))
    except EvidenceSearchError as exc:
        _raise_http(exc)


@router.post(
    "/evidence-index-versions/{index_version_id}/activation",
    response_model=SearchIndexVersionResponse,
)
def activate_index_version(
    index_version_id: UUID,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> SearchIndexVersionResponse:
    _require_platform_search_management(
        authorization,
        principal,
        request_id,
        target_id=index_version_id,
    )
    try:
        return _version_response(service.activate(index_version_id))
    except EvidenceSearchError as exc:
        _raise_http(exc)


@router.post("/evidence-search", response_model=EvidenceSearchResponse)
def search_evidence(
    request: EvidenceSearchRequest,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> EvidenceSearchResponse:
    _require_search(authorization, principal, request_id, target_type="evidence_search")
    query = EvidenceSearchQuery(
        query=request.query,
        filters=_filters(request.filters),
        retrieval_mode=RetrievalMode(request.retrieval_mode),
        rerank=request.rerank,
        limit=request.limit,
        index_version_id=request.index_version_id,
        trace_id=request_id,
    )
    try:
        result = service.search(
            query,
            allowed_organization_ids=authorization.organizations_with_permission(
                principal,
                AuthorizationPermission.SEARCH,
            ),
            allow_inactive=principal.system or principal.platform_operator,
        )
    except (EvidenceSearchError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return EvidenceSearchResponse(
        query=result.query.query,
        trace_id=result.query.trace_id,
        index_version=_version_response(result.index_version),
        total_candidates=result.total_candidates,
        elapsed_ms=result.elapsed_ms,
        hits=tuple(_hit_response(item) for item in result.hits),
    )


@router.post(
    "/retrieval-evaluation-datasets",
    response_model=RetrievalEvaluationDatasetResponse,
    status_code=status.HTTP_201_CREATED,
)
def freeze_retrieval_dataset(
    request: FreezeRetrievalDatasetRequest,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> RetrievalEvaluationDatasetResponse:
    _require_platform_search_management(authorization, principal, request_id)
    try:
        record = service.freeze_evaluation_dataset(
            dataset_name=request.dataset_name,
            dataset_version=request.dataset_version,
            visibility=ArtifactVisibility(request.visibility),
            organization_id=request.organization_id,
            queries=tuple(_evaluation_query(item) for item in request.queries),
            frozen_by=principal.user_id,
        )
    except (EvidenceSearchError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _dataset_response(record)


@router.get(
    "/retrieval-evaluation-datasets/{dataset_id}",
    response_model=RetrievalEvaluationDatasetResponse,
)
def get_retrieval_dataset(
    dataset_id: UUID,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> RetrievalEvaluationDatasetResponse:
    _require_platform_search_management(
        authorization,
        principal,
        request_id,
        target_id=dataset_id,
    )
    try:
        return _dataset_response(service.get_evaluation_dataset(dataset_id))
    except EvidenceSearchError as exc:
        _raise_http(exc)


@router.post(
    "/retrieval-evaluation-runs",
    response_model=RetrievalEvaluationRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def run_retrieval_evaluation(
    request: RunRetrievalEvaluationRequest,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> RetrievalEvaluationRunResponse:
    _require_platform_search_management(
        authorization,
        principal,
        request_id,
        target_id=request.index_version_id,
    )
    try:
        record = service.evaluate(
            dataset_id=request.dataset_id,
            index_version_id=request.index_version_id,
            configuration=_evaluation_configuration(request.configuration),
        )
    except (EvidenceSearchError, ValidationError, ValueError) as exc:
        _raise_http(exc)
    return _evaluation_run_response(record)


@router.get(
    "/retrieval-evaluation-runs/{evaluation_run_id}",
    response_model=RetrievalEvaluationRunResponse,
)
def get_retrieval_evaluation(
    evaluation_run_id: UUID,
    service: SearchServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> RetrievalEvaluationRunResponse:
    _require_platform_search_management(
        authorization,
        principal,
        request_id,
        target_id=evaluation_run_id,
    )
    try:
        return _evaluation_run_response(service.get_evaluation_run(evaluation_run_id))
    except EvidenceSearchError as exc:
        _raise_http(exc)


def _authorize_index_document(
    document: EvidenceIndexDocument,
    *,
    artifacts: ArtifactService,
    principal: AuthenticatedPrincipal,
    authorization: AuthorizationService,
    request_id: UUID,
) -> None:
    document_scope = ResourceScope(
        visibility=document.visibility,
        organization_id=document.organization_id,
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
        scope=document_scope,
        target_type="evidence_index_document",
        target_id=document.document_id,
        request_id=request_id,
    )
    case_scope = require_resource(
        authorization,
        principal,
        AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
        target_type="evaluation_case",
        target_id=document.case_id,
        request_id=request_id,
    )
    _require_compatible_child_scope(document_scope, case_scope, "case")
    for target_type, target_id in (
        (
            "annotation_revision",
            document.source_annotation_revision_id,
        ),
        (
            "pipeline_publication",
            document.source_pipeline_publication_id,
        ),
    ):
        if target_id is None:
            continue
        source_scope = require_resource(
            authorization,
            principal,
            AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
        )
        _require_compatible_child_scope(document_scope, source_scope, target_type)
    for citation in document.citations:
        artifact_scope = require_resource(
            authorization,
            principal,
            AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
            target_type="artifact",
            target_id=citation.artifact.artifact_id,
            request_id=request_id,
        )
        _require_compatible_child_scope(document_scope, artifact_scope, "citation")
        try:
            stored = artifacts.get_artifact(citation.artifact.artifact_id)
        except ArtifactStorageError as exc:
            _raise_http(exc)
        if stored.sha256 != citation.artifact.sha256 or (
            stored.media_type != citation.artifact.media_type
            or stored.byte_size != citation.artifact.byte_size
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="citation metadata does not match the immutable artifact",
            )
        if citation.source_uri is not None and citation.source_uri != stored.source_uri:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="citation source URI does not match artifact provenance",
            )


def _require_compatible_child_scope(
    child: ResourceScope,
    parent: ResourceScope,
    label: str,
) -> None:
    if child.visibility is ArtifactVisibility.PUBLIC and parent.visibility is not child.visibility:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"public index documents cannot reference private {label}",
        )
    if (
        parent.visibility is ArtifactVisibility.ORGANIZATION_PRIVATE
        and parent.organization_id != child.organization_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"private {label} must match the index document organization",
        )


def _require_platform_search_management(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
    *,
    target_id: UUID | None = None,
) -> None:
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.PLATFORM_SEARCH_MANAGE,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type="evidence_index_management",
        target_id=target_id,
        request_id=request_id,
    )


def _require_search(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
    *,
    target_type: str,
    target_id: UUID | None = None,
) -> None:
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.SEARCH,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _document_input(value: EvidenceIndexDocumentInput) -> EvidenceIndexDocument:
    return EvidenceIndexDocument(
        document_id=value.document_id,
        case_id=value.case_id,
        paper_id=value.paper_id,
        paper_title=value.paper_title,
        figure_label=value.figure_label,
        experiment_label=value.experiment_label,
        proteins=value.proteins,
        biological_systems=value.biological_systems,
        treatments=value.treatments,
        conditions=value.conditions,
        observations=tuple(
            IndexedEvidenceObservation(
                observation_id=item.observation_id,
                statement=item.statement,
                relation=EvidenceRelation(item.relation),
                state=ObservationState(item.state),
                protein=item.protein,
                biological_system=item.biological_system,
                treatment=item.treatment,
                condition=item.condition,
                source_entity_id=item.source_entity_id,
                citation_artifact_ids=item.citation_artifact_ids,
            )
            for item in value.observations
        ),
        claims=tuple(
            IndexedEvidenceClaim(
                claim_id=item.claim_id,
                statement=item.statement,
                citation_artifact_ids=item.citation_artifact_ids,
            )
            for item in value.claims
        ),
        review_status=ReviewStatus(value.review_status),
        evidence_quality=EvidenceQuality(value.evidence_quality),
        citations=tuple(
            EvidenceCitation(
                artifact=ArtifactReference(
                    artifact_id=item.artifact.artifact_id,
                    sha256=item.artifact.sha256,
                    media_type=item.artifact.media_type,
                    byte_size=item.artifact.byte_size,
                ),
                source_uri=item.source_uri,
                label=item.label,
                page_number=item.page_number,
                region=(
                    BoundingRegion.model_validate(item.region.model_dump(mode="python"))
                    if item.region is not None
                    else None
                ),
            )
            for item in value.citations
        ),
        visibility=ArtifactVisibility(value.visibility),
        organization_id=value.organization_id,
        source_annotation_revision_id=value.source_annotation_revision_id,
        source_pipeline_publication_id=value.source_pipeline_publication_id,
        created_at=value.created_at,
    )


def _filters(value: EvidenceSearchFiltersInput) -> EvidenceSearchFilters:
    return EvidenceSearchFilters(
        proteins=value.proteins,
        biological_systems=value.biological_systems,
        treatments=value.treatments,
        conditions=value.conditions,
        review_statuses=tuple(ReviewStatus(item) for item in value.review_statuses),
        evidence_qualities=tuple(EvidenceQuality(item) for item in value.evidence_qualities),
    )


def _evaluation_query(value: RetrievalEvaluationQueryInput) -> RetrievalEvaluationQuery:
    return RetrievalEvaluationQuery(
        query_id=value.query_id,
        query=value.query,
        relevant=tuple(
            RetrievalRelevanceJudgment(
                document_id=item.document_id,
                relevance=item.relevance,
                required_citation_artifact_ids=item.required_citation_artifact_ids,
            )
            for item in value.relevant
        ),
        hard_negative_document_ids=value.hard_negative_document_ids,
        expected_filters=_filters(value.expected_filters),
        allowed_organization_ids=value.allowed_organization_ids,
    )


def _evaluation_configuration(
    value: RetrievalEvaluationConfigurationInput,
) -> RetrievalEvaluationConfiguration:
    return RetrievalEvaluationConfiguration(
        k_values=value.k_values,
        minimum_recall_at_largest_k=value.minimum_recall_at_largest_k,
        minimum_filter_correctness=value.minimum_filter_correctness,
        minimum_citation_correctness=value.minimum_citation_correctness,
        maximum_cross_tenant_leakage_rate=value.maximum_cross_tenant_leakage_rate,
        retrieval_mode=RetrievalMode(value.retrieval_mode),
        rerank=value.rerank,
    )


def _configuration_response(
    value: SearchIndexConfigurationRecord,
) -> SearchIndexConfigurationResponse:
    return SearchIndexConfigurationResponse(
        configuration_id=value.configuration_id,
        index_name=value.index_name,
        configuration_version=value.configuration_version,
        embedding_model=SearchModelResponse(
            provider=value.embedding_model.provider,
            name=value.embedding_model.name,
            version=value.embedding_model.version,
        ),
        embedding_dimensions=value.embedding_dimensions,
        lexical_weight=value.lexical_weight,
        semantic_weight=value.semantic_weight,
        candidate_limit=value.candidate_limit,
        reranker=(
            SearchToolResponse(name=value.reranker.name, version=value.reranker.version)
            if value.reranker is not None
            else None
        ),
        reranker_weight=value.reranker_weight,
        configuration_sha256=value.configuration_sha256,
        created_by=value.created_by,
        created_at=value.created_at,
    )


def _version_response(value: SearchIndexVersionRecord) -> SearchIndexVersionResponse:
    return SearchIndexVersionResponse(
        index_version_id=value.index_version_id,
        configuration_id=value.configuration_id,
        index_name=value.index_name,
        index_version=value.index_version,
        status=value.status.value,
        document_count=value.document_count,
        manifest_sha256=value.manifest_sha256,
        failure_reason=value.failure_reason,
        created_by=value.created_by,
        created_at=value.created_at,
        built_at=value.built_at,
        evaluated_at=value.evaluated_at,
        activated_at=value.activated_at,
        activation_evaluation_run_id=value.activation_evaluation_run_id,
    )


def _observation_response(value: IndexedEvidenceObservation) -> IndexedObservationResponse:
    return IndexedObservationResponse(
        observation_id=value.observation_id,
        statement=value.statement,
        relation=value.relation.value,
        state=value.state.value,
        protein=value.protein,
        biological_system=value.biological_system,
        treatment=value.treatment,
        condition=value.condition,
        source_entity_id=value.source_entity_id,
        citation_artifact_ids=value.citation_artifact_ids,
    )


def _document_response(value: EvidenceIndexDocument) -> EvidenceIndexDocumentResponse:
    return EvidenceIndexDocumentResponse(
        document_id=value.document_id,
        case_id=value.case_id,
        paper_id=value.paper_id,
        paper_title=value.paper_title,
        figure_label=value.figure_label,
        experiment_label=value.experiment_label,
        proteins=value.proteins,
        biological_systems=value.biological_systems,
        treatments=value.treatments,
        conditions=value.conditions,
        observations=tuple(_observation_response(item) for item in value.observations),
        claims=tuple(
            IndexedClaimResponse(
                claim_id=item.claim_id,
                statement=item.statement,
                citation_artifact_ids=item.citation_artifact_ids,
            )
            for item in value.claims
        ),
        review_status=value.review_status.value,
        evidence_quality=value.evidence_quality.value,
        citations=tuple(
            EvidenceCitationResponse(
                artifact=ArtifactReferenceResponse(
                    artifact_id=item.artifact.artifact_id,
                    sha256=item.artifact.sha256,
                    media_type=item.artifact.media_type,
                    byte_size=item.artifact.byte_size,
                ),
                artifact_url=f"/api/v1/artifacts/{item.artifact.artifact_id}",
                source_uri=item.source_uri,
                label=item.label,
                page_number=item.page_number,
                region=(
                    BoundingRegionResponse.model_validate_json(item.region.model_dump_json())
                    if item.region is not None
                    else None
                ),
            )
            for item in value.citations
        ),
        visibility=value.visibility.value,
        organization_id=value.organization_id,
        source_annotation_revision_id=value.source_annotation_revision_id,
        source_pipeline_publication_id=value.source_pipeline_publication_id,
        created_at=value.created_at,
    )


def _hit_response(value: object) -> EvidenceSearchHitResponse:
    from hiveblot_contracts import EvidenceSearchHit

    if not isinstance(value, EvidenceSearchHit):
        raise TypeError("search result must contain EvidenceSearchHit values")
    return EvidenceSearchHitResponse(
        rank=value.rank,
        score=value.score,
        lexical_score=value.lexical_score,
        semantic_score=value.semantic_score,
        reranker_score=value.reranker_score,
        document=_document_response(value.document),
        supporting_observations=tuple(
            _observation_response(item) for item in value.supporting_observations
        ),
        contradictory_observations=tuple(
            _observation_response(item) for item in value.contradictory_observations
        ),
    )


def _dataset_response(value: object) -> RetrievalEvaluationDatasetResponse:
    from hiveblot_contracts import RetrievalEvaluationDatasetRecord

    if not isinstance(value, RetrievalEvaluationDatasetRecord):
        raise TypeError("retrieval dataset response requires a canonical dataset")
    return RetrievalEvaluationDatasetResponse(
        dataset_id=value.dataset_id,
        dataset_name=value.dataset_name,
        dataset_version=value.dataset_version,
        visibility=value.visibility.value,
        organization_id=value.organization_id,
        queries=tuple(
            RetrievalEvaluationQueryResponse(
                query_id=item.query_id,
                query=item.query,
                relevant=tuple(
                    RetrievalJudgmentResponse(
                        document_id=judgment.document_id,
                        relevance=judgment.relevance,
                        required_citation_artifact_ids=(judgment.required_citation_artifact_ids),
                    )
                    for judgment in item.relevant
                ),
                hard_negative_document_ids=item.hard_negative_document_ids,
                expected_filters=EvidenceSearchFiltersResponse(
                    proteins=item.expected_filters.proteins,
                    biological_systems=item.expected_filters.biological_systems,
                    treatments=item.expected_filters.treatments,
                    conditions=item.expected_filters.conditions,
                    review_statuses=tuple(
                        status.value for status in item.expected_filters.review_statuses
                    ),
                    evidence_qualities=tuple(
                        quality.value for quality in item.expected_filters.evidence_qualities
                    ),
                ),
                allowed_organization_ids=item.allowed_organization_ids,
            )
            for item in value.queries
        ),
        content_sha256=value.content_sha256,
        frozen_by=value.frozen_by,
        frozen_at=value.frozen_at,
    )


def _metric_at_k_response(value: RetrievalMetricAtK) -> RetrievalMetricAtKResponse:
    return RetrievalMetricAtKResponse(k=value.k, value=value.value)


def _evaluation_run_response(value: object) -> RetrievalEvaluationRunResponse:
    from hiveblot_contracts import RetrievalEvaluationRunRecord

    if not isinstance(value, RetrievalEvaluationRunRecord):
        raise TypeError("retrieval evaluation response requires a canonical run")
    return RetrievalEvaluationRunResponse(
        evaluation_run_id=value.evaluation_run_id,
        dataset_id=value.dataset_id,
        dataset_sha256=value.dataset_sha256,
        index_version_id=value.index_version_id,
        scorer=SearchToolResponse(name=value.scorer.name, version=value.scorer.version),
        configuration=RetrievalEvaluationConfigurationResponse(
            k_values=value.configuration.k_values,
            minimum_recall_at_largest_k=(value.configuration.minimum_recall_at_largest_k),
            minimum_filter_correctness=value.configuration.minimum_filter_correctness,
            minimum_citation_correctness=value.configuration.minimum_citation_correctness,
            maximum_cross_tenant_leakage_rate=(
                value.configuration.maximum_cross_tenant_leakage_rate
            ),
            retrieval_mode=value.configuration.retrieval_mode.value,
            rerank=value.configuration.rerank,
        ),
        metrics=RetrievalAggregateMetricsResponse(
            recall_at_k=tuple(_metric_at_k_response(item) for item in value.metrics.recall_at_k),
            mean_reciprocal_rank=value.metrics.mean_reciprocal_rank,
            ndcg_at_k=tuple(_metric_at_k_response(item) for item in value.metrics.ndcg_at_k),
            filter_correctness=value.metrics.filter_correctness,
            citation_correctness=value.metrics.citation_correctness,
            cross_tenant_leakage_rate=value.metrics.cross_tenant_leakage_rate,
        ),
        query_metrics=tuple(
            RetrievalQueryMetricResponse(
                query_id=item.query_id,
                retrieved_document_ids=item.retrieved_document_ids,
                recall_at_k=tuple(_metric_at_k_response(metric) for metric in item.recall_at_k),
                ndcg_at_k=tuple(_metric_at_k_response(metric) for metric in item.ndcg_at_k),
                reciprocal_rank=item.reciprocal_rank,
                filter_correctness=item.filter_correctness,
                citation_correctness=item.citation_correctness,
                cross_tenant_leaks=item.cross_tenant_leaks,
            )
            for item in value.query_metrics
        ),
        passed=value.passed,
        created_at=value.created_at,
    )


def _raise_http(
    exc: EvidenceSearchError | ArtifactStorageError | ValidationError | ValueError,
) -> NoReturn:
    if isinstance(exc, (EvidenceSearchNotFound, ArtifactNotFound)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (DuplicateEvidenceSearchRecord, InvalidEvidenceSearchState)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, EvidenceSearchSecurityInvariant):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evidence search authorization invariant failed",
        ) from exc
    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
