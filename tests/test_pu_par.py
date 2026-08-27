"""Testes do motor de cálculo do PU Par (fórmula ANBIMA, ver pu_par.py).

Não dependem de rede — os valores de Taxa DI usados aqui são sintéticos,
só para verificar que a fórmula está implementada corretamente (conferida
por invariantes matemáticos e por um cálculo independente linha a linha,
não só reproduzindo a mesma conta do código sob teste).
"""

from __future__ import annotations

from datetime import date

import pytest

from debenture_search.models import TipoEvento
from debenture_search.pu_par import (
    calcular_pu_par,
    calcular_serie_pu_par,
    fator_di_diario,
    fator_juros_percentual,
    fator_juros_spread,
    parse_spread_di_aa,
)


def test_fator_di_diario_taxa_zero_eh_neutro():
    assert fator_di_diario(0.0) == pytest.approx(1.0)


def test_fator_di_diario_bate_com_calculo_independente():
    taxa = 13.65
    esperado = (1 + taxa / 100) ** (1 / 252)
    assert fator_di_diario(taxa) == pytest.approx(esperado)


def test_fator_juros_spread_sem_dias_e_fator_neutro_mais_spread():
    # Nenhum dia útil decorrido (produtório vazio = 1) — só o spread aplica.
    fator = fator_juros_spread([], spread_pct_aa=4.35)
    assert fator == pytest.approx(1.0435)


def test_fator_juros_spread_acumula_produto_diario():
    taxas = [13.65, 13.70, 13.68]
    fator = fator_juros_spread(taxas, spread_pct_aa=0.0)
    # Cálculo independente, dia a dia, sem reusar fator_di_diario.
    esperado = 1.0
    for t in taxas:
        esperado *= (1 + t / 100) ** (1 / 252)
    assert fator == pytest.approx(esperado)


def test_fator_juros_percentual_100_por_cento_do_di_equivale_a_di_puro():
    # 100% do DI é matematicamente idêntico a "DI + 0" — cada fator diário
    # (fator - 1) * 1.0 + 1 == fator.
    taxas = [13.65, 13.70, 13.68, 13.50]
    assert fator_juros_percentual(taxas, 100.0) == pytest.approx(
        fator_juros_spread(taxas, spread_pct_aa=0.0)
    )


def test_fator_juros_percentual_reduz_a_fracao_do_spread_do_di():
    taxas = [13.65, 13.70]
    fator_120 = fator_juros_percentual(taxas, 120.0)
    fator_100 = fator_juros_percentual(taxas, 100.0)
    # 120% do DI acumula mais juro que 100% do DI (taxa positiva).
    assert fator_120 > fator_100


def test_calcular_pu_par_spread():
    taxas = [13.65, 13.70]
    vna = 1000.0
    esperado = vna * fator_juros_spread(taxas, spread_pct_aa=4.35)
    assert calcular_pu_par(vna, taxas, spread_pct_aa=4.35) == pytest.approx(esperado)


def test_calcular_pu_par_percentual():
    taxas = [13.65, 13.70]
    vna = 1000.0
    esperado = vna * fator_juros_percentual(taxas, 108.0)
    assert calcular_pu_par(vna, taxas, percentual_di=108.0) == pytest.approx(esperado)


def test_calcular_pu_par_sem_dias_uteis_e_so_vna_com_spread():
    assert calcular_pu_par(1000.0, [], spread_pct_aa=4.35) == pytest.approx(1043.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"spread_pct_aa": 4.35, "percentual_di": 100.0},
    ],
)
def test_calcular_pu_par_exige_exatamente_um_dos_dois_parametros(kwargs):
    with pytest.raises(ValueError):
        calcular_pu_par(1000.0, [13.65], **kwargs)


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("DI + 4,3500%", 4.35),
        ("DI+4,35%", 4.35),
        ("di + 0,50%", 0.50),
        ("DI + 1.234,56%", 1234.56),
    ],
)
def test_parse_spread_di_aa_formato_reconhecido(texto, esperado):
    assert parse_spread_di_aa(texto) == pytest.approx(esperado)


@pytest.mark.parametrize(
    "texto",
    [
        None,
        "",
        "120% do DI",
        "IPCA + 6,00%",
        "8,50%",
    ],
)
def test_parse_spread_di_aa_formato_nao_reconhecido_e_none(texto):
    assert parse_spread_di_aa(texto) is None


