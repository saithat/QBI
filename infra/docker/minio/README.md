# Local MinIO image

The upstream `RELEASE.2025-10-15T17-29-55Z` fixes a session-policy privilege escalation but did
not publish an official container for that release. This local-development image follows the
upstream release instruction to build the tagged source. It uses pinned multi-architecture Go and
Alpine base-image digests and runs as UID 10001.

This is local infrastructure only. Production object storage remains externally managed.
