from tests.unit.contracts.test_contracts import job_specification
from workers.extraction.messages import decode_job_specification


def test_worker_decodes_the_shared_job_contract() -> None:
    specification = job_specification()

    assert decode_job_specification(specification.model_dump_json()) == specification
