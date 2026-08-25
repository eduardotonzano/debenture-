"""CLI de teste ponta a ponta da Fase 1.

    python -m debenture_search busca TEPA23
    python -m debenture_search busca BRTEPADBS0XX --tipo isin
    python -m debenture_search busca "Tegma" --tipo emissor
    python -m debenture_search manual-set TEPA23 rating "AA-" --fonte "Fitch, 03/2026"

Sem UI: o objetivo é validar que busca -> resolução -> merge de fontes
funciona ponta a ponta antes de existir qualquer tela (ver plano de fases).
"""

from __future__ import annotations

import json
import re

import typer

from debenture_search.aggregator import Ambiguous
from debenture_search.compose import build_aggregator
from debenture_search.config import MANUAL_INPUT_DB_PATH
from debenture_search.models import DebentureRef, SearchQuery
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.serialization import debenture_to_dict

app = typer.Typer(add_completion=False)

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")  # heurística: formato ISO 6166
_CODIGO_ATIVO_RE = re.compile(r"^[A-Z0-9]{4,8}$")


def _infer_query(termo: str, tipo: str | None) -> SearchQuery:
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


@app.command()
def busca(
    termo: str,
    tipo: str = typer.Option(
        None, "--tipo", help="Forçar o tipo do termo: isin | codigo_ativo | emissor"
    ),
) -> None:
    """Busca uma debênture por ISIN, código de ativo ou nome de emissor."""
    query = _infer_query(termo, tipo)
    aggregator = build_aggregator()

    try:
        resultado = aggregator.search_and_build(query)
    except Exception as exc:  # noqa: BLE001 - falha de fonte externa, não bug interno
        typer.echo(f"Não foi possível resolver a busca via fontes automáticas: {exc}")
        typer.echo("Nenhum dado inventado — a busca simplesmente não pôde ser concluída agora.")
        raise typer.Exit(code=2) from None

    if isinstance(resultado, Ambiguous):
        typer.echo("Mais de uma série encontrada, escolha uma e busque de novo por ISIN:")
        for ref in resultado.candidatos:
            typer.echo(f"  - {ref.nome_emissor} | ativo={ref.codigo_ativo} | isin={ref.isin}")
        raise typer.Exit(code=1)

    typer.echo(json.dumps(debenture_to_dict(resultado), ensure_ascii=False, indent=2))


@app.command("manual-set")
def manual_set(
    termo: str,
    campo: str,
    valor: str,
    fonte: str = typer.Option(..., "--fonte", help='Descrição da fonte, ex.: "Fitch, 03/2026"'),
    tipo: str = typer.Option(None, "--tipo", help="isin | codigo_ativo | emissor"),
) -> None:
    """Registra (ou atualiza) um override manual para um campo de uma série."""
    query = _infer_query(termo, tipo)
    ref = DebentureRef(isin=query.isin, codigo_ativo=query.codigo_ativo, nome_emissor=query.nome_emissor or "")
    provider = ManualInputProvider(MANUAL_INPUT_DB_PATH)
    provider.set_field(ref, campo, valor, fonte)
    typer.echo(f"Override manual salvo: {campo}={valor!r} ({fonte})")


if __name__ == "__main__":
    app()
