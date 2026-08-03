"""Deterministic source identities and URL normalization."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from .errors import InvalidDiscoveryRecord

_PMC_ACCESSION = re.compile(r"^(?:PMC)?([1-9][0-9]*)$", re.IGNORECASE)


def canonical_pmc_accession(value: str) -> str:
    match = _PMC_ACCESSION.fullmatch(value.strip())
    if match is None:
        raise InvalidDiscoveryRecord(f"invalid PMCID {value!r}")
    return f"PMC{match.group(1)}"


def pmc_identity_key(accession: str, *, metadata: bool = False) -> str:
    normalized = canonical_pmc_accession(accession).casefold()
    prefix = "pmc-metadata" if metadata else "pmc"
    return f"{prefix}:{normalized}"


def normalize_source_url(value: str) -> str:
    """Normalize safe HTTP(S) identity fields without changing query semantics."""

    candidate = value.strip()
    if not candidate or any(character.isspace() for character in candidate):
        raise InvalidDiscoveryRecord("source URL cannot be blank or contain whitespace")
    parsed = urlsplit(candidate)
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise InvalidDiscoveryRecord("source URL must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise InvalidDiscoveryRecord("source URL cannot contain credentials")
    if parsed.hostname is None:
        raise InvalidDiscoveryRecord("source URL requires a host")
    try:
        host = parsed.hostname.encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise InvalidDiscoveryRecord("source URL host or port is invalid") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    scheme = parsed.scheme.casefold()
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    authority = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, authority, path, parsed.query, ""))