def _taxas_dict(dias: list[int], taxa: float = 13.0) -> dict[date, float]:
    return {date(2020, 1, dia): taxa for dia in dias}


def test_calcular_serie_pu_par_sem_eventos_acumula_desde_a_emissao():
    taxas = _taxas_dict([2, 3, 4, 5])
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=[],
        taxas_di_por_dia=taxas,
        datas_referencia=[date(2020, 1, 5)],
        spread_pct_aa=4.0,
    )
    esperado = calcular_pu_par(10_000.0, [13.0, 13.0, 13.0, 13.0], spread_pct_aa=4.0)
    assert serie[date(2020, 1, 5)] == pytest.approx(esperado)


def test_calcular_serie_pu_par_reinicia_acumulo_no_ultimo_evento_de_juros():
    taxas = _taxas_dict(range(2, 11))
    eventos = [(date(2020, 1, 5), TipoEvento.JUROS, 50.0)]
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=eventos,
        taxas_di_por_dia=taxas,
        datas_referencia=[date(2020, 1, 8)],
        spread_pct_aa=4.0,
    )
    # Só os dias ESTRITAMENTE depois do último Juros (05) até 08, inclusive.
    esperado = calcular_pu_par(10_000.0, [13.0, 13.0, 13.0], spread_pct_aa=4.0)
    assert serie[date(2020, 1, 8)] == pytest.approx(esperado)


def test_calcular_serie_pu_par_amortizacao_reduz_vna():
    taxas = _taxas_dict(range(2, 11))
    eventos = [(date(2020, 1, 5), TipoEvento.AMORTIZACAO, 500.0)]
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=eventos,
        taxas_di_por_dia=taxas,
        datas_referencia=[date(2020, 1, 10)],
        spread_pct_aa=4.0,
    )
    taxas_periodo = [13.0] * 9  # dias 2..10, nenhum evento de Juros ainda
    esperado = calcular_pu_par(9_500.0, taxas_periodo, spread_pct_aa=4.0)
    assert serie[date(2020, 1, 10)] == pytest.approx(esperado)


def test_calcular_serie_pu_par_amortizacao_futura_nao_conta():
    taxas = _taxas_dict(range(2, 6))
    eventos = [(date(2020, 1, 20), TipoEvento.AMORTIZACAO, 500.0)]
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=eventos,
        taxas_di_por_dia=taxas,
        datas_referencia=[date(2020, 1, 5)],
        spread_pct_aa=4.0,
    )
    esperado = calcular_pu_par(10_000.0, [13.0] * 4, spread_pct_aa=4.0)
    assert serie[date(2020, 1, 5)] == pytest.approx(esperado)


def test_calcular_serie_pu_par_sem_dado_de_di_no_periodo_fica_indisponivel():
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=[],
        taxas_di_por_dia={},
        datas_referencia=[date(2020, 1, 10)],
        spread_pct_aa=4.0,
    )
    assert serie[date(2020, 1, 10)] is None


def test_calcular_serie_pu_par_data_referencia_igual_ao_inicio_do_acumulo():
    # Zero dias úteis decorridos é uma resposta válida (fator neutro + o
    # spread), não um "buraco de dado" — não deve virar None.
    eventos = [(date(2020, 1, 5), TipoEvento.JUROS, 50.0)]
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=eventos,
        taxas_di_por_dia={},
        datas_referencia=[date(2020, 1, 5)],
        spread_pct_aa=4.0,
    )
    assert serie[date(2020, 1, 5)] == pytest.approx(10_400.0)


def test_calcular_serie_pu_par_percentual_di():
    taxas = _taxas_dict([2, 3, 4, 5])
    serie = calcular_serie_pu_par(
        vne=10_000.0,
        data_emissao=date(2020, 1, 1),
        eventos=[],
        taxas_di_por_dia=taxas,
        datas_referencia=[date(2020, 1, 5)],
        percentual_di=108.0,
    )
    esperado = calcular_pu_par(10_000.0, [13.0, 13.0, 13.0, 13.0], percentual_di=108.0)
    assert serie[date(2020, 1, 5)] == pytest.approx(esperado)
