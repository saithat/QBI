from hiveblot.records import flatten_records


def test_flatten_records_preserves_band_states_and_panel_context() -> None:
    figures = [
        {
            "paper_id": "10.1234/example",
            "page": 3,
            "candidate_path": "/data/runs/example/page_003_cand_0001.png",
            "extraction": {
                "is_western_blot": True,
                "figure_label": "Figure 2",
                "cell_line_tissue": "A549",
                "organism": "human",
                "treatment_context": "Nutlin-3",
                "panels": [
                    {
                        "panel_label": "B",
                        "treatment_context": "",
                        "targets_top_to_bottom": [
                            {
                                "row_index": 1,
                                "target": "p53",
                                "is_loading_control": False,
                                "confidence": "high",
                            },
                            {
                                "row_index": 2,
                                "target": "GAPDH",
                                "is_loading_control": True,
                                "confidence": "medium",
                            },
                        ],
                        "lanes_left_to_right": [
                            {"lane_index": 1, "condition": "vehicle"},
                            {"lane_index": 2, "condition": "Nutlin-3 10 uM"},
                        ],
                        "bands": [
                            {
                                "row_index": 1,
                                "target": "p53",
                                "lane_index": 1,
                                "band_state": "absent",
                                "confidence": "low",
                            },
                            {
                                "row_index": 1,
                                "target": "p53",
                                "lane_index": 2,
                                "band_state": "present",
                                "confidence": "high",
                            },
                            {
                                "row_index": 2,
                                "target": "GAPDH",
                                "lane_index": 2,
                                "band_state": "uncertain",
                                "confidence": 1.4,
                            },
                        ],
                    }
                ],
            },
        }
    ]

    records = flatten_records(figures, source_pdf="/data/input/example.pdf")

    assert [record["band_state"] for record in records] == ["absent", "present", "uncertain"]
    assert records[0]["sample"] == "A549"
    assert records[0]["organism"] == "human"
    assert records[0]["treatment_context"] == "Nutlin-3"
    assert records[1]["condition"] == "Nutlin-3 10 uM"
    assert records[2]["western_blot_type"] == "loading_control"
    assert records[2]["confidence"] == 1.0


def test_flatten_records_ignores_negative_or_invalid_bands() -> None:
    figures = [
        {"extraction": {"is_western_blot": False}},
        {
            "paper_id": "paper",
            "candidate_path": "candidate.png",
            "extraction": {
                "is_western_blot": True,
                "bands": [
                    {"target": "", "band_state": "present"},
                    {"target": "AKT", "band_state": "maybe"},
                ],
            },
        },
    ]

    assert flatten_records(figures) == []
