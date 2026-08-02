# Platform foundation boundaries

## Dependency direction

```text
apps/api ---------------------> hiveblot domain/persistence/model adapters
    |                                      |
    +----------------> hiveblot_contracts <-+---- workers/extraction

hiveblot_contracts --------X apps, workers, database, model clients
```

`hiveblot_contracts` is the only canonical cross-process package in PRD-001. It owns immutable,
strict, versioned contracts and their JSON Schemas. HTTP-only schemas live under `apps/api`.
Model output is normalized to its own versioned Pydantic type. Persistence rows are TypedDicts,
and internal search criteria are frozen dataclasses. Mapping is explicit at each boundary.

## External-data boundary

```text
external/model JSON -> source parser -> strict versioned prediction -> domain criteria
                                                     |                 |
                                                     |                 v
                                                     |          persistence query
                                                     v
                                             HTTP response mapping
```

The legacy scientific extraction payload remains dictionary-based until PRD-011 so this PR does
not silently redefine its scientific meaning. Its deterministic normalizer is protected by a
captured real-output fixture.

## Stable contract policy

Canonical contracts:

- inherit `ContractModel`;
- use `strict=True`, `extra="forbid"`, `frozen=True`, and validated defaults;
- carry `schema_version`;
- use tuples for immutable collections;
- use discriminated unions for heterogeneous producer/job-result records;
- serialize through Pydantic rather than handwritten dictionaries;
- have deterministic JSON Schema snapshots under `packages/contracts/schemas/v1/`.

Changing a committed public schema requires an intentional model change, `make schemas`, tests,
and a migration note. A breaking contract should receive a new schema version/path instead of
silently changing v1.

## Transitional boundaries

`hiveblot.api` remains an import facade. Extraction modules remain under `hiveblot/`, reached
through the canonical worker wrapper. Root Docker/Compose files remain compatibility entry
points. These choices preserve useful history and keep PRD-001 independently mergeable without
starting PRD-002, PRD-008, PRD-011, PRD-013, or PRD-014.
