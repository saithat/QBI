import pytest

from hiveblot import db
from hiveblot.domain import RecordSearchCriteria


def test_record_query_parameters_combine_text_filters_and_exact_source():
    malicious = "p53%' OR true --"
    query, params = db.build_record_query(
        RecordSearchCriteria(
            q="TP53 Nutlin",
            target=malicious,
            sample="A549 cells",
            condition="treated with Nutlin",
            paper_id="doi:10.1000/example",
        ),
        limit=500,
        offset=-4,
    )
    assert malicious not in query
    assert f"%{malicious}%" in params
    assert "paper_id = %s" in query and "doi:10.1000/example" in params
    assert "%TP53%" in params and "%Nutlin%" in params
    assert "%cells%" not in params and "%treated%" not in params
    assert params[-2:] == [200, 0]
    assert "ORDER BY updated_at DESC, id DESC" in query


def test_replacement_rejects_cross_source_rows_before_touching_database(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        pytest.fail("Invalid replacement must not connect or delete rows")

    monkeypatch.setattr(db.psycopg, "connect", unexpected_connection)
    with pytest.raises(ValueError, match="specified paper"):
        db.replace_records_for_source("test-database", "paper-a", [{"paper_id": "paper-b"}])
    with pytest.raises(ValueError, match="specified paper"):
        db.replace_records_for_source("test-database", "", [])
    with pytest.raises(ValueError, match="specified source"):
        db.replace_records_for_source(
            "test-database",
            "paper-a",
            [{"paper_id": "paper-a", "source_id": "b.png"}],
            source_id="a.png",
        )
