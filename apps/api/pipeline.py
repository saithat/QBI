"""Versioned pipeline definitions, invocation results, replay, and publication API."""

from __future__ import annotations

from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from hiveblot_auth import AuthorizationService
from hiveblot_contracts import (
    ArtifactReference,
    ArtifactVisibility,
    AuthenticatedPrincipal,
    AuthorizationPermission,
    BoundingRegion,
    ComponentEvidenceReference,
    ComponentInvocationRecord,
    FailedComponentResult,
    InvocationFailureKind,
    ModelIdentifier,
    ModelPipelineComponent,
    OutputSchemaIdentifier,
    PipelineComponentDefinition,
    PipelineDefinitionRecord,
    PipelineIdentifier,
    PipelinePublicationRecord,
    PipelineRunDetail,
    PipelineRunRecord,
    PublishedPipelineValue,
    ResourceScope,
    SucceededComponentResult,
    ToolIdentifier,
    ToolPipelineComponent,
    ValidationIssue,
    ValidationSeverity,
)
from hiveblot_evaluation import (
    ConcurrencyConflict,
    DuplicateEvaluationRecord,
    EvaluationError,
    EvaluationNotFound,
    InvalidEvaluationState,
    PipelineRegistryService,
)
from hiveblot_storage import ArtifactNotFound, ArtifactStorageError
from pydantic import ValidationError

from .auth_dependencies import (
    AuthorizationServiceDependency,
    PrincipalDependency,
    RequestIdDependency,
)
from .authorization import (
    private_creation_scope,
    require_resource,
    require_scope,
    visible_records,
)
from .evaluation_schemas import (
    BoundingRegionInput,
    BoundingRegionResponse,
    ValidationIssueInput,
    ValidationIssueResponse,
)
from .pipeline_dependencies import get_pipeline_registry_service
from .pipeline_schemas import (
    ArtifactReferenceResponse,
    CompleteComponentResultRequest,
    ComponentEvidenceInput,
    ComponentEvidenceResponse,
    ComponentInvocationResponse,
    ComponentResultResponse,
    CreatePipelineDefinitionRequest,
    CreatePipelineRunRequest,
    ModelComponentInput,
    OutputSchemaInput,
    OutputSchemaResponse,
    PipelineComponentInput,
    PipelineComponentResponse,
    PipelineDefinitionListResponse,
    PipelineDefinitionResponse,
    PipelinePublicationResponse,
    PipelineRunDetailResponse,
    PipelineRunListResponse,
    PipelineRunResponse,
    PublishedValueInput,
    PublishedValueResponse,
    PublishPipelineRunRequest,
    ReplayComponentRequest,
    SuccessfulComponentResultRequest,
)

router = APIRouter(prefix="/api/v1", tags=["pipeline registry"])
PipelineServiceDependency = Annotated[
    PipelineRegistryService,
    Depends(get_pipeline_registry_service),
]


