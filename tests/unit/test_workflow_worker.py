from __future__ import annotations

import pytest

from workers.workflow.entrypoint import RuntimeProbeWorkflow


@pytest.mark.asyncio
async def test_runtime_probe_workflow_is_deterministic() -> None:
    assert await RuntimeProbeWorkflow().run() == "ok"
