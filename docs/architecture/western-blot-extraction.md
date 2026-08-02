# Versioned western-blot extraction

```text
published PDF/image artifact
        |
        v
verified bytes (size + SHA-256)
        |
        +------------------------ stable evaluation case
        |
        v
figure_detection
  retained PDF/OpenCV or image full-frame adapter
  raw detector record + strict candidate set + evidence
        |
        v
western_blot_vision
  retained prompt + configured local model revision
  exact response text -> permissive adapter -> strict prediction set
        |
        v
case_assembly
  deterministic structured entities + spatial graph + warnings
        |
        +------------------------ immutable prediction document
        |
        +------------------------ producer-linked publication
        |
        v
existing workbench, structured editor, and spatial editor
```

`WesternBlotExtractionService` composes immutable artifact access, evaluation persistence, and the
pipeline registry. It does not own storage publication, model serving, reviewer mutations, or job
leases. HTTP request/summary models remain in `apps/api`; canonical stage models live in
`hiveblot_contracts`; implementation and orchestration code live in `hiveblot_extraction`; ORM
records remain behind their repositories.

## Stage contracts and representations

The public contract registry includes:

- `western-blot-extraction-input`
- `western-blot-figure-candidate-set`
- `western-blot-candidate-prediction-set`
- `western-blot-component-replay-record`
- `western-blot-extraction-result`
- `western-blot-extraction-run-record`

All are strict, frozen, versioned Pydantic v2 models with committed JSON Schema. Candidate and
entity UUIDs are deterministic for one source hash, detector version, and geometry. Candidate
predictions require a complete row-by-lane band-state grid. Final results validate that structured
evidence and field paths reference regions from the same exact source artifact.

The model response crosses two explicit boundaries. The invocation and evaluation prediction keep
the exact returned response string inside a raw envelope. `normalize_candidate_predictions` then
accepts common legacy quirks at the external boundary and builds a strict candidate-prediction
document. A malformed response remains in failed invocation history; it never enters a normalized
scientific record.

## Provenance and version identity

The retained implementation identity records:

- implementation name and semantic version;
- OpenCV detector name and version;
- model provider, model name, and required deployed revision;
- SHA-256 of the complete prompt text;
- deterministic assembler name and version; and
- a pipeline version fingerprint derived from those identities.

Each component records configuration, trace and parent invocation IDs, latency, cost metadata,
evidence, warnings, raw output, normalized output, and validation outcome through the PRD-008
registry. The retained local model API does not report monetary cost, so the vision invocation
records zero as the numeric compatibility value plus a `model_cost_unavailable` warning.

## Review and replay behavior

The case key is stable per source artifact. A full rerun creates a new run, three new invocations,
a new prediction, and a new publication on that same case. Existing reviewer documents and every
annotation revision remain untouched. The structured and spatial services understand the nested
v1 extraction result while continuing to accept their original standalone prediction schemas.

Selective replay is non-cascading. Detection and model replays record only their chosen stage.
Assembly replay consumes its original successful model parent and appends a new reviewable
prediction and publication. Every replay gets a new invocation and trace identifier and names the
invocation it replays.

## Geometry fidelity

PDF candidate boxes use pixels from a page rendered at the recorded run DPI and retain the page
number and canvas dimensions while referencing the immutable PDF artifact. Stored images use their
native pixel dimensions. Because the retained model returns lane/row ordering but not exact
coordinates, the assembler partitions candidate geometry into estimated panel, blot, lane, row,
and band regions. The warning is mandatory and reviewers can correct the graph through the
append-only spatial editor. PRD-012 must use explicit reviewed geometry and must not mistake these
estimates for numerical measurements.