@router.post(
    "/pipeline-definitions",
    response_model=PipelineDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pipeline_definition(
    request: CreatePipelineDefinitionRequest,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineDefinitionResponse:
    _require_public(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_MANAGE,
        request_id,
        target_type="pipeline_definition",
    )
    try:
        record = service.register_definition(
            pipeline=PipelineIdentifier(
                name=request.pipeline.name,
                version=request.pipeline.version,
            ),
            description=request.description,
            components=tuple(_component(item) for item in request.components),
            configuration_json=request.configuration_json,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _definition_response(record)


@router.get(
    "/pipeline-definitions",
    response_model=PipelineDefinitionListResponse,
)
def list_pipeline_definitions(
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineDefinitionListResponse:
    _require_public(
        authorization,
        principal,
        AuthorizationPermission.TRACE_READ,
        request_id,
        target_type="pipeline_definition",
    )
    try:
        records = service.list_definitions()
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return PipelineDefinitionListResponse(
        definitions=tuple(_definition_response(record) for record in records)
    )


@router.get(
    "/pipeline-definitions/{definition_id}",
    response_model=PipelineDefinitionResponse,
)
def get_pipeline_definition(
    definition_id: UUID,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineDefinitionResponse:
    _require_public(
        authorization,
        principal,
        AuthorizationPermission.TRACE_READ,
        request_id,
        target_type="pipeline_definition",
        target_id=definition_id,
    )
    try:
        record = service.get_definition(definition_id)
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _definition_response(record)


@router.post(
    "/evaluation-cases/{case_id}/pipeline-runs",
    response_model=PipelineRunDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pipeline_run(
    case_id: UUID,
    request: CreatePipelineRunRequest,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineRunDetailResponse:
    case_scope = require_resource(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_MANAGE,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    run_scope = private_creation_scope(
        authorization,
        principal,
        case_scope=case_scope,
        requested_visibility=(
            ArtifactVisibility(request.visibility) if request.visibility is not None else None
        ),
        requested_organization_id=request.organization_id,
        permission=AuthorizationPermission.EVALUATION_MANAGE,
        resource_name="pipeline runs",
    )
    require_scope(
        authorization,
        principal,
        AuthorizationPermission.EVALUATION_MANAGE,
        scope=run_scope,
        target_type="pipeline_run",
        request_id=request_id,
    )
    _authorize_artifacts(
        request.input_artifact_ids,
        authorization,
        principal,
        request_id,
    )
    try:
        detail = service.create_run(
            case_id,
            definition_id=request.definition_id,
            input_artifact_ids=request.input_artifact_ids,
            configuration_json=request.configuration_json,
            trace_id=request.trace_id,
            visibility=run_scope.visibility,
            organization_id=run_scope.organization_id,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _run_detail_response(detail)


@router.get(
    "/evaluation-cases/{case_id}/pipeline-runs",
    response_model=PipelineRunListResponse,
)
def list_pipeline_runs(
    case_id: UUID,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineRunListResponse:
    require_resource(
        authorization,
        principal,
        AuthorizationPermission.TRACE_READ,
        target_type="evaluation_case",
        target_id=case_id,
        request_id=request_id,
    )
    try:
        records = visible_records(
            service.list_runs(case_id),
            authorization=authorization,
            principal=principal,
            permission=AuthorizationPermission.TRACE_READ,
            scope_of=lambda detail: ResourceScope(
                visibility=detail.run.visibility,
                organization_id=detail.run.organization_id,
            ),
        )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return PipelineRunListResponse(
        case_id=case_id,
        runs=tuple(_run_detail_response(record) for record in records),
    )


@router.get(
    "/pipeline-runs/{run_id}",
    response_model=PipelineRunDetailResponse,
)
def get_pipeline_run(
    run_id: UUID,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelineRunDetailResponse:
    _require_pipeline_resource(
        "pipeline_run",
        run_id,
        AuthorizationPermission.TRACE_READ,
        authorization,
        principal,
        request_id,
    )
    try:
        detail = service.get_run_detail(run_id)
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _run_detail_response(detail)


@router.post(
    "/component-invocations/{invocation_id}/results",
    response_model=ComponentInvocationResponse,
)
def complete_component_invocation(
    invocation_id: UUID,
    request: CompleteComponentResultRequest,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ComponentInvocationResponse:
    _require_pipeline_resource(
        "component_invocation",
        invocation_id,
        AuthorizationPermission.EVALUATION_MANAGE,
        authorization,
        principal,
        request_id,
    )
    _authorize_artifacts(
        (
            *request.output_artifact_ids,
            *(item.artifact_id for item in request.evidence),
            *(
                artifact_id
                for issue in request.validation_issues
                for artifact_id in issue.evidence_artifact_ids
            ),
        ),
        authorization,
        principal,
        request_id,
    )
    try:
        evidence = tuple(_evidence(service, item) for item in request.evidence)
        issues = tuple(_validation_issue(item) for item in request.validation_issues)
        if isinstance(request, SuccessfulComponentResultRequest):
            record = service.complete_success(
                invocation_id,
                output_schema=_output_schema(request.output_schema),
                raw_output_json=request.raw_output_json,
                normalized_output_json=request.normalized_output_json,
                output_artifact_ids=request.output_artifact_ids,
                evidence=evidence,
                validation_issues=issues,
                latency_ms=request.latency_ms,
                cost_microusd=request.cost_microusd,
            )
        else:
            record = service.complete_failure(
                invocation_id,
                failure_kind=InvocationFailureKind(request.failure_kind),
                error_code=request.error_code,
                error_message=request.error_message,
                raw_output_json=request.raw_output_json,
                normalized_output_json=request.normalized_output_json,
                output_artifact_ids=request.output_artifact_ids,
                evidence=evidence,
                validation_issues=issues,
                latency_ms=request.latency_ms,
                cost_microusd=request.cost_microusd,
            )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _invocation_response(record)


@router.post(
    "/component-invocations/{invocation_id}/replays",
    response_model=ComponentInvocationResponse,
    status_code=status.HTTP_201_CREATED,
)
def replay_component_invocation(
    invocation_id: UUID,
    request: ReplayComponentRequest,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> ComponentInvocationResponse:
    _require_pipeline_resource(
        "component_invocation",
        invocation_id,
        AuthorizationPermission.EVALUATION_MANAGE,
        authorization,
        principal,
        request_id,
    )
    try:
        record = service.replay(
            invocation_id,
            configuration_json=request.configuration_json,
            trace_id=request.trace_id,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _invocation_response(record)


@router.post(
    "/pipeline-runs/{run_id}/publications",
    response_model=PipelinePublicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def publish_pipeline_run(
    run_id: UUID,
    request: PublishPipelineRunRequest,
    service: PipelineServiceDependency,
    principal: PrincipalDependency,
    authorization: AuthorizationServiceDependency,
    request_id: RequestIdDependency,
) -> PipelinePublicationResponse:
    _require_pipeline_resource(
        "pipeline_run",
        run_id,
        AuthorizationPermission.EVALUATION_MANAGE,
        authorization,
        principal,
        request_id,
    )
    _authorize_artifacts(
        (
            *request.output_artifact_ids,
            *(evidence.artifact_id for value in request.values for evidence in value.evidence),
        ),
        authorization,
        principal,
        request_id,
    )
    try:
        publication = service.publish(
            run_id,
            output_schema=_output_schema(request.output_schema),
            normalized_output_json=request.normalized_output_json,
            values=tuple(_published_value(service, value) for value in request.values),
            output_artifact_ids=request.output_artifact_ids,
            trace_id=request.trace_id,
        )
    except (EvaluationError, ArtifactStorageError, ValidationError) as exc:
        _raise_http(exc)
    return _publication_response(publication)


def _require_public(
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    permission: AuthorizationPermission,
    request_id: UUID,
    *,
    target_type: str,
    target_id: UUID | None = None,
) -> None:
    require_scope(
        authorization,
        principal,
        permission,
        scope=ResourceScope(visibility=ArtifactVisibility.PUBLIC),
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _require_pipeline_resource(
    target_type: str,
    target_id: UUID,
    permission: AuthorizationPermission,
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
) -> None:
    require_resource(
        authorization,
        principal,
        permission,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
    )


def _authorize_artifacts(
    artifact_ids: tuple[UUID, ...],
    authorization: AuthorizationService,
    principal: AuthenticatedPrincipal,
    request_id: UUID,
) -> None:
    for artifact_id in dict.fromkeys(artifact_ids):
        require_resource(
            authorization,
            principal,
            AuthorizationPermission.ARTIFACT_READ,
            target_type="artifact",
            target_id=artifact_id,
            request_id=request_id,
        )


def _component(value: PipelineComponentInput) -> PipelineComponentDefinition:
    if isinstance(value, ModelComponentInput):
        return ModelPipelineComponent(
            component_key=value.component_key,
            depends_on=value.depends_on,
            output_schema=_output_schema(value.output_schema),
            configuration_json=value.configuration_json,
            model=ModelIdentifier(
                provider=value.provider,
                name=value.model_name,
                version=value.model_version,
            ),
            prompt_version=value.prompt_version,
        )
    return ToolPipelineComponent(
        component_key=value.component_key,
        depends_on=value.depends_on,
        output_schema=_output_schema(value.output_schema),
        configuration_json=value.configuration_json,
        tool=ToolIdentifier(name=value.tool_name, version=value.tool_version),
    )


def _output_schema(value: OutputSchemaInput) -> OutputSchemaIdentifier:
    return OutputSchemaIdentifier(name=value.name, version=value.version)


def _bounding_region(value: BoundingRegionInput) -> BoundingRegion:
    return BoundingRegion(
        region_id=value.region_id,
        source_artifact_id=value.source_artifact_id,
        x=value.x,
        y=value.y,
        width=value.width,
        height=value.height,
        canvas_width=value.canvas_width,
        canvas_height=value.canvas_height,
        page_number=value.page_number,
    )


def _evidence(
    service: PipelineRegistryService,
    value: ComponentEvidenceInput,
) -> ComponentEvidenceReference:
    return service.evidence_reference(
        artifact_id=value.artifact_id,
        field_path=value.field_path,
        region=_bounding_region(value.region) if value.region is not None else None,
        description=value.description,
    )


def _validation_issue(value: ValidationIssueInput) -> ValidationIssue:
    return ValidationIssue(
        issue_id=value.issue_id,
        severity=ValidationSeverity(value.severity),
        code=value.code,
        message=value.message,
        field_path=value.field_path,
        evidence_artifact_ids=value.evidence_artifact_ids,
    )


def _published_value(
    service: PipelineRegistryService,
    value: PublishedValueInput,
) -> PublishedPipelineValue:
    return PublishedPipelineValue(
        field_path=value.field_path,
        producing_invocation_id=value.producing_invocation_id,
        result_path=value.result_path,
        value_json=value.value_json,
        evidence=tuple(_evidence(service, item) for item in value.evidence),
    )


def _artifact_response(value: ArtifactReference) -> ArtifactReferenceResponse:
    return ArtifactReferenceResponse(
        artifact_id=value.artifact_id,
        sha256=value.sha256,
        media_type=value.media_type,
        byte_size=value.byte_size,
    )


def _component_response(value: PipelineComponentDefinition) -> PipelineComponentResponse:
    if isinstance(value, ModelPipelineComponent):
        producer_name = value.model.name
        producer_version = value.model.version
        provider = value.model.provider
        prompt_version = value.prompt_version
    else:
        producer_name = value.tool.name
        producer_version = value.tool.version
        provider = None
        prompt_version = None
    return PipelineComponentResponse(
        component_type=value.component_type,
        component_key=value.component_key,
        depends_on=value.depends_on,
        output_schema=OutputSchemaResponse(
            name=value.output_schema.name,
            version=value.output_schema.version,
        ),
        configuration_json=value.configuration_json,
        producer_name=producer_name,
        producer_version=producer_version,
        provider=provider,
        prompt_version=prompt_version,
    )


def _definition_response(value: PipelineDefinitionRecord) -> PipelineDefinitionResponse:
    return PipelineDefinitionResponse(
        definition_id=value.definition_id,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        description=value.description,
        components=tuple(_component_response(item) for item in value.components),
        configuration_json=value.configuration_json,
        created_at=value.created_at,
    )


def _evidence_response(value: ComponentEvidenceReference) -> ComponentEvidenceResponse:
    return ComponentEvidenceResponse(
        artifact=_artifact_response(value.artifact),
        field_path=value.field_path,
        region=(
            None
            if value.region is None
            else BoundingRegionResponse.model_validate(value.region.model_dump(mode="python"))
        ),
        description=value.description,
    )


def _result_response(
    value: SucceededComponentResult | FailedComponentResult,
) -> ComponentResultResponse:
    failed = value if isinstance(value, FailedComponentResult) else None
    return ComponentResultResponse(
        result_type=value.result_type,
        failure_kind=failed.failure_kind.value if failed is not None else None,
        error_code=failed.error_code if failed is not None else None,
        error_message=failed.error_message if failed is not None else None,
        output_schema=OutputSchemaResponse(
            name=value.output_schema.name,
            version=value.output_schema.version,
        ),
        raw_output_json=value.raw_output_json,
        normalized_output_json=value.normalized_output_json,
        output_artifacts=tuple(_artifact_response(item) for item in value.output_artifacts),
        evidence=tuple(_evidence_response(item) for item in value.evidence),
        validation_issues=tuple(
            ValidationIssueResponse.model_validate(item.model_dump(mode="python"))
            for item in value.validation_issues
        ),
        latency_ms=value.latency_ms,
        cost_microusd=value.cost_microusd,
        completed_at=value.completed_at,
    )


def _invocation_response(value: ComponentInvocationRecord) -> ComponentInvocationResponse:
    return ComponentInvocationResponse(
        invocation_id=value.invocation_id,
        run_id=value.run_id,
        component=_component_response(value.component),
        status=value.status.value,
        input_artifacts=tuple(_artifact_response(item) for item in value.input_artifacts),
        parent_invocation_ids=value.parent_invocation_ids,
        replay_of_invocation_id=value.replay_of_invocation_id,
        configuration_json=value.configuration_json,
        trace_id=value.trace_id,
        result=_result_response(value.result) if value.result is not None else None,
        created_at=value.created_at,
    )


def _run_response(value: PipelineRunRecord) -> PipelineRunResponse:
    return PipelineRunResponse(
        run_id=value.run_id,
        case_id=value.case_id,
        definition_id=value.definition_id,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        status=value.status.value,
        visibility=value.visibility.value,
        organization_id=value.organization_id,
        input_artifacts=tuple(_artifact_response(item) for item in value.input_artifacts),
        configuration_json=value.configuration_json,
        trace_id=value.trace_id,
        created_at=value.created_at,
        updated_at=value.updated_at,
    )


def _published_value_response(value: PublishedPipelineValue) -> PublishedValueResponse:
    return PublishedValueResponse(
        field_path=value.field_path,
        producing_invocation_id=value.producing_invocation_id,
        result_path=value.result_path,
        value_json=value.value_json,
        evidence=tuple(_evidence_response(item) for item in value.evidence),
    )


def _publication_response(value: PipelinePublicationRecord) -> PipelinePublicationResponse:
    return PipelinePublicationResponse(
        publication_id=value.publication_id,
        run_id=value.run_id,
        case_id=value.case_id,
        pipeline_name=value.pipeline.name,
        pipeline_version=value.pipeline.version,
        visibility=value.visibility.value,
        organization_id=value.organization_id,
        output_schema=OutputSchemaResponse(
            name=value.output_schema.name,
            version=value.output_schema.version,
        ),
        normalized_output_json=value.normalized_output_json,
        values=tuple(_published_value_response(item) for item in value.values),
        output_artifacts=tuple(_artifact_response(item) for item in value.output_artifacts),
        trace_id=value.trace_id,
        created_at=value.created_at,
    )


def _run_detail_response(value: PipelineRunDetail) -> PipelineRunDetailResponse:
    return PipelineRunDetailResponse(
        run=_run_response(value.run),
        invocations=tuple(_invocation_response(item) for item in value.invocations),
        publications=tuple(_publication_response(item) for item in value.publications),
    )


def _raise_http(exc: EvaluationError | ArtifactStorageError | ValidationError) -> NoReturn:
    if isinstance(exc, EvaluationNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ArtifactNotFound):
        raise HTTPException(
            status_code=status.HTTP_424_FAILED_DEPENDENCY,
            detail=str(exc),
        ) from exc
    if isinstance(exc, (ConcurrencyConflict, DuplicateEvaluationRecord)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, (InvalidEvaluationState, ArtifactStorageError, ValidationError)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    raise HTTPException(status_code=500, detail="pipeline registry operation failed") from exc
