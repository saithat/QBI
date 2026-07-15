import json

from hiveblot.vlm_extract import (
    _merge_split_extractions,
    _should_retry_extraction,
    parse_json,
    run_vlm_extraction,
)


def test_parse_json_handles_thinking_and_fences() -> None:
    parsed = parse_json('<think>ignore this</think>```json\n{"is_western_blot": false}\n```')

    assert parsed == {"is_western_blot": False}
    assert parse_json("not json")["error"] == "bad_json"


def test_failed_extractions_are_retryable() -> None:
    assert _should_retry_extraction(None)
    assert _should_retry_extraction({"error": "invalid_extraction"})
    assert not _should_retry_extraction({"is_western_blot": False})


def test_merge_split_extractions_collects_panels() -> None:
    merged = _merge_split_extractions(
        [
            {
                "is_western_blot": True,
                "figure_label": "Figure 1",
                "bands": [{"target": "p53"}],
                "warnings": [],
            },
            {"is_western_blot": False, "reason": "not a blot"},
        ]
    )

    assert merged is not None
    assert merged["figure_label"] == "Figure 1"
    assert merged["panels"] == [{"bands": [{"target": "p53"}], "warnings": []}]


def test_run_vlm_extraction_reuses_successful_cache(tmp_path) -> None:
    candidate = {
        "paper_id": "paper",
        "page": 1,
        "candidate_path": str(tmp_path / "candidate.png"),
        "cv_score": 0.9,
    }
    cached = {**candidate, "extraction": {"is_western_blot": False, "reason": "table"}}
    (tmp_path / "llm_candidates.json").write_text(json.dumps([candidate]))
    (tmp_path / "candidate_contexts.jsonl").write_text(
        json.dumps({"candidate_path": candidate["candidate_path"], "text_context": ""}) + "\n"
    )
    (tmp_path / "vlm_extractions.jsonl").write_text(json.dumps(cached) + "\n")

    summary = run_vlm_extraction(tmp_path, resume=True)

    assert summary["cached_results"] == 1
    assert summary["queried_candidates"] == 0
    assert summary["results"] == 1
