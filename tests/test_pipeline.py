import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PIL import Image

from hiveblot import pipeline
from hiveblot.settings import Settings


def _positive(target="p53"):
    return {
        "is_western_blot": True,
        "bands": [
            {
                "target": target,
                "row_index": 1,
                "lane_index": 1,
                "band_state": "present",
                "confidence": "high",
            }
        ],
    }


@pytest.fixture
def ingestion(tmp_path, monkeypatch):
    state = SimpleNamespace(answer=_positive(), requests=[], published={}, replacements=[])
    settings = Settings(
        database_url="postgresql://unused",
        data_dir=tmp_path / "data",
        vllm_model="test-model",
        vllm_model_revision="r1",
        pdf_dpi=72,
        _env_file=None,
    )

    def extract(_self, candidate_path, text_context, **_options):
        state.requests.append((candidate_path, text_context))
        if isinstance(state.answer, Exception):
            raise state.answer
        if callable(state.answer):
            return state.answer(candidate_path)
        return copy.deepcopy(state.answer)

    def replace(_database_url, paper_id, records, *, source_id):
        assert all(row["paper_id"] == paper_id and row["source_id"] == source_id for row in records)
        state.replacements.append((paper_id, source_id))
        state.published[paper_id, source_id] = copy.deepcopy(records)
        return len(records)

    monkeypatch.setattr(pipeline.vlm_extract.OpenAICompatibleVLM, "extract_candidate", extract)
    monkeypatch.setattr(pipeline.db, "initialize", lambda _: None)
    monkeypatch.setattr(pipeline.db, "replace_records_for_source", replace, raising=False)
    return settings, state


@pytest.mark.parametrize("extension", ["png", "jpg"])
def test_image_ingestion_publishes_complete_portable_source(ingestion, tmp_path, extension):
    settings, state = ingestion
    source = tmp_path / f"blot.{extension}"
    Image.new("RGB", (80, 40), "gray").save(source)

    summary = pipeline.run_pipeline(
        source,
        settings=settings,
        paper_id="10.1234/paper",
        source_id="figure-2",
        source_url="https://doi.org/10.1234/paper",
        context="Figure 2: p53 after treatment.",
    )

    assert state.replacements == [("10.1234/paper", "figure-2")]
    record = state.published["10.1234/paper", "figure-2"][0]
    assert record["target"] == "p53"
    assert record["model_version"] == "test-model@r1"
    assert record["source_url"] == "https://doi.org/10.1234/paper"
    assert not Path(record["candidate_path"]).is_absolute()
    assert record["candidate_path"].startswith("runs/")
    candidate = settings.data_dir / record["candidate_path"]
    assert record["image_sha256"] == hashlib.sha256(candidate.read_bytes()).hexdigest()
    assert (settings.data_dir / record["source_pdf"]).read_bytes() == source.read_bytes()
    assert state.requests[0][1] == "Figure 2: p53 after treatment."
    assert summary["database_rows"] == summary["queried_candidates"] == 1


def test_same_paper_images_coexist_and_only_successful_reimport_replaces_one(ingestion, tmp_path):
    settings, state = ingestion
    first, second = tmp_path / "figure-1.png", tmp_path / "figure-2.png"
    for source in (first, second):
        Image.new("RGB", (80, 40), "gray").save(source)
        pipeline.run_pipeline(source, settings=settings, paper_id="paper")
    before = copy.deepcopy(state.published)
    published_dir = (
        settings.data_dir / before["paper", first.name][0]["candidate_path"]
    ).parent.parent
    published_files = {
        path: path.read_bytes() for path in published_dir.rglob("*") if path.is_file()
    }
    assert len(before) == 2
    assert (
        before["paper", first.name][0]["candidate_key"]
        != before["paper", second.name][0]["candidate_key"]
    )

    state.answer = RuntimeError("model unavailable")
    with pytest.raises(RuntimeError, match="existing catalog records were kept"):
        pipeline.run_pipeline(first, settings=settings, paper_id="paper", use_cache=False)
    assert state.published == before
    assert {path: path.read_bytes() for path in published_files} == published_files
    assert len(state.replacements) == 2

    state.answer = _positive("AKT")
    pipeline.run_pipeline(first, settings=settings, paper_id="paper")
    assert state.published["paper", first.name][0]["target"] == "AKT"
    assert state.published["paper", second.name] == before["paper", second.name]
    assert len(state.replacements) == 3
    assert {path: path.read_bytes() for path in published_files} == published_files


