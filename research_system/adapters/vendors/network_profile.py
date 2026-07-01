from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class NetworkProbeResult:
    name: str
    url: str
    reachable: bool
    latency_ms: int | None
    error: str | None = None


class NetworkProfile:
    def __init__(self, timeout_seconds: float = 1.0) -> None:
        self.timeout_seconds = timeout_seconds

    def probe_defaults(self) -> dict[str, Any]:
        domestic = [
            self._probe("cninfo", "https://www.cninfo.com.cn"),
            self._probe("eastmoney", "https://www.eastmoney.com"),
        ]
        overseas = [
            self._probe("github", "https://github.com"),
        ]
        return {
            "timeout_seconds": self.timeout_seconds,
            "domestic": [item.__dict__ for item in domestic],
            "overseas": [item.__dict__ for item in overseas],
            "domestic_reachable": any(item.reachable for item in domestic),
            "overseas_reachable": any(item.reachable for item in overseas),
        }

    def _probe(self, name: str, url: str) -> NetworkProbeResult:
        import time

        request = Request(url, method="HEAD", headers={"User-Agent": "Cognitrade/1.0"})
        start = time.perf_counter()
        try:
            with urlopen(request, timeout=self.timeout_seconds):
                latency_ms = int((time.perf_counter() - start) * 1000)
                return NetworkProbeResult(name=name, url=url, reachable=True, latency_ms=latency_ms)
        except Exception as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            host = urlparse(url).netloc or url
            return NetworkProbeResult(
                name=name,
                url=url,
                reachable=False,
                latency_ms=latency_ms,
                error=f"{host}: {exc.__class__.__name__}",
            )
