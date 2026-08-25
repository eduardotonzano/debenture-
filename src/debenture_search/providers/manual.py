"""Provider de overrides manuais (rating, PU, spread etc. colados pelo usuário).

Sempre disponível (não depende de credencial nem de rede) e tem a maior
precedência no merge do DebentureAggregator: um dado que o usuário colou
explicitamente nunca é sobrescrito por uma fonte automática.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from debenture_search.models import Debenture, DebentureRef, SourcedValue
from debenture_search.providers.base import ProviderResult

FONTE = "Manual"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_input (
    debenture_key TEXT NOT NULL,
    campo TEXT NOT NULL,
    valor TEXT NOT NULL,
    fonte_descricao TEXT NOT NULL,
    inserido_em TEXT NOT NULL,
    PRIMARY KEY (debenture_key, campo)
);
"""

# Campos de Debenture que este provider pode preencher via override manual.
CAMPOS_SUPORTADOS = {"rating", "taxa", "quantidade_mercado"}


def _key(ref: DebentureRef) -> str:
    return ref.isin or ref.codigo_ativo or ref.nome_emissor


class ManualInputProvider:
    name = FONTE

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def is_available(self) -> bool:
        return True

    def set_field(self, ref: DebentureRef, campo: str, valor: str, fonte_descricao: str) -> None:
        if campo not in CAMPOS_SUPORTADOS:
            raise ValueError(f"Campo '{campo}' não suportado para override manual: {CAMPOS_SUPORTADOS}")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO manual_input (debenture_key, campo, valor, fonte_descricao, inserido_em) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(debenture_key, campo) DO UPDATE SET "
                "valor = excluded.valor, fonte_descricao = excluded.fonte_descricao, "
                "inserido_em = excluded.inserido_em",
                (_key(ref), campo, valor, fonte_descricao, datetime.utcnow().isoformat()),
            )

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT campo, valor, fonte_descricao FROM manual_input WHERE debenture_key = ?",
                (_key(ref),),
            ).fetchall()

        debenture = Debenture()
        for campo, valor, fonte_descricao in rows:
            setattr(debenture, campo, SourcedValue(valor, fonte=f"{FONTE} ({fonte_descricao})"))
        return ProviderResult.ok(self.name, debenture)
