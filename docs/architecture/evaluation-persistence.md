# Evaluation and annotation persistence

## Record separation

```text
evaluation case
  |-- immutable prediction document (raw output + normalized output)
  |-- reviewer A annotation document -> revision 1 -> revision 2
  |-- reviewer B annotation document -> revision 1
  `-- adjudication -> selected revision + all considered revisions
```

Raw model text remains an exact string even when malformed. A normalized prediction, when present,
must be valid JSON and is stored separately. Producer, pipeline, configuration, evidence,
validation warnings, confidence, trace, latency, and cost metadata travel with each prediction.

## Revision transaction

1. Lock the annotation document row.
2. Compare its head UUID with `expected_head_revision_id`.
3. Insert one revision and its complete field, spatial, relationship, and error-code snapshot.
4. Advance the document head and revision count in the same transaction.

The database additionally checks revision sequence/prior relationships, geometry bounds, target
shape, observation states, scalar value rules, error-code references, case/artifact foreign keys,
and reviewer uniqueness per case.

## Stable targets

- `field_path` targets use JSON Pointer syntax, such as `/targets/0/name`.
- `entity` targets use a stable entity UUID and optional field name.
- Spatial regions use source artifact IDs, source-pixel coordinates, canvas dimensions, and an
  optional PDF page number.
- Relationships retain stable subject/object IDs and a constrained relation name.

The PRD-006/007 editors will operate on these contracts; they do not need a new persistence model.
