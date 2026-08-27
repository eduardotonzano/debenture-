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

import pytest

from debenture_search.models import DebentureRef, TipoEvento
from debenture_search.providers.snd import (
    SndParsingError,
    _caracteristicas_encontrou_ativo,
    _extrair_cnpj_do_canonical,
    _mapear_tipo_evento_pagamento,
    _parse_ativo_options,
    _parse_caracteristicas_html,
    _parse_emissor_options,
    _parse_inadimplencias_html,
    _parse_precos_html,
    _parse_pu_de_eventos_html,
    _parse_registros_excluidos_html,
    _parse_repactuacoes_html,
    _parse_vencimentos_antecipados_html,
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
    # VNE (valor nominal na data de emissão, constante) — separado do
    # nominal atualizado acima; é o insumo do cálculo de PU Par (pu_par.py).
    assert deb.valor_nominal_emissao.valor == "10.000,000000"
    assert deb.situacao.valor == "Registrado"
    # Rating vazio nesta amostra (campo existe na página, mas sem valor
    # preenchido para esta debênture específica) — indisponível é o
    # resultado honesto, não None por falha de parsing.
    assert deb.rating.valor is None

    # Como a emissão foi realizada + agentes contratados (pedido explícito
    # do usuário: "banco coordenador", "como foi realizada a emissão").
    assert deb.forma.valor == "Escritural"
    assert deb.registro_cvm_emissao.valor == "DISPENSA ICVM 476/09 em 10/06/2013"
    assert deb.ato_societario.valor == "AGE em 12/03/2013 e RCA em 30/04/2013"
    assert deb.inicio_distribuicao.valor.isoformat() == "2013-06-11"
    assert deb.banco_mandatario.valor == "BANCO BRADESCO S/A"
    assert deb.agente_fiduciario.valor == "GDC PART.SERV.FIDUCIARIOS DTVM LTDA"
    assert deb.instituicao_depositaria.valor == "BANCO BRADESCO S/A"
    assert deb.coordenador_lider.valor == "BCO BTG PACTUAL S/A"

    for campo in (
        deb.isin, deb.codigo_ativo, deb.emissor_nome, deb.indexador, deb.taxa,
        deb.data_emissao, deb.data_vencimento, deb.especie, deb.classe,
        deb.quantidade_emitida, deb.quantidade_mercado, deb.valor_nominal_unitario,
        deb.valor_nominal_emissao,
        deb.situacao, deb.forma, deb.registro_cvm_emissao, deb.ato_societario,
        deb.inicio_distribuicao, deb.banco_mandatario, deb.agente_fiduciario,
        deb.instituicao_depositaria, deb.coordenador_lider,
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


def test_parse_registros_excluidos_html() -> None:
    """Lista global (todos os emissores) de registros excluídos — o sinal
    de 'problema com a debênture' mais direto que o SND expõe: motivo de
    exclusão às vezes indica processo na CVM, e o nome do emissor às vezes
    já vem anotado com 'EM RECUPERAÇÃO JUDICIAL'."""
    html = (FIXTURES / "snd_registrosexcluidos_r.html").read_text(encoding="utf-8")
    registros = _parse_registros_excluidos_html(html)

    assert len(registros) == 1424  # histórico real, fev/2021 a ago/2026

    com_motivo = [r for r in registros if r[3]]
    assert len(com_motivo) == 4  # a maioria vem sem motivo preenchido — dado real, não bug

    data, codigo_ativo, emissor, motivo = next(r for r in registros if r[1] == "QGIM12")
    assert data.isoformat() == "2023-07-04"
    assert motivo == "VENCIMENTO"
    assert "QUEIROZ GALVAO" in emissor

    _, codigo_ativo2, emissor2, motivo2 = next(r for r in registros if r[1] == "ODBE13")
    assert "RECUPERACAO JUDICIAL" in emissor2
    assert motivo2 == "VENCIMENTO"

    # ativo ativo (sem trocadilho) não deve aparecer na lista de excluídos
    assert not any(r[1] == "BODY12" for r in registros)


def test_parse_repactuacoes_html() -> None:
    html = (FIXTURES / "snd_repactuacoes_r.html").read_text(encoding="utf-8")
    registros = _parse_repactuacoes_html(html)

    assert len(registros) == 54  # histórico real, 1995-2010

    data, codigo_ativo, emissor, deliberacao = registros[0]
    assert data.isoformat() == "1995-11-01"
    assert codigo_ativo == "BFBL34"
    assert emissor == "DIBENS LEASING S/A ARRENDAMENTO MERCANTIL"
    assert deliberacao == "RCA - 16/10/1995"

    # linhas mais recentes do fixture não têm deliberação preenchida —
    # dado real, o parser não deve inventar nem quebrar
    sem_deliberacao = [r for r in registros if r[3] is None]
    assert len(sem_deliberacao) > 0

    # nome de emissor com " - " embutido não deve confundir o split do
    # código de ativo (extraído do href, não do texto visível)
    mend17 = next(r for r in registros if r[1] == "MEND17")
    assert mend17[2] == "MENDES JUNIOR ENGENHARIA S/A"


def test_parse_vencimentos_antecipados_html_sem_resultado() -> None:
    """Única situação real confirmada até agora: nenhum vencimento
    antecipado declarado no período consultado (2020-2026)."""
    html = (FIXTURES / "snd_vencimentosantecipados_r.html").read_text(encoding="utf-8")
    assert _parse_vencimentos_antecipados_html(html) == []


def test_parse_vencimentos_antecipados_html_conteudo_desconhecido_falha_alto() -> None:
    """Nunca vimos uma página de vencimentos antecipados COM resultado —
    então, em vez de arriscar um parsing de linha nunca verificado, a
    função levanta erro claro pra qualquer conteúdo que não seja o caso
    vazio conhecido."""
    with pytest.raises(SndParsingError):
        _parse_vencimentos_antecipados_html("<html><body>algo diferente</body></html>")


def test_parse_inadimplencias_html_sem_resultado() -> None:
    """Única situação real confirmada até agora: nenhuma inadimplência
    corrente no momento da captura."""
    html = (FIXTURES / "snd_inadimplencias_r.html").read_text(encoding="utf-8")
    assert _parse_inadimplencias_html(html) == []


def test_parse_inadimplencias_html_conteudo_desconhecido_falha_alto() -> None:
    with pytest.raises(SndParsingError):
        _parse_inadimplencias_html("<html><body>algo diferente</body></html>")


def test_mapear_tipo_evento_pagamento_juros() -> None:
    assert _mapear_tipo_evento_pagamento("Juros") == TipoEvento.JUROS


def test_mapear_tipo_evento_pagamento_amortizacao_com_acento() -> None:
    assert _mapear_tipo_evento_pagamento("Amortização") == TipoEvento.AMORTIZACAO


def test_mapear_tipo_evento_pagamento_amortizacao_com_acento_corrompido() -> None:
    # Mesma corrupção real do HAR exportado pelo navegador (ver docstring do
    # módulo de testes) — o prefixo sem acento precisa sobreviver a isso.
    assert _mapear_tipo_evento_pagamento("Amortiza��o") == TipoEvento.AMORTIZACAO


def test_mapear_tipo_evento_pagamento_desconhecido_e_none() -> None:
    assert _mapear_tipo_evento_pagamento("Repactuação") is None
    assert _mapear_tipo_evento_pagamento("") is None


def test_parse_pu_de_eventos_html() -> None:
    """Contra HTML real (tests/fixtures/snd_pudeeventos_r.html, ativo
    BODY12, capturado via HAR pelo usuário) — 128 linhas de dados no
    fixture real."""
    html = (FIXTURES / "snd_pudeeventos_r.html").read_text(encoding="utf-8")
    linhas = _parse_pu_de_eventos_html(html)

    assert len(linhas) == 128
    mais_recente = linhas[0]
    data, ativo, tipo, valor, situacao, liquidacao = mais_recente
    assert data.isoformat() == "2026-08-24"
    assert ativo == "BODY12"
    assert tipo == TipoEvento.AMORTIZACAO
    assert valor == pytest.approx(33.333333)
    assert situacao == "Registrado"
    assert liquidacao == "LIQUIDADO"

    segunda = linhas[1]
    assert segunda[2] == TipoEvento.JUROS
    assert segunda[3] == pytest.approx(83.915585)
