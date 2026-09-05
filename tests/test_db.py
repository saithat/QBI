from hiveblot.db import build_record_query
from hiveblot.domain import RecordSearchCriteria


def test_build_record_query_uses_parameters() -> None:
    malicious = "p53%' OR true --"
    query, params = build_record_query(
        RecordSearchCriteria(target=malicious, sample="A549", condition="Nutlin"),
        limit=500,
        offset=-4,
    )

    assert malicious not in query
    assert f"%{malicious}%" in params
    assert params[-2:] == [200, 0]
