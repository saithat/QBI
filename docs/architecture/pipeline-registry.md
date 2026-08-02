# Pipeline registry, provenance, and replay

```text
immutable source artifacts + evaluation case
                    |
                    v
        versioned pipeline definition
        model/tool DAG + output schemas
                    |
                    v
             versioned run
                    |
       parent-linked component invocations
        /             |              \
  raw output   normalized output   evidence/artifacts
        \             |              /
         strict Pydantic validation
                    |
          success or recorded failure
                    |
       replay -> new invocation UUID
                    |
         explicit result publication
      field -> producer invocation + pointer
```

`PipelineRegistryService` owns graph registration, run materialization, schema validation, replay,
and publication invariants. It depends on the evaluation service only to resolve a case and its
source artifacts, and on the artifact service only to canonicalize immutable artifact metadata.
It does not know how a container, worker, or Kubernetes Job executes a component.

The default `OutputSchemaRegistry` is derived from the committed canonical contract registry. A
definition cannot name an unknown output schema. When a caller reports success, the attempted
normalized JSON is validated in strict mode. Invalid output is persisted as an
`output_validation` failure rather than discarded or admitted to canonical scientific state.

## Persistence model

PostgreSQL stores complete canonical JSONB snapshots and indexed relational columns. Association
tables enforce ordered, unique input/output artifacts. Composite foreign keys constrain parent and
replay links to one run and constrain a publication's case to its run. Conditional terminal updates
provide an optimistic write-once boundary for invocation results.

The service reconstructs history from definitions, runs, invocations, and publications. A replay
keeps its original component identity and parent references while receiving new configuration,
trace, and invocation identifiers. A publication never changes invocation documents; it records a
new immutable selection of their validated results.

## Workbench boundary

The source workbench fetches pipeline history separately from source evidence. A run-history
failure does not prevent reviewers from inspecting artifacts. The **Runs** tab shows producer and
version identity, status, trace, latency, cost, warnings, replay origin, and collapsible raw and
normalized output. Mutation endpoints remain API-only; the workbench is an inspection surface.
