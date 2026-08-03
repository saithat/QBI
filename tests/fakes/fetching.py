from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from uuid import UUID, uuid4

from hiveblot_contracts import DomainRequestPermit
from hiveblot_crawler import RobotsCacheEntry


class InMemoryDomainPermitRepository:
    def __init__(
        self,
        *,
        minimum_interval_milliseconds: int = 0,
        maximum_concurrency: int = 1,
    ) -> None:
        self.minimum_interval_milliseconds = minimum_interval_milliseconds
        self.maximum_concurrency = maximum_concurrency
        self.next_request_at: dict[str, datetime] = {}
        self.permits: dict[UUID, DomainRequestPermit] = {}
        self._lock = Lock()

    def try_acquire_domain_permit(
        self,
        *,
        domain: str,
        worker_id: str,
        acquired_at: datetime,
        expires_at: datetime,
    ) -> tuple[DomainRequestPermit | None, datetime]:
        with self._lock:
            self.permits = {
                key: permit
                for key, permit in self.permits.items()
                if permit.expires_at > acquired_at
            }
            active = [permit for permit in self.permits.values() if permit.domain == domain]
            rate_ready = self.next_request_at.get(domain, acquired_at)
            concurrency_ready = min(
                (permit.expires_at for permit in active),
                default=acquired_at,
            )
            if len(active) >= self.maximum_concurrency or rate_ready > acquired_at:
                retry_at = max(
                    rate_ready if rate_ready > acquired_at else acquired_at,
                    concurrency_ready if len(active) >= self.maximum_concurrency else acquired_at,
                )
                return None, retry_at
            permit = DomainRequestPermit(
                permit_id=uuid4(),
                domain=domain,
                worker_id=worker_id,
                acquired_at=acquired_at,
                expires_at=expires_at,
            )
            self.permits[permit.permit_id] = permit
            self.next_request_at[domain] = acquired_at + timedelta(
                milliseconds=self.minimum_interval_milliseconds
            )
            return permit, self.next_request_at[domain]

    def release_domain_permit(
        self,
        permit: DomainRequestPermit,
        *,
        released_at: datetime,
    ) -> None:
        del released_at
        with self._lock:
            self.permits.pop(permit.permit_id, None)


class InMemoryRobotsCache:
    def __init__(self) -> None:
        self.entries: dict[str, RobotsCacheEntry] = {}

    def get_robots(self, origin: str, *, at: datetime) -> RobotsCacheEntry | None:
        entry = self.entries.get(origin)
        return entry if entry is not None and entry.expires_at > at else None

    def store_robots(self, entry: RobotsCacheEntry) -> None:
        self.entries[entry.origin] = entry
