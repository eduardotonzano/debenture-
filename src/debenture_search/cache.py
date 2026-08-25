"""Cache local em SQLite (tabela ProviderFetchLog do modelo de dados).

Existe para um motivo único: o SND é consulta pontual disparada pelo usuário,
não uma fonte para bater com frequência. Uma busca repetida pelo mesmo
ISIN no mesmo dia deve usar o cache, não uma nova requisição HTTP.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_TTL = timedelta(hours=24)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_fetch_log (
    provider_name TEXT NOT NULL,
    query_type TEXT NOT NULL,
    query_value TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    status TEXT NOT NULL,
    raw_response TEXT,
    PRIMARY KEY (provider_name, query_type, query_value)
);
"""


class SqliteCache:
    """Cache chave->resposta bruta, com TTL, para chamadas de provider.

    Uso típico: o provider serializa o que baixou (ou o erro) como string
    (HTML bruto, JSON) e deixa o parsing por conta de quem chama — o cache
    não sabe nem precisa saber o formato do conteúdo.
    """

    def __init__(self, db_path: str | Path, ttl: timedelta = DEFAULT_TTL) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def get(self, provider_name: str, query_type: str, query_value: str) -> str | None:
        """Retorna o conteúdo em cache se existir e ainda estiver dentro do TTL."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, status, raw_response FROM provider_fetch_log "
                "WHERE provider_name = ? AND query_type = ? AND query_value = ?",
                (provider_name, query_type, query_value),
            ).fetchone()
        if row is None:
            return None
        fetched_at_str, status, raw_response = row
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if datetime.utcnow() - fetched_at > self.ttl:
            return None
        if status != "ok":
            return None
        return raw_response

    def set(
        self,
        provider_name: str,
        query_type: str,
        query_value: str,
        raw_response: str | None,
        status: str = "ok",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO provider_fetch_log "
                "(provider_name, query_type, query_value, fetched_at, status, raw_response) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_name, query_type, query_value) DO UPDATE SET "
                "fetched_at = excluded.fetched_at, status = excluded.status, "
                "raw_response = excluded.raw_response",
                (
                    provider_name,
                    query_type,
                    query_value,
                    datetime.utcnow().isoformat(),
                    status,
                    raw_response,
                ),
            )

    def get_json(self, provider_name: str, query_type: str, query_value: str) -> object | None:
        raw = self.get(provider_name, query_type, query_value)
        return json.loads(raw) if raw is not None else None

    def set_json(
        self, provider_name: str, query_type: str, query_value: str, value: object
    ) -> None:
        self.set(provider_name, query_type, query_value, json.dumps(value))
