# Structured annotation architecture

```text
normalized prediction JSON
          |
          v
WesternBlotStructuredAnnotation -- semantic comparison -- reviewer draft
          |                                                  |
 canonical lookup                               individual / bulk accept
          |                                                  |
          `---------------- typed editor --------------------'
                                      |
                            expected head revision UUID
                                      |
                         EvaluationService.append_revision
                                      |
        generic field/spatial snapshot + structured JSONB snapshot
                                      |
                         immutable annotation history
```

The contract uses stable entity UUIDs and an enumerated relationship graph. Cross-entity validators
ensure, for example, that a loading-control relationship connects a target protein to a protein
marked as a loading control, and that lane relationships point to compatible condition entities.

The HTTP layer adapts JSON UUIDs and arrays before entering the strict contract. The persistence
mapper performs the inverse boundary operation: JSONB is serialized back through Pydantic JSON-mode
validation before domain code receives a frozen tuple-based model.

Semantic diffs key collections by entity or relationship UUID instead of array position. Reordering
therefore does not masquerade as a scientific change. Added, removed, modified, and unchanged field
paths remain separately countable and inspectable.
