from hiveblot.db import build_record_query
from hiveblot.model_client import SearchFilters


def test_build_record_query_uses_parameters() -> None:
    malicious = "p53%' OR true --"
    query, params = build_record_query(
        SearchFilters(target=malicious, sample="A549", condition="Nutlin"),
        limit=500,
        offset=-4,
    )

    assert malicious not in query
    assert params == [
        f"%{malicious}%",
        "%A549%",
        "%A549%",
        "%Nutlin%",
        "%Nutlin%",
        200,
        0,
    ]
    assert "target ILIKE %s" in query


def test_build_record_query_falls_back_to_broad_text() -> None:
    query, params = build_record_query(SearchFilters(), broad_query="  apoptosis  ")

    assert query.count("ILIKE %s") == 5
    assert params[:-2] == ["%apoptosis%"] * 5
    assert params[-2:] == [100, 0]
