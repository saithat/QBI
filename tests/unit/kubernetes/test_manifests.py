from __future__ import annotations

import importlib.util
import secrets
import stat
from pathlib import Path
from types import ModuleType

import yaml

ROOT = Path(__file__).parents[3]
BASE = ROOT / "infra" / "kubernetes" / "base"
KIND = ROOT / "infra" / "kubernetes" / "kind"
KIND_OVERLAY = ROOT / "infra" / "kubernetes" / "overlays" / "kind"
KEDA_ADDON = ROOT / "infra" / "kubernetes" / "addons" / "keda"


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


def test_base_defines_required_stateless_workloads_and_no_durable_backends() -> None:
    documents = _documents(BASE)
    deployments = {
        item["metadata"]["name"]: item for item in documents if item.get("kind") == "Deployment"
    }
    assert set(deployments) == {
        "hiveblot-api",
        "hiveblot-web",
        "hiveblot-job-scheduler",
        "hiveblot-evaluation-workers",
        "hiveblot-workflow-workers",
        "hiveblot-cpu-workers",
        "hiveblot-fetch-workers",
        "hiveblot-otel-collector",
    }
    assert not {"StatefulSet", "Secret"} & {item.get("kind") for item in documents}
    serialized = yaml.safe_dump_all(documents).casefold()
    assert "kind: statefulset" not in serialized
    assert "name: postgres" not in serialized
    assert "name: minio" not in serialized


