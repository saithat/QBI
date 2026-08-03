from __future__ import annotations

import pytest
from hiveblot_crawler import (
    InvalidDiscoveryRecord,
    canonical_pmc_accession,
    normalize_source_url,
    pmc_identity_key,
)


def test_pmc_accessions_and_identity_keys_are_canonical() -> None:
    assert canonical_pmc_accession(" pmc123 ") == "PMC123"
    assert canonical_pmc_accession("123") == "PMC123"
    assert pmc_identity_key("PMC123") == "pmc:pmc123"
    assert pmc_identity_key("123", metadata=True) == "pmc-metadata:pmc123"


@pytest.mark.parametrize("value", ["PMC0", "doi:123", "", "PMC 123"])
def test_invalid_pmc_accessions_fail(value: str) -> None:
    with pytest.raises(InvalidDiscoveryRecord):
        canonical_pmc_accession(value)


def test_url_normalization_deduplicates_safe_identity_variants() -> None:
    assert normalize_source_url("HTTPS://EXAMPLE.COM:443/path?q=A#fragment") == (
        "https://example.com/path?q=A"
    )
    assert normalize_source_url("http://example.com") == "http://example.com/"
    assert normalize_source_url("https://bücher.example/data") == (
        "https://xn--bcher-kva.example/data"
    )


@pytest.mark.parametrize(
    "value",
    ["file:///tmp/paper.pdf", "https://user:pass@example.test/x", "https://exa mple/x"],
)
def test_unsafe_source_urls_fail(value: str) -> None:
    with pytest.raises(InvalidDiscoveryRecord):
        normalize_source_url(value)
