"""Testa o parsing do provider SND contra HTML REAL, capturado via HAR pelo
usuário em 25/08/2026 (ativo BODY12, emissor A Bodytech Participações S.A.).

Diferente da primeira versão deste arquivo, as fixtures aqui NÃO são
sintéticas — vieram do tráfego de rede real do usuário. Uma ressalva: o
processo de exportação HAR do navegador corrompeu alguns caracteres
acentuados (viram "ï¿½"), então alguns campos com acento no meio da palavra
(ex.: número de série) podem não bater perfeitamente aqui mesmo que o
parsing funcione corretamente contra a página real (que chega com bytes
corretos direto do servidor, sem passar pelo pipeline de exportação do
navegador).
"""

from __future__ import annotations

from pathlib import Path

from debenture_search.models import DebentureRef
from debenture_search.providers.snd import (
    _caracteristicas_encontrou_ativo,
    _extrair_cnpj_do_canonical,
    _parse_ativo_options,
    _parse_caracteristicas_html,
    _parse_emissor_options,
    _parse_precos_html,
    _pad_ativo,
)

FIXTURES = Path(__file__).parent / "fixtures"
REF = DebentureRef(isin="BRBODYDBS000", codigo_ativo="BODY12", nome_emissor="A Bodytech Participações S.A.")


def test_parse_emissor_options_encontra_bodytech() -> None:
    html = (FIXTURES / "snd_estoqueporativo_f.html").read_text(encoding="utf-8")
    opcoes = _parse_emissor_options(html)
    assert len(opcoes) > 1000  # lista real tem ~1466 emissores
    assert ("07737623000190", "A BODYTECH PARTICIPACOES S.A.") in opcoes


def test_parse_ativo_options_para_bodytech() -> None:
    html = (FIXTURES / "snd_estoqueporativo_f_emissor.html").read_text(encoding="utf-8")
    ativos = _parse_ativo_options(html)
    assert "BODY12" in [a.strip() for a in ativos]
    assert "BODY13" in [a.strip() for a in ativos]


def test_caracteristicas_encontrou_ativo() -> None:
    html = (FIXTURES / "snd_caracteristicas_d.html").read_text(encoding="utf-8")
    assert _caracteristicas_encontrou_ativo(html) is True


def test_caracteristicas_encontrou_ativo_falso_para_html_generico() -> None:
    assert _caracteristicas_encontrou_ativo("<html><body>página qualquer</body></html>") is False


def test_extrair_cnpj_do_canonical() -> None:
    html = (FIXTURES / "snd_caracteristicas_d.html").read_text(encoding="utf-8")
    assert _extrair_cnpj_do_canonical(html) == "07737623000190"


def test_parse_caracteristicas_html_campos_principais() -> None:
    html = (FIXTURES / "snd_caracteristicas_d.html").read_text(encoding="utf-8")
    deb = _parse_caracteristicas_html(html, codigo_ativo="BODY12")

    assert deb.isin.valor == "BRBODYDBS000"
    assert deb.codigo_ativo.valor == "BODY12"
    assert "BODYTECH" in deb.emissor_nome.valor
    assert deb.indexador.valor == "DI"
    assert deb.taxa.valor == "DI + 4,3500%"
    assert deb.data_emissao.valor.isoformat() == "2013-05-15"
    assert deb.data_vencimento.valor.isoformat() == "2031-12-22"
    assert deb.especie.valor is not None and "Quirograf" in deb.especie.valor
    assert deb.classe.valor == "Simples"
    assert deb.quantidade_emitida.valor == "19.000"
    assert deb.quantidade_mercado.valor == "6.000"
    assert deb.valor_nominal_unitario.valor == "5.215,572539"
    assert deb.situacao.valor == "Registrado"
    # Rating vazio nesta amostra (campo existe na página, mas sem valor
    # preenchido para esta debênture específica) — indisponível é o
    # resultado honesto, não None por falha de parsing.
    assert deb.rating.valor is None
    for campo in (
        deb.isin, deb.codigo_ativo, deb.emissor_nome, deb.indexador, deb.taxa,
        deb.data_emissao, deb.data_vencimento, deb.especie, deb.classe,
        deb.quantidade_emitida, deb.quantidade_mercado, deb.valor_nominal_unitario,
        deb.situacao,
    ):
        assert campo.fonte == "SND"


def test_parse_precos_html() -> None:
    html = (FIXTURES / "snd_precosdenegociacao_r.html").read_text(encoding="utf-8")
    snapshots = _parse_precos_html(html, REF)

    assert len(snapshots) >= 30  # histórico real tem 32 negociações
    mais_recente = snapshots[0]
    assert mais_recente.periodo_referencia == "07/06/2024"
    assert mais_recente.pu_minimo.valor == 4466.874270
    assert mais_recente.pu_medio.valor == 4466.874270
    assert mais_recente.pu_maximo.valor == 4466.874270
    assert mais_recente.quantidade_negociada.valor == 9334
    assert mais_recente.numero_negocios.valor == 2
    assert mais_recente.pu_minimo.fonte == "SND"


def test_parse_precos_html_sem_isin_na_pagina_retorna_vazio() -> None:
    assert _parse_precos_html("<html><body>sem tabela</body></html>", REF) == []


def test_pad_ativo() -> None:
    assert _pad_ativo("BODY12") == "BODY12    "
    assert len(_pad_ativo("BODY12")) == 10
