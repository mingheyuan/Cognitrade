from __future__ import annotations

import contextlib
import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from weakref import WeakValueDictionary

try:  # pragma: no cover - platform-specific import
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # pragma: no cover - platform-specific import
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

from research_system.runtime.core.id_utils import generate_ulid
from research_system.runtime.storage.jsonl import read_json, write_json

_FILE_LEASE_LOCKS_GUARD = RLock()
_FILE_LEASE_LOCKS: WeakValueDictionary[str, RLock] = WeakValueDictionary()


def _process_file_lock(path: Path) -> RLock:
    key = str(path.resolve())
    with _FILE_LEASE_LOCKS_GUARD:
        return _FILE_LEASE_LOCKS.setdefault(key, RLock())


@contextlib.contextmanager
def _exclusive_file_lock(path: Path):
    """Serialize a lease mutation across threads and worker processes."""

    process_lock = _process_file_lock(path)
    with process_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0, 2)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResourceLeaseStoreError(RuntimeError):
    """Raised when the configured lease backend cannot be used."""


@dataclass(frozen=True)
class ResourceLease:
    lease_id: str
    resource_key: str
    expires_at: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ResourceLeaseStore(Protocol):
    """Backend-neutral contract for an exclusive Tool resource lease."""

    def acquire(
        self,
        *,
        resource_key: str,
        metadata: dict[str, Any],
        lease_seconds: float,
    ) -> ResourceLease | None: ...

    def renew(self, lease: ResourceLease, *, lease_seconds: float) -> ResourceLease | None: ...

    def release(self, lease: ResourceLease) -> bool: ...


def _expires_at(lease_seconds: float) -> float:
    return (utc_now().timestamp() + float(lease_seconds))


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _parse_expiry(value: Any) -> datetime:
    try:
        expiry = datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ResourceLeaseStoreError("resource lease expiry is invalid") from exc
    return expiry if expiry.tzinfo is not None else expiry.replace(tzinfo=timezone.utc)


