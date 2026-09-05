from hiveblot.pdf_preprocess import (
    build_text_context_for_candidate,
    expand_bbox,
    filter_candidates_for_llm,
)


def test_text_helpers_build_bounded_candidate_context() -> None:
    pages = [
        {"page": 1, "text": "Methods used A549 cells."},
        {"page": 2, "text": "Figure 3. p53 immunoblot after Nutlin treatment."},
        {"page": 3, "text": "Additional human cell results."},
    ]
    paper_text = "\n".join(page["text"] for page in pages)
    candidate = {
        "paper_id": "paper-1",
        "page": 2,
        "bbox_page": [10, 20, 100, 200],
    }

    context = build_text_context_for_candidate(candidate, pages, paper_text, max_chars=1200)

    assert "PAPER_ID: paper-1" in context
    assert len(context) <= 1200


def test_candidate_helpers_bound_boxes_and_thresholds() -> None:
    assert expand_bbox(10, 20, 100, 50, 500, 400) == (0, 0, 130, 90)
    candidates = [{"cv_score": 0.64}, {"cv_score": 0.65}, {"cv_score": 0.9}]
    assert filter_candidates_for_llm(candidates) == candidates[1:]
