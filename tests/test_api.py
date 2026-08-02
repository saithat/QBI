import json

from hiveblot.api import (
    _candidate_extraction,
    _page_body,
    _page_context,
    _safe_candidate_path,
)


def test_record_assets_are_loaded_from_run_directory(tmp_path) -> None:
    data_dir = tmp_path / "data"
    run_dir = data_dir / "runs" / "paper"
    candidate_path = run_dir / "panel_candidates" / "page_004.png"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"image")
    (run_dir / "vlm_extractions.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "candidate_path": str(candidate_path),
                        "extraction": {"error": "invalid_extraction"},
                    }
                ),
                json.dumps(
                    {
                        "candidate_path": str(candidate_path),
                        "extraction": {
                            "is_western_blot": True,
                            "figure_caption": "A useful figure caption.",
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    (run_dir / "pages.json").write_text(
        json.dumps(
            [
                {
                    "page": 3,
                    "text": (
                        "Earlier results were inconclusive. The interaction was tested. "
                        "As shown in Figures 1A–C, the target decreased after treatment.\n"
                        "Oduah et al.\n10.3389/fonc.2024.1363543\nfrontiersin.org\n03"
                    ),
                },
                {
                    "page": 4,
                    "text": (
                        "This supports the proposed mechanism.\nA\nB\nFIGURE 1\n"
                        "A useful figure caption.\nFrontiers in Oncology\nfrontiersin.org\n04"
                    ),
                },
            ]
        )
    )

    safe_path = _safe_candidate_path(str(candidate_path), data_dir)

    assert safe_path == candidate_path
    assert _candidate_extraction(safe_path)["figure_caption"] == ("A useful figure caption.")
    context = _page_context(safe_path, 4, "FIGURE 1")
    assert "Figures 1A–C" in context
    assert "A useful figure caption" not in context
    assert "frontiersin.org" not in context


def test_page_context_follows_references_across_page_breaks(tmp_path) -> None:
    run_dir = tmp_path / "data" / "runs" / "paper"
    candidate_path = run_dir / "panel_candidates" / "page_005.png"
    candidate_path.parent.mkdir(parents=True)
    candidate_path.write_bytes(b"image")
    (run_dir / "pages.json").write_text(
        json.dumps(
            [
                {
                    "page": 3,
                    "text": (
                        "The interaction was evaluated before and after treatment. "
                        "As shown in Figures 2A–C, Hsp70 was bound to mutant p53, "
                        "but the detected p53 decreased in the\n"
                    ),
                },
                {
                    "page": 4,
                    "text": (
                        "input. More Hsp70 co-immunoprecipitated after treatment "
                        "(Figures 2D–F). These findings support Hsp70-mediated degradation. "
                        "Knockdown rescued mutant p53 (Figures 2G–I). Together, these "
                        "findings confirm the proposed mechanism.\n"
                        "A\nB\nFIGURE 1\nAn unrelated caption.\nfrontiersin.org\n04"
                    ),
                },
                {
                    "page": 5,
                    "text": (
                        "A different experiment is shown in Figures 3A, B.\n"
                        "A\nB\nFIGURE 2\nThe full Figure 2 caption.\nfrontiersin.org\n05"
                    ),
                },
                {
                    "page": 6,
                    "text": (
                        "A separate experiment appears in Supplementary Figure 2. "
                        "This text is unrelated."
                    ),
                },
            ]
        )
    )

    context = _page_context(candidate_path, 5, "FIGURE 2")

    assert "Figures 2A–C" in context
    assert "decreased in the input" in context
    assert "Figures 2G–I" in context
    assert "Figures 3A, B" not in context
    assert "Supplementary Figure 2" not in context
    assert "full Figure 2 caption" not in context


def test_page_body_removes_figure_and_footer_artifacts() -> None:
    text = (
        "Relevant co-\nimmunoprecipitation text.\nA\nB\nD\nFIGURE 2\nCaption text.\n"
        "Oduah et al.\n10.3389/fonc.2024.1363543\n"
        "Frontiers in Oncology\nfrontiersin.org\n05"
    )

    assert _page_body(text) == "Relevant co-immunoprecipitation text."


def test_candidate_image_must_stay_inside_runs_directory(tmp_path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "runs").mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")

    assert _safe_candidate_path(str(outside), data_dir) is None
