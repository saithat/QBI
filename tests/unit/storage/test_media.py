import pytest
from hiveblot_storage.errors import InvalidArtifact
from hiveblot_storage.media import detect_media_type, validate_declared_media_type


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7\nbody", "application/pdf"),
        (b"\x89PNG\r\n\x1a\nbody", "image/png"),
        (b'{"paper": "10.1/example"}', "application/json"),
        (b'<?xml version="1.0"?><records><record /></records>', "application/xml"),
        (b"target,lane\np53,1\nactin,2\n", "text/csv"),
        (b"target\tlane\np53\t1\nactin\t2\n", "text/tab-separated-values"),
        (b"PK\x03\x04archive", "application/zip"),
    ],
)
def test_detect_media_type_uses_bytes(content: bytes, expected: str) -> None:
    assert detect_media_type(content) == expected


def test_declared_media_type_must_match_detected_bytes() -> None:
    with pytest.raises(InvalidArtifact, match="does not match"):
        validate_declared_media_type("image/png", "application/pdf")


def test_unknown_binary_is_rejected() -> None:
    with pytest.raises(InvalidArtifact, match="unsupported"):
        detect_media_type(b"\x00\x01\x02not-a-supported-file")


def test_json_prefix_can_be_sniffed_before_the_complete_large_document() -> None:
    assert detect_media_type(b'{"rows": [', sample_is_complete=False) == "application/json"


def test_xml_document_types_are_classified_without_expanding_entities() -> None:
    assert detect_media_type(b"<!DOCTYPE records><records />") == "application/xml"