def test_resume_reuses_only_matching_content_model_and_options(ingestion, tmp_path):
    settings, state = ingestion
    source = tmp_path / "blot.png"
    Image.new("RGB", (80, 40), "gray").save(source)
    first = pipeline.run_pipeline(source, settings=settings)
    resumed = pipeline.run_pipeline(source, settings=settings)
    assert resumed["run_dir"] == first["run_dir"]
    assert resumed["cached_results"] == 1 and resumed["queried_candidates"] == 0
    assert len(state.requests) == 1

    Image.new("RGB", (80, 40), "white").save(source)
    changed_image = pipeline.run_pipeline(source, settings=settings)
    changed_model = pipeline.run_pipeline(
        source,
        settings=settings.model_copy(update={"vllm_model_revision": "r2"}),
    )
    changed_options = pipeline.run_pipeline(
        source,
        settings=settings.model_copy(update={"vllm_max_tokens": 2048}),
    )
    assert (
        len({item["run_dir"] for item in (first, changed_image, changed_model, changed_options)})
        == 4
    )
    assert len(state.requests) == 4


def test_pdf_pipeline_is_atomic_resumable_and_tracks_parent_bytes(ingestion, tmp_path, monkeypatch):
    settings, state = ingestion
    source = tmp_path / "paper.pdf"

    def write_pdf(title):
        with fitz.open() as document:
            page = document.new_page(width=200, height=200)
            page.insert_text((20, 20), "Figure 1. A549 cells.")
            document.set_metadata({"title": title})
            document.save(source)

    def candidate_from_rendered_page(pages, out_dir, paper_id, **_options):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        candidates = []
        for index in range(2):
            candidate_path = out_dir / f"page_001_cand_{index:04}.png"
            with Image.open(pages[0]["image_path"]) as image:
                image.save(candidate_path)
            candidates.append(
                {
                    "paper_id": paper_id,
                    "page": 1,
                    "cv_score": 1.0,
                    "bbox_page": [0, 0, 200, 200],
                    "candidate_path": str(candidate_path),
                }
            )
        return candidates

    def fail_second_candidate(path):
        if path.name.endswith("0001.png"):
            raise RuntimeError("second model request failed")
        return _positive()

    monkeypatch.setattr(
        pipeline.pdf_preprocess, "generate_candidates", candidate_from_rendered_page
    )
    write_pdf("First source version")
    state.published["paper", source.name] = [{"target": "previous result"}]
    before = copy.deepcopy(state.published)
    state.answer = fail_second_candidate
    with pytest.raises(RuntimeError, match="existing catalog records were kept"):
        pipeline.run_pipeline(source, settings=settings, paper_id="paper", context="Caption.")
    assert state.published == before
    assert state.replacements == []
    state.answer = _positive()
    first = pipeline.run_pipeline(source, settings=settings, paper_id="paper", context="Caption.")
    assert first["cached_results"] == first["queried_candidates"] == 1
    assert first["database_rows"] == 2
    assert first["pages"] == 1
    assert "A549 cells" in state.requests[0][1]
    assert state.requests[0][1].startswith("Caption.")
    first_record = state.published["paper", source.name][0]
    write_pdf("Changed parent PDF, same rendered content")
    changed = pipeline.run_pipeline(source, settings=settings, paper_id="paper", context="Caption.")
    assert changed["run_dir"] != first["run_dir"]
    assert len(state.requests) == 5
    assert state.published["paper", source.name][0]["image_sha256"] == first_record["image_sha256"]


@pytest.mark.parametrize(
    "answer",
    [
        {"error": "bad_json"},
        {"is_western_blot": True, "bands": []},
        {"is_western_blot": True, "bands": _positive()["bands"] * 2},
    ],
)
def test_invalid_extractions_never_publish(ingestion, tmp_path, answer):
    settings, state = ingestion
    source = tmp_path / "blot.png"
    Image.new("RGB", (80, 40), "gray").save(source)
    state.answer = answer
    with pytest.raises((RuntimeError, ValueError)):
        pipeline.run_pipeline(source, settings=settings)
    assert state.replacements == []


def test_cli_failure_is_nonzero_and_does_not_publish(ingestion, tmp_path, monkeypatch):
    settings, state = ingestion
    source = tmp_path / "blot.png"
    Image.new("RGB", (80, 40), "gray").save(source)
    state.answer = RuntimeError("model unavailable")
    monkeypatch.setattr(pipeline.Settings, "from_env", lambda: settings)
    monkeypatch.setattr("sys.argv", ["hiveblot-ingest", str(source)])
    with pytest.raises(SystemExit) as error:
        pipeline.main()
    assert error.value.code == 1
    assert state.replacements == []
    failure = next(settings.data_dir.glob("runs/*/*/vlm_summary.json"))
    assert json.loads(failure.read_text())["failed_candidates"] == 1
