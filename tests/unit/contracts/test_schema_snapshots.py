from pathlib import Path

from hiveblot_contracts.registry import rendered_schema_snapshots

SCHEMA_DIR = Path("packages/contracts/schemas/v1")


def test_committed_json_schema_snapshots_are_current() -> None:
    expected = rendered_schema_snapshots()
    actual = {
        path.name: path.read_text(encoding="utf-8") for path in SCHEMA_DIR.glob("*.schema.json")
    }

    assert actual == expected