class FileResourceLeaseStore:
    """Local/shared-filesystem lease backend.

    Acquisition uses exclusive file creation so separate worker processes do
    not claim the same live lease. This remains the default development
    backend; production deployments can use Redis for atomic expiry and
    renewal across workers and hosts.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def acquire(
        self,
        *,
        resource_key: str,
        metadata: dict[str, Any],
        lease_seconds: float,
    ) -> ResourceLease | None:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ResourceLeaseStoreError(
                f"resource lease store is unavailable: {self.root}"
            ) from exc

        path = self._path(resource_key)
        lease = self._new_lease(resource_key, metadata, lease_seconds)
        payload = self._payload(lease)
        try:
            with _exclusive_file_lock(self._lock_path(resource_key)):
                existing = read_json(path)
                if isinstance(existing, dict):
                    existing_expiry = _parse_expiry(existing.get("expires_at"))
                    if existing_expiry > utc_now():
                        return None
                    path.unlink(missing_ok=True)
                elif path.exists():
                    raise ResourceLeaseStoreError("resource lease file is invalid")
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                return lease
        except ResourceLeaseStoreError:
            raise
        except OSError as exc:
            raise ResourceLeaseStoreError(
                "resource lease could not be acquired"
            ) from exc

    def renew(self, lease: ResourceLease, *, lease_seconds: float) -> ResourceLease | None:
        path = self._path(lease.resource_key)
        try:
            with _exclusive_file_lock(self._lock_path(lease.resource_key)):
                current = read_json(path)
                if not isinstance(current, dict) or current.get("lease_id") != lease.lease_id:
                    return None
                if _parse_expiry(current.get("expires_at")) <= utc_now():
                    return None
                renewed = ResourceLease(
                    lease_id=lease.lease_id,
                    resource_key=lease.resource_key,
                    expires_at=_iso_timestamp(_expires_at(lease_seconds)),
                    metadata=dict(lease.metadata),
                )
                write_json(path, self._payload(renewed))
        except OSError:
            return None
        return renewed

    def release(self, lease: ResourceLease) -> bool:
        path = self._path(lease.resource_key)
        try:
            with _exclusive_file_lock(self._lock_path(lease.resource_key)):
                current = read_json(path)
                if isinstance(current, dict) and current.get("lease_id") == lease.lease_id:
                    path.unlink(missing_ok=True)
                    return True
        except OSError:
            return False
        return False

    def _path(self, resource_key: str) -> Path:
        digest = hashlib.sha256(resource_key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.json"

    def _lock_path(self, resource_key: str) -> Path:
        digest = hashlib.sha256(resource_key.encode("utf-8")).hexdigest()
        return self.root / f"{digest}.lock"

    @staticmethod
    def _new_lease(
        resource_key: str,
        metadata: dict[str, Any],
        lease_seconds: float,
    ) -> ResourceLease:
        return ResourceLease(
            lease_id=generate_ulid(),
            resource_key=resource_key,
            expires_at=_iso_timestamp(_expires_at(lease_seconds)),
            metadata=dict(metadata),
        )

    @staticmethod
    def _payload(lease: ResourceLease) -> dict[str, Any]:
        return {
            "lease_id": lease.lease_id,
            "resource_key": lease.resource_key,
            "expires_at": lease.expires_at,
            **dict(lease.metadata),
        }


class RedisResourceLeaseStore:
    """Redis lease backend with atomic acquire, renew and owner-checked release."""

    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    end
    return 0
    """
    _RENEW_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('pexpire', KEYS[1], ARGV[2])
    end
    return 0
    """

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "cognitrade:tool:lease:",
        timeout_seconds: float = 0.5,
        client: Any | None = None,
    ) -> None:
        self.redis_url = redis_url
        self.key_prefix = key_prefix
        self.timeout_seconds = timeout_seconds
        self._client = client

    def acquire(
        self,
        *,
        resource_key: str,
        metadata: dict[str, Any],
        lease_seconds: float,
    ) -> ResourceLease | None:
        lease = ResourceLease(
            lease_id=generate_ulid(),
            resource_key=resource_key,
            expires_at=_iso_timestamp(_expires_at(lease_seconds)),
            metadata=dict(metadata),
        )
        # Redis only needs the owner token for atomic release/renew. The
        # metadata remains in the returned lease and in the audit event.
        payload = lease.lease_id
        try:
            claimed = self._client_instance().set(
                name=self._key(resource_key),
                value=payload,
                nx=True,
                px=self._ttl_ms(lease_seconds),
            )
        except Exception as exc:  # pragma: no cover - depends on Redis/network
            raise ResourceLeaseStoreError(f"failed to acquire Redis resource lease: {exc}") from exc
        return lease if claimed else None

    def renew(self, lease: ResourceLease, *, lease_seconds: float) -> ResourceLease | None:
        key = self._key(lease.resource_key)
        try:
            renewed = self._client_instance().eval(
                self._RENEW_SCRIPT,
                1,
                key,
                self._lease_token(lease),
                self._ttl_ms(lease_seconds),
            )
        except Exception as exc:  # pragma: no cover - depends on Redis/network
            raise ResourceLeaseStoreError(f"failed to renew Redis resource lease: {exc}") from exc
        if not renewed:
            return None
        return ResourceLease(
            lease_id=lease.lease_id,
            resource_key=lease.resource_key,
            expires_at=_iso_timestamp(_expires_at(lease_seconds)),
            metadata=dict(lease.metadata),
        )

    def release(self, lease: ResourceLease) -> bool:
        try:
            released = self._client_instance().eval(
                self._RELEASE_SCRIPT,
                1,
                self._key(lease.resource_key),
                self._lease_token(lease),
            )
        except Exception as exc:  # pragma: no cover - depends on Redis/network
            raise ResourceLeaseStoreError(f"failed to release Redis resource lease: {exc}") from exc
        return bool(released)

    def _key(self, resource_key: str) -> str:
        digest = hashlib.sha256(resource_key.encode("utf-8")).hexdigest()
        return f"{self.key_prefix}{digest}"

    @staticmethod
    def _lease_token(lease: ResourceLease) -> str:
        return lease.lease_id

    @staticmethod
    def _ttl_ms(lease_seconds: float) -> int:
        return max(1, math.ceil(float(lease_seconds) * 1000))

    def _client_instance(self):
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ResourceLeaseStoreError("redis package is not installed") from exc
        self._client = redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=self.timeout_seconds,
            socket_timeout=self.timeout_seconds,
        )
        return self._client


def build_resource_lease_store(
    *,
    settings: Any | None = None,
    resource_lease_root: Path | None = None,
) -> ResourceLeaseStore | None:
    """Build the configured backend without forcing Redis in local tests."""
    backend = str(getattr(settings, "tool_resource_lease_backend", "file") or "file").lower()
    if backend == "redis":
        redis_url = str(getattr(settings, "tool_resource_lease_redis_url", "") or "").strip()
        if not redis_url:
            raise ResourceLeaseStoreError(
                "tool_resource_lease_backend=redis requires tool_resource_lease_redis_url"
            )
        return RedisResourceLeaseStore(
            redis_url,
            key_prefix=str(
                getattr(settings, "tool_resource_lease_key_prefix", "cognitrade:tool:lease:")
            ),
            timeout_seconds=float(
                getattr(settings, "tool_resource_lease_redis_timeout_seconds", 0.5)
            ),
        )
    if backend != "file":
        raise ResourceLeaseStoreError(f"unknown Tool resource lease backend: {backend}")
    root = resource_lease_root or getattr(settings, "tool_resource_lease_root", None)
    return FileResourceLeaseStore(Path(root)) if root else None


__all__ = [
    "FileResourceLeaseStore",
    "RedisResourceLeaseStore",
    "ResourceLease",
    "ResourceLeaseStore",
    "ResourceLeaseStoreError",
    "build_resource_lease_store",
]
