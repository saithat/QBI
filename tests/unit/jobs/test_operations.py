import json
from pathlib import Path

from workers.jobs.operations import normalize_legacy_western_blot

FIXTURE_DIR = Path(__file__).parents[2] / "fixtures" / "baseline"
SOURCE_PDF = "/data/input/oduah-2024-p53-bortezomib.pdf"


def test_legacy_normalization_runs_as_a_bounded_job_operation(tmp_path: Path) -> None:
    output = tmp_path / "records"

    count = normalize_legacy_western_blot(
        FIXTURE_DIR / "oduah_2024_page_4_model_output.json",
        output,
        source_pdf=SOURCE_PDF,
    )

    expected = json.loads(
        (FIXTURE_DIR / "oduah_2024_page_4_normalized_records.json").read_text(encoding="utf-8")
    )
    assert count == 40
    assert json.loads(output.read_text(encoding="utf-8")) == expected
