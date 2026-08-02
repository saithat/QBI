"""Compatibility imports for the pre-foundation ASGI module path.

New deployment code should import :mod:`apps.api.main`. This facade remains so existing
scripts and tests do not lose useful hackathon behavior during PRD-001.
"""

from apps.api import application as _application

INDEX = _application.INDEX
app = _application.app
db = _application.db
get_settings = _application.get_settings
health = _application.health
index = _application.index
lifespan = _application.lifespan
record_detail = _application.record_detail
record_image = _application.record_image
records = _application.records
search = _application.search

_candidate_extraction = _application._candidate_extraction
_filters_response = _application._filters_response
_page_body = _application._page_body
_page_context = _application._page_context
_prediction_to_criteria = _application._prediction_to_criteria
_record_response = _application._record_response
_safe_candidate_path = _application._safe_candidate_path
_sentences = _application._sentences

__all__ = [
    "INDEX",
    "app",
    "db",
    "get_settings",
    "health",
    "index",
    "lifespan",
    "record_detail",
    "record_image",
    "records",
    "search",
]
