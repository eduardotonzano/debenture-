"""Testa as funções _parse_* do provider SND contra fixtures SINTÉTICAS.

IMPORTANTE: as fixtures em tests/fixtures/snd_*.html não são HTML real
capturado do site — foram escritas à mão para bater com os seletores
placeholder de providers/snd.py (ver docstring do módulo). Esses testes
provam que a lógica de parsing funciona estruturalmente, não que os
seletores estão corretos para o site real. Depois que alguém rodar isso
fora deste ambiente (onde o egress para debentures.com.br não é bloqueado)
e substituir as fixtures por HTML real, é esperado que os seletores em
providers/snd.py precisem de ajuste — e esses mesmos testes vão apontar
exatamente onde.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from debenture_search.models import DebentureRef, Situacao
from debenture_search.providers.snd import (
    SndParsingError,
    _parse_estoque_html,
    _parse_precos_html,
    _parse_search_results_html,
)

FIXTURES = Path(__file__).parent / "fixtures"
REF = DebentureRef(isin="BRTEPADBS001", codigo_ativo="TEPA23", nome_emissor="Tegma")


def test_parse_search_results() -> None:
    html = (FIXTURES / "snd_busca_sample.html").read_text()
    refs = _parse_search_results_html(html)
    assert refs == [
        DebentureRef(isin="BRTEPADBS001", codigo_ativo="TEPA23", nome_emissor="Tegma Gestão Logística S.A.")
    ]


def test_parse_search_results_html_inesperado_levanta_erro_claro() -> None:
    with pytest.raises(SndParsingError):
        _parse_search_results_html("<html><body>página de erro do SND</body></html>")


def test_parse_estoque() -> None:
    html = (FIXTURES / "snd_estoque_sample.html").read_text()
    debenture = _parse_estoque_html(html, REF)
    assert debenture.situacao.valor == Situacao.ATIVA
    assert debenture.quantidade_mercado.valor == "50.000"


def test_parse_precos() -> None:
    html = (FIXTURES / "snd_precos_sample.html").read_text()
    snapshots = _parse_precos_html(html, REF)
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap.pu_minimo.valor == 985.32
    assert snap.pu_medio.valor == 991.10
    assert snap.pu_maximo.valor == 997.00
    assert snap.quantidade_negociada.valor == 1250
    assert snap.numero_negocios.valor == 18
