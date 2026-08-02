# Applications

Deployable user-facing process entry points live here. Applications may depend on shared
packages and domain adapters; shared packages must never import application entry points.

`apps/web` contains browser-delivered static surfaces. The PRD-004 review queue intentionally uses
plain HTML, CSS, and JavaScript so it adds no second build toolchain. PRD-005 adds the workbench in
the same application boundary while source aggregation remains a strict backend service. PRD-006
adds the typed western-blot editor at `/annotate/{case_id}`; its HTTP inputs are distinct adapters
around the canonical annotation contracts. PRD-007 adds `/spatial/{case_id}` for validated
source-pixel graph editing while continuing to stream source bytes directly from object storage.
