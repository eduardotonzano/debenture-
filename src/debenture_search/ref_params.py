"""Conversão entre DebentureRef e parâmetros de querystring — usado pela web
para montar/ler links da ficha (ex.: /ficha?codigo_ativo=BODY12)."""

from __future__ import annotations

from debenture_search.models import DebentureRef


def ref_to_params(ref: DebentureRef) -> dict[str, str]:
    """Prioriza código de ativo (é a rota mais confiável no SND), depois
    ISIN, depois nome do emissor — só o necessário pra identificar de novo
    a mesma série numa nova consulta."""
    params: dict[str, str] = {}
    if ref.codigo_ativo:
        params["codigo_ativo"] = ref.codigo_ativo.strip()
    elif ref.isin:
        params["isin"] = ref.isin.strip()
    if ref.nome_emissor:
        params["nome_emissor"] = ref.nome_emissor
    return params


def ref_from_params(codigo_ativo: str | None, isin: str | None, nome_emissor: str | None) -> DebentureRef:
    return DebentureRef(
        isin=isin or None,
        codigo_ativo=codigo_ativo or None,
        nome_emissor=nome_emissor or "",
    )
