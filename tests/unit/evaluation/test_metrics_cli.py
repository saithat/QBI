from hiveblot_contracts import EvaluationMetricRunRecord
from hiveblot_evaluation import evaluation_scoring_input_sha256
from hiveblot_evaluation.metrics_cli import main

from tests.fakes.metrics import scoring_input


def test_cli_generates_identical_valid_results_for_identical_inputs(tmp_path) -> None:
    input_path = tmp_path / "input.json"
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    input_path.write_text(scoring_input(candidate=True).model_dump_json(), encoding="utf-8")

    assert main((str(input_path), "--output", str(first_path))) == 0
    assert main((str(input_path), "--output", str(second_path))) == 0

    first = first_path.read_text(encoding="utf-8")
    assert first == second_path.read_text(encoding="utf-8")
    record = EvaluationMetricRunRecord.model_validate_json(first)
    assert record.pipeline.version == "2.0"
    assert record.input_sha256 == evaluation_scoring_input_sha256(scoring_input(candidate=True))
