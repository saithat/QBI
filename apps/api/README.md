# API application

`apps.api.main:app` is the canonical ASGI entry point. HTTP request/response models remain distinct
from canonical domain contracts, model output, persistence rows, and queue messages. Feature routes
and their HTTP adapters are split by artifact, evaluation, queue, workbench, and structured-editor
boundaries. The spatial-editor adapter composes strict source-pixel graph contracts over the same
immutable annotation service. `hiveblot.api` is only a compatibility facade.
