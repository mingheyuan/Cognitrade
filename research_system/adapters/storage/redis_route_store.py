from __future__ import annotations


class RouteTableBackendError(Exception):
    pass


class InMemoryRouteTableBackend:
    def __init__(self, initial_payloads: dict[str, str] | None = None) -> None:
        self._payloads = dict(initial_payloads or {})

    def get_payload(self, key: str) -> str | None:
        payload = self._payloads.get(key)
        return payload if isinstance(payload, str) and payload.strip() else None

    def set_payload(self, key: str, payload: str, *, nx: bool = False) -> bool:
        if nx and key in self._payloads:
            return False
        self._payloads[key] = payload
        return True

    def ping(self) -> bool:
        return True


class RedisRouteTableBackend:
    def __init__(self, redis_url: str, *, timeout_seconds: float = 0.2) -> None:
        self.redis_url = redis_url
        self.timeout_seconds = timeout_seconds
        self._client = None

    def get_payload(self, key: str) -> str | None:
        try:
            payload = self._client_instance().get(key)
        except Exception as exc:  # pragma: no cover - network/client errors
            raise RouteTableBackendError(f"failed to read route table from redis: {exc}") from exc
        return payload if isinstance(payload, str) and payload.strip() else None

    def set_payload(self, key: str, payload: str, *, nx: bool = False) -> bool:
        try:
            return bool(self._client_instance().set(name=key, value=payload, nx=nx))
        except Exception as exc:  # pragma: no cover - network/client errors
            raise RouteTableBackendError(f"failed to write route table to redis: {exc}") from exc

    def ping(self) -> bool:
        try:
            return bool(self._client_instance().ping())
        except Exception as exc:  # pragma: no cover - network/client errors
            raise RouteTableBackendError(f"failed to ping redis route store: {exc}") from exc

    def _client_instance(self):
        if self._client is not None:
            return self._client
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise RouteTableBackendError("redis package is not installed") from exc
        self._client = redis.Redis.from_url(
            self.redis_url,
            decode_responses=True,
            socket_connect_timeout=self.timeout_seconds,
            socket_timeout=self.timeout_seconds,
        )
        return self._client