def test_every_long_lived_container_has_resources_security_and_health_probes() -> None:
    for document in _documents(BASE):
        if document.get("kind") != "Deployment":
            continue
        template = document["spec"]["template"]
        pod = template["spec"]
        assert pod["serviceAccountName"] != "default"
        assert pod["enableServiceLinks"] is False
        assert pod["securityContext"]["runAsNonRoot"] is True
        assert pod["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
        for container in pod["containers"]:
            assert set(container["resources"]) == {"requests", "limits"}
            assert container["resources"]["requests"]
            assert container["resources"]["limits"]
            assert container["startupProbe"]
            assert container["readinessProbe"]
            assert container["livenessProbe"]
            security = container["securityContext"]
            assert security["allowPrivilegeEscalation"] is False
            assert security["readOnlyRootFilesystem"] is True
            assert security["capabilities"]["drop"] == ["ALL"]
            assert not str(container["image"]).endswith(":latest")


def test_service_accounts_are_separate_and_job_rbac_is_least_privilege() -> None:
    documents = _documents(BASE)
    accounts = {
        item["metadata"]["name"] for item in documents if item.get("kind") == "ServiceAccount"
    }
    assert len(accounts) == 10
    assert "hiveblot-job-runner" in accounts
    assert "hiveblot-discovery" in accounts
    assert "hiveblot-fetch-worker" in accounts
    role = next(item for item in documents if item.get("kind") == "Role")
    resources = {resource for rule in role["rules"] for resource in rule["resources"]}
    assert resources == {"jobs", "pods", "pods/log"}
    assert "secrets" not in resources


def test_workspace_is_explicitly_non_authoritative_attempt_scratch() -> None:
    documents = _documents(BASE)
    claim = next(item for item in documents if item.get("kind") == "PersistentVolumeClaim")
    assert claim["metadata"]["annotations"]["hiveblot.io/data-class"] == (
        "ephemeral-attempt-scratch"
    )
    assert claim["spec"]["accessModes"] == ["ReadWriteMany"]


def test_kustomization_references_existing_resources() -> None:
    kustomization = yaml.safe_load((BASE / "kustomization.yaml").read_text())
    for resource in kustomization["resources"]:
        assert (BASE / resource).is_file()


def test_secret_renderer_writes_only_runtime_secret_fields_with_private_mode(tmp_path) -> None:
    renderer = _load_secret_renderer()
    source = tmp_path / "source.env"
    output = tmp_path / "runtime.env"
    credentials = [secrets.token_urlsafe(24) for _ in range(4)]
    source.write_text(
        "POSTGRES_DB=db\n"
        "POSTGRES_USER=user\n"
        f"POSTGRES_PASSWORD={credentials[0]}\n"
        f"VLLM_API_KEY={credentials[1]}\n"
        f"S3_ACCESS_KEY_ID={credentials[2]}\n"
        f"S3_SECRET_ACCESS_KEY={credentials[3]}\n"
        "UNRELATED=excluded\n",
        encoding="utf-8",
    )

    renderer.write_secret_environment(source, output)

    values = renderer.parse_environment(output.read_text(encoding="utf-8"))
    assert set(values) == {
        "DATABASE_URL",
        "VLLM_API_KEY",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "TEMPORAL_API_KEY",
    }
    assert "@postgres:5432/" in values["DATABASE_URL"]
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_kind_overlay_is_bounded_and_avoids_host_dependency_ports() -> None:
    kustomization = yaml.safe_load((KIND_OVERLAY / "kustomization.yaml").read_text())
    for patch in kustomization["patches"]:
        assert (KIND_OVERLAY / patch["path"]).is_file()

    compose_override = (KIND / "compose.dependencies.yaml").read_text()
    assert compose_override.count("ports: !reset []") == 2
    cluster = yaml.safe_load((KIND / "cluster.yaml").read_text())
    mapping = cluster["nodes"][0]["extraPortMappings"][0]
    assert mapping == {
        "containerPort": 30080,
        "hostPort": 18080,
        "listenAddress": "127.0.0.1",
        "protocol": "TCP",
    }

    ingress = yaml.safe_load((KIND_OVERLAY / "network-policy-patch.yaml").read_text())
    assert ingress["spec"]["ingress"][0]["from"][0]["ipBlock"]["cidr"] == "0.0.0.0/0"
    for script in (KIND / "up.sh", KIND / "down.sh"):
        assert stat.S_IMODE(script.stat().st_mode) & stat.S_IXUSR


def test_kind_smoke_job_is_restricted_and_finite() -> None:
    smoke = yaml.safe_load((KIND / "smoke-job.yaml").read_text())
    assert smoke["kind"] == "Job"
    assert smoke["spec"]["backoffLimit"] == 0
    assert smoke["spec"]["activeDeadlineSeconds"] == 240
    pod = smoke["spec"]["template"]["spec"]
    assert pod["automountServiceAccountToken"] is False
    assert pod["restartPolicy"] == "Never"
    container = pod["containers"][0]
    assert container["resources"]["requests"]
    assert container["resources"]["limits"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_discovery_cronjob_is_bounded_scheduled_and_tokenless() -> None:
    documents = _documents(BASE)
    cronjob = next(item for item in documents if item.get("kind") == "CronJob")
    assert cronjob["metadata"]["name"] == "hiveblot-pmc-discovery"
    spec = cronjob["spec"]
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["startingDeadlineSeconds"] <= 1800
    job = spec["jobTemplate"]["spec"]
    assert job["backoffLimit"] == 2
    assert job["activeDeadlineSeconds"] == 1800
    pod = job["template"]["spec"]
    assert pod["serviceAccountName"] == "hiveblot-discovery"
    assert pod["automountServiceAccountToken"] is False
    assert pod["restartPolicy"] == "Never"
    container = pod["containers"][0]
    assert container["command"] == ["hiveblot-discover-pmc"]
    assert container["resources"]["requests"]
    assert container["resources"]["limits"]
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert "envFrom" not in container
    environment_names = {item["name"] for item in container["env"]}
    assert environment_names == {
        "HIVEBLOT_ENV",
        "DATABASE_URL",
        "S3_ENDPOINT_URL",
        "S3_REGION",
        "S3_BUCKET",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "ARTIFACT_MAX_BYTES",
        "PMC_OAI_BASE_URL",
        "DISCOVERY_USER_AGENT",
        "DISCOVERY_HTTP_TIMEOUT_SECONDS",
        "DISCOVERY_MAX_RESPONSE_BYTES",
        "DISCOVERY_LOOKBACK_DAYS",
        "DISCOVERY_MAXIMUM_PAGES",
        "OTEL_SERVICE_NAME",
    }

    kind_patch = yaml.safe_load((KIND_OVERLAY / "discovery-patch.yaml").read_text())
    assert kind_patch["spec"]["suspend"] is True


def test_fetch_workers_are_long_lived_tokenless_and_least_privilege() -> None:
    deployment = next(
        item
        for item in _documents(BASE)
        if item.get("kind") == "Deployment" and item["metadata"]["name"] == "hiveblot-fetch-workers"
    )
    pod = deployment["spec"]["template"]["spec"]
    assert pod["serviceAccountName"] == "hiveblot-fetch-worker"
    assert pod["automountServiceAccountToken"] is False
    container = pod["containers"][0]
    assert container["command"] == ["hiveblot-fetch-worker"]
    assert "envFrom" not in container
    environment_names = {item["name"] for item in container["env"]}
    assert "VLLM_API_KEY" not in environment_names
    assert "TEMPORAL_API_KEY" not in environment_names
    assert {
        "DATABASE_URL",
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "FETCH_WORKER_ID",
        "FETCH_DOMAIN_MINIMUM_INTERVAL_MILLISECONDS",
        "FETCH_DOMAIN_MAXIMUM_CONCURRENCY",
    } <= environment_names


def test_keda_addon_scales_fetch_deployment_from_durable_queue_depth() -> None:
    documents = tuple(
        yaml.safe_load_all((KEDA_ADDON / "fetch-worker-scaled-object.yaml").read_text())
    )
    authentication = next(item for item in documents if item["kind"] == "TriggerAuthentication")
    scaler = next(item for item in documents if item["kind"] == "ScaledObject")
    secret_reference = authentication["spec"]["secretTargetRef"][0]
    assert secret_reference == {
        "parameter": "connection",
        "name": "hiveblot-keda-secrets",
        "key": "DATABASE_URL",
    }
    assert scaler["apiVersion"] == "keda.sh/v1alpha1"
    assert scaler["kind"] == "ScaledObject"
    spec = scaler["spec"]
    assert spec["scaleTargetRef"]["name"] == "hiveblot-fetch-workers"
    assert spec["minReplicaCount"] == 1
    assert spec["maxReplicaCount"] > spec["minReplicaCount"]
    trigger = spec["triggers"][0]
    assert trigger["type"] == "postgresql"
    assert trigger["authenticationRef"]["name"] == "hiveblot-fetch-queue"
    assert "crawl_fetch_tasks" in trigger["metadata"]["query"]
