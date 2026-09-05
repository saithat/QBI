from pathlib import Path

import pytest
from hiveblot_contracts import schema_cli


@pytest.mark.parametrize("installed", [False, True], ids=["source-checkout", "installed-wheel"])
def test_check_uses_snapshots_for_the_package_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: bool
) -> None:
    if installed:
        package_directory = tmp_path / "site-packages" / "hiveblot_contracts"
        snapshots = package_directory / "schemas" / "v1"
    else:
        contract_directory = tmp_path / "packages" / "contracts"
        package_directory = contract_directory / "src" / "hiveblot_contracts"
        snapshots = contract_directory / "schemas" / "v1"
    package_directory.mkdir(parents=True)
    monkeypatch.setattr(schema_cli, "__file__", str(package_directory / "schema_cli.py"))
    monkeypatch.setattr("sys.argv", ["hiveblot-schemas", "--check"])
    monkeypatch.chdir(tmp_path)
    schema_cli.write_snapshots(snapshots)

    schema_cli.main()

    snapshot = next(snapshots.glob("*.schema.json"))
    snapshot.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        schema_cli.main()
    assert error.value.code == 1


def test_explicit_output_directory_overrides_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshots = tmp_path / "custom-snapshots"
    monkeypatch.setattr("sys.argv", ["hiveblot-schemas", "--output-dir", str(snapshots)])

    schema_cli.main()

    assert schema_cli.check_snapshots(snapshots) == []
