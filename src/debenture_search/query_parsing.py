"""Inferência do tipo de busca (ISIN / código de ativo / emissor) a partir de
um termo digitado — compartilhado entre CLI e web para não duplicar a
heurística."""

from __future__ import annotations

import re

from debenture_search.models import SearchQuery

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")  # formato ISO 6166
# Código de ativo de debênture sempre tem pelo menos um dígito (ex.: BODY12,
# TEPA23) — exigir isso evita classificar nome de empresa em maiúsculas sem
# número (ex.: "BODYTECH") como se fosse um código de ativo.
_CODIGO_ATIVO_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{4,8}$")


def infer_query(termo: str, tipo: str | None = None) -> SearchQuery:
    """tipo, se informado, força a interpretação: 'isin' | 'codigo_ativo' | 'emissor'."""
    termo_normalizado = termo.strip()
    if tipo == "isin":
        return SearchQuery(isin=termo_normalizado.upper())
    if tipo == "codigo_ativo":
        return SearchQuery(codigo_ativo=termo_normalizado.upper())
    if tipo == "emissor":
        return SearchQuery(nome_emissor=termo_normalizado)

    upper = termo_normalizado.upper()
    if _ISIN_RE.match(upper):
        return SearchQuery(isin=upper)
    if _CODIGO_ATIVO_RE.match(upper):
        return SearchQuery(codigo_ativo=upper)
    return SearchQuery(nome_emissor=termo_normalizado)
