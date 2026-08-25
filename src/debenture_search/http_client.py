"""Cliente HTTP com rate limit para scraping respeitoso de fontes públicas.

Regra do projeto: o SND (e qualquer outra fonte HTML pública) é consulta
pontual disparada pela busca do usuário, não um pipeline de dados em massa.
Este cliente força um intervalo mínimo entre requisições ao mesmo host e
não abre requisições em paralelo — mesmo que o código que o chama tente.
"""

from __future__ import annotations

import time
from threading import Lock

import httpx

DEFAULT_MIN_INTERVAL_SECONDS = 2.0
DEFAULT_USER_AGENT = "debenture-search/0.1 (uso pessoal/pesquisa; contato: ver README)"


class RateLimitedHttpClient:
    def __init__(
        self,
        min_interval_seconds: float = DEFAULT_MIN_INTERVAL_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._lock = Lock()
        self._last_request_at: float | None = None
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def _throttle(self) -> None:
        with self._lock:
            if self._last_request_at is not None:
                elapsed = time.monotonic() - self._last_request_at
                remaining = self._min_interval - elapsed
                if remaining > 0:
                    time.sleep(remaining)
            self._last_request_at = time.monotonic()

    def get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        self._throttle()
        return self._client.get(url, params=params)

    def post(self, url: str, data: dict[str, str] | None = None) -> httpx.Response:
        self._throttle()
        return self._client.post(url, data=data)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "RateLimitedHttpClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
