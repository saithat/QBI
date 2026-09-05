import base64
import io
import json

import pytest
from PIL import Image

from hiveblot.vlm_extract import (
    OpenAICompatibleVLM,
    _extract_with_split_fallback,
    _merge_split_extractions,
    _read_cached_results,
    _should_retry_extraction,
    parse_json,
    run_vlm_extraction,
)


def test_vlm_disables_thinking_for_structured_output(tmp_path, monkeypatch) -> None:
    candidate_path = tmp_path / "candidate.png"
    Image.new("RGB", (40, 20), "white").save(candidate_path)
    captured = {}
    raw_response = '{"is_western_blot": false}'

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"choices": [{"message": {"content": raw_response}}]}

    def post(*args, **kwargs):
        captured.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("requests.post", post)

    raw, result = OpenAICompatibleVLM().extract_candidate_with_raw(
        candidate_path,
        "",
        image_max_side=10,
    )

    assert raw == raw_response
    assert result == {"is_western_blot": False}
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    data_url = captured["messages"][0]["content"][0]["image_url"]["url"]
    encoded = data_url.partition(",")[2]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.size == (10, 5)


def test_parse_json_handles_thinking_and_fences() -> None:
    parsed = parse_json('<think>ignore this</think>```json\n{"is_western_blot": false}\n```')

    assert parsed == {"is_western_blot": False}
    assert parse_json("not json")["error"] == "bad_json"


def test_failed_extractions_are_retryable() -> None:
    assert _should_retry_extraction(None)
    assert _should_retry_extraction({"error": "invalid_extraction"})
    assert _should_retry_extraction({"is_western_blot": True, "bands": []})
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


@pytest.mark.parametrize("torn_tail", [b'{"candidate_path":', b'{"candidate_path":"\xe2', b""])
def test_run_vlm_extraction_recovers_tail_and_reuses_complete_cache(
    tmp_path, monkeypatch, torn_tail
) -> None:
    candidate = {
        "paper_id": "paper",
        "page": 1,
        "candidate_path": str(tmp_path / "candidate.png"),
        "cv_score": 0.9,
    }
    pending = {**candidate, "candidate_path": str(tmp_path / "pending.png")}
    cached = {**candidate, "extraction": {"is_western_blot": False, "reason": "table"}}
    (tmp_path / "llm_candidates.json").write_text(json.dumps([candidate, pending]))
    (tmp_path / "candidate_contexts.jsonl").write_text(
        json.dumps({"candidate_path": candidate["candidate_path"], "text_context": ""}) + "\n"
    )
    cache = tmp_path / "vlm_extractions.jsonl"
    content = json.dumps(cached).encode()
    cache.write_bytes(content + b"\n" + torn_tail if torn_tail else content)
    requests = []

    def extract(_self, candidate_path, **_options):
        requests.append(str(candidate_path))
        return {"is_western_blot": False}

    monkeypatch.setattr(OpenAICompatibleVLM, "extract_candidate", extract)

    summary = run_vlm_extraction(tmp_path, resume=True)

    assert summary["cached_results"] == 1
    assert summary["queried_candidates"] == 1
    assert summary["results"] == 2
    assert requests == [pending["candidate_path"]]
    assert len([json.loads(line) for line in cache.read_bytes().splitlines()]) == 2
    resumed = run_vlm_extraction(tmp_path, resume=True)
    assert resumed["cached_results"] == 2 and resumed["queried_candidates"] == 0


@pytest.mark.parametrize("corrupt_entry", [b"not-json\n", b"{}\n"])
def test_cache_recovery_rejects_corrupt_completed_records(tmp_path, corrupt_entry) -> None:
    cache = tmp_path / "vlm_extractions.jsonl"
    content = corrupt_entry + b'{"candidate_path":"complete.png"}\n'
    cache.write_bytes(content)

    with pytest.raises(ValueError, match="line 1"):
        _read_cached_results(cache)

    assert cache.read_bytes() == content


@pytest.mark.parametrize(
    "second_result",
    [
        RuntimeError("second half failed"),
        {"error": "bad_json"},
        {"is_western_blot": True, "bands": []},
    ],
)
def test_split_fallback_cannot_publish_when_either_half_is_invalid(
    tmp_path, monkeypatch, second_result
) -> None:
    source = tmp_path / "candidate.png"
    Image.new("RGB", (1000, 1400), "white").save(source)

    def extract(_self, candidate_path, **_options):
        if candidate_path == source:
            raise RuntimeError("full image failed")
        if candidate_path.name.endswith("part_01.png"):
            return {"is_western_blot": True, "bands": [{"target": "p53"}]}
        if isinstance(second_result, Exception):
            raise second_result
        return second_result

    monkeypatch.setattr(OpenAICompatibleVLM, "extract_candidate", extract)
    with pytest.raises(RuntimeError, match="failed|invalid"):
        _extract_with_split_fallback(OpenAICompatibleVLM(), source, "", 4096)


def test_split_fallback_accepts_two_completed_negative_halves(tmp_path, monkeypatch) -> None:
    source = tmp_path / "candidate.png"
    Image.new("RGB", (1000, 1400), "white").save(source)

    def extract(_self, candidate_path, **_options):
        if candidate_path == source:
            raise RuntimeError("full image failed")
        return {"is_western_blot": False}

    monkeypatch.setattr(OpenAICompatibleVLM, "extract_candidate", extract)
    result = _extract_with_split_fallback(OpenAICompatibleVLM(), source, "", 4096)

    assert result["is_western_blot"] is False
    assert not _should_retry_extraction(result)
