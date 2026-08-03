"""Shared per-domain request permits independent of worker replica count."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hiveblot_contracts import DomainRequestPermit


class DomainPermitRepository(Protocol):
    def try_acquire_domain_permit(
        self,
        *,
        domain: str,
        worker_id: str,
        acquired_at: datetime,
        expires_at: datetime,
    ) -> tuple[DomainRequestPermit | None, datetime]: ...

    def release_domain_permit(
        self,
        permit: DomainRequestPermit,
        *,
        released_at: datetime,
    ) -> None: ...


class DomainRateLimitTimeout(RuntimeError):
    """No shared domain permit became available inside the configured wait."""


class SharedDomainRateLimiter:
    def __init__(
        self,
        repository: DomainPermitRepository,
        *,
        permit_seconds: int = 60,
        maximum_wait_seconds: float = 30,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if not 1 <= permit_seconds <= 3600:
            raise ValueError("domain permit_seconds must be between 1 and 3600")
        if not 0 < maximum_wait_seconds <= 3600:
            raise ValueError("domain maximum wait must be between 0 and 3600 seconds")
        self._repository = repository
        self._permit_seconds = permit_seconds
        self._maximum_wait_seconds = maximum_wait_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep

    def acquire(self, domain: str, *, worker_id: str) -> DomainRequestPermit:
        deadline = self._monotonic() + self._maximum_wait_seconds
        while True:
            now = self._clock()
            permit, retry_at = self._repository.try_acquire_domain_permit(
                domain=domain,
                worker_id=worker_id,
                acquired_at=now,
                expires_at=now + timedelta(seconds=self._permit_seconds),
            )
            if permit is not None:
                return permit
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise DomainRateLimitTimeout(
                    f"shared request permit for {domain!r} was not available"
                )
            source_delay = max(0.001, (retry_at - now).total_seconds())
            self._sleeper(min(remaining, source_delay, 0.25))

    def release(self, permit: DomainRequestPermit) -> None:
        self._repository.release_domain_permit(permit, released_at=self._clock())
