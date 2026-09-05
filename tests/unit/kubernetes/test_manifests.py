from __future__ import annotations

import importlib.util
import secrets
import stat
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).parents[3]
BASE = ROOT / "infra" / "kubernetes" / "base"


def _documents(directory: Path) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name == "kustomization.yaml":
            continue
        result.extend(
            item for item in yaml.safe_load_all(path.read_text()) if isinstance(item, dict)
        )
    return result


def _load_secret_renderer() -> ModuleType:
    path = ROOT / "scripts" / "render_kubernetes_secret_env.py"
    spec = importlib.util.spec_from_file_location("render_kubernetes_secret_env", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployment_containers_cannot_gain_root_privileges() -> None:
    for document in _documents(BASE):
        if document.get("kind") != "Deployment":
            continue
        template = document["spec"]["template"]
        pod = template["spec"]
        assert pod["serviceAccountName"] != "default"
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        for container in pod["containers"]:
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["readOnlyRootFilesystem"] is True
            assert security["capabilities"]["drop"] == ["ALL"]


def test_job_rbac_cannot_read_secrets_or_change_other_workloads() -> None:
    documents = _documents(BASE)
    role = next(item for item in documents if item.get("kind") == "Role")
    resources = {resource for rule in role["rules"] for resource in rule["resources"]}
    assert resources == {"jobs", "pods", "pods/log"}


def test_secret_renderer_writes_only_runtime_secret_fields_with_private_mode(tmp_path) -> None:
    renderer = _load_secret_renderer()
    source = tmp_path / "source.env"
    output = tmp_path / "runtime.env"
    credentials = [secrets.token_urlsafe(24) for _ in range(5)]
    source.write_text(
        "POSTGRES_DB=db\n"
        "POSTGRES_USER=user\n"
        f"POSTGRES_PASSWORD={credentials[0]}\n"
        f"VLLM_API_KEY={credentials[1]}\n"
        f"S3_ACCESS_KEY_ID={credentials[2]}\n"
        f"S3_SECRET_ACCESS_KEY={credentials[3]}\n"
        f"AUTH_TOKEN_PEPPER={credentials[4]}\n"
        "UNRELATED=excluded\n",
        encoding="utf-8",
    )

    renderer.write_secret_environment(source, output)

    values = renderer.parse_environment(output.read_text(encoding="utf-8"))
    assert set(values) == {
        "DATABASE_URL",
        "AUTH_TOKEN_PEPPER",
        "VLLM_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "TEMPORAL_API_KEY",
    }
    assert "@postgres:5432/" in values["DATABASE_URL"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
