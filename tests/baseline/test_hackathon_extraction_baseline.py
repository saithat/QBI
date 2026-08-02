import json
from pathlib import Path

from hiveblot.records import flatten_records

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "baseline"
SOURCE_PDF = "/data/input/oduah-2024-p53-bortezomib.pdf"


def test_historic_vlm_output_normalizes_to_the_preserved_records() -> None:
    model_output = json.loads(
        (FIXTURE_DIR / "oduah_2024_page_4_model_output.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (FIXTURE_DIR / "oduah_2024_page_4_normalized_records.json").read_text(encoding="utf-8")
    )

    actual = flatten_records([model_output], source_pdf=SOURCE_PDF)

    assert actual == expected
    assert len(actual) == 40
    assert {record["panel_label"] for record in actual} == {"A", "B", "E"}
    assert {record["target"] for record in actual} == {"p53", "GAPDH", "Vinculin"}
