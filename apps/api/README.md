# API application

`apps.api.main:app` is the canonical ASGI entry point. HTTP request/response models remain distinct
from canonical domain contracts, model output, persistence rows, and queue messages. Feature routes
and their HTTP adapters are split by artifact, evaluation, queue, workbench, and structured-editor
boundaries. The spatial-editor adapter composes strict source-pixel graph contracts over the same
immutable annotation service. The pipeline-registry adapter maps explicit HTTP schemas to immutable
definition, invocation, replay, and publication contracts; it does not execute containers.
`hiveblot.api` is only a compatibility facade.

The densitometry adapter exposes complete geometry options, immutable attempt history, execution,
and exact replay. It maps HTTP-friendly UUID/enumeration values into strict canonical contracts and
returns expiring object-store URLs for source and overlay bytes.

The public-discovery adapter accepts strict, versioned source batches, exposes filtered frontier
inspection, and maps optimistic schedule/retry and immutable-artifact acquisition mutations. Source
protocol parsing remains in `services/crawler`, not in FastAPI request handlers. Fetch task,
append-only attempt, and aggregate crawl-metric routes expose the distributed worker state without
proxying acquired bytes through FastAPI.
