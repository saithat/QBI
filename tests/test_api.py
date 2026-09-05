import json

from hiveblot.api import (
    _page_context,
    _safe_candidate_path,
)


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


def test_candidate_image_must_stay_inside_runs_directory(tmp_path) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "runs").mkdir(parents=True)
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"image")

    assert _safe_candidate_path(str(outside), data_dir) is None
