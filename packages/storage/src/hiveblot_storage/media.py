"""Small, deterministic media sniffer for the artifact types accepted by PRD-002."""

from __future__ import annotations

import csv
import io
import json
import xml.etree.ElementTree as ET

from .errors import InvalidArtifact

SNIFF_BYTES = 262_144
SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "application/gzip",
        "application/json",
        "application/pdf",
        "application/xml",
        "application/x-tar",
        "application/zip",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "image/webp",
        "text/csv",
        "text/tab-separated-values",
    }
)

MEDIA_TYPE_ALIASES = {
    "application/x-gzip": "application/gzip",
    "application/x-zip-compressed": "application/zip",
    "image/jpg": "image/jpeg",
    "text/xml": "application/xml",
    "text/tab-separated-values": "text/tab-separated-values",
}


def normalize_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.partition(";")[0].strip().casefold()
    return MEDIA_TYPE_ALIASES.get(normalized, normalized)


def detect_media_type(sample: bytes, *, sample_is_complete: bool = True) -> str:
    """Identify supported content from bytes rather than a filename extension."""

    if sample.startswith(b"%PDF-"):
        return "application/pdf"
    if sample.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if sample.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if sample.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if sample.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if len(sample) >= 12 and sample[:4] == b"RIFF" and sample[8:12] == b"WEBP":
        return "image/webp"
    if sample.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        return "application/zip"
    if sample.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if len(sample) > 262 and sample[257:262] == b"ustar":
        return "application/x-tar"

    try:
        text = sample.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidArtifact("unsupported or unrecognized binary media type") from exc

    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        if not sample_is_complete:
            return "application/json"
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidArtifact("content resembles JSON but is invalid") from exc
        if isinstance(value, (dict, list)):
            return "application/json"

    if stripped.startswith(("<?xml", "<")):
        lowered = sample[:4096].lower()
        if b"<!doctype" in lowered or b"<!entity" in lowered:
            return "application/xml"
        if not sample_is_complete:
            return "application/xml"
        try:
            ET.fromstring(sample)
        except ET.ParseError as exc:
            raise InvalidArtifact("content resembles XML but is invalid") from exc
        return "application/xml"

    lines = [line for line in text.splitlines() if line.strip()][:20]
    if len(lines) >= 2:
        for delimiter, media_type in (
            ("\t", "text/tab-separated-values"),
            (",", "text/csv"),
        ):
            try:
                rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
            except csv.Error:
                continue
            widths = {len(row) for row in rows}
            if len(widths) == 1 and next(iter(widths)) >= 2:
                return media_type

    raise InvalidArtifact("unsupported or unrecognized media type")


def validate_declared_media_type(detected: str, declared: str | None) -> None:
    normalized = normalize_media_type(declared)
    if normalized in {None, "application/octet-stream"}:
        return
    if normalized != detected:
        raise InvalidArtifact(
            f"declared media type {normalized!r} does not match detected type {detected!r}"
        )
