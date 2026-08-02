# API application

`apps.api.main:app` is the canonical ASGI entry point. HTTP request/response models are kept
in `schemas.py` and are distinct from model output, persistence rows, and queue messages.
The route implementation moved here in PRD-001; `hiveblot.api` is only a compatibility facade.
