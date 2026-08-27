"""Testa o PuParCalculator (passo de pós-processamento que preenche
`pu_par` usando o motor de cálculo real) — sem rede, com um
BancoCentralDiProvider falso."""

from __future__ import annotations

from datetime import date

import pytest

from debenture_search.models import (
    Debenture,
    DebentureRef,
    Event,
    MarketPriceSnapshot,
    SourcedValue,
    TipoEvento,
)
from debenture_search.pu_par import calcular_pu_par
from debenture_search.pu_par_calculator import FONTE_CALCULADO, PuParCalculator

REF = DebentureRef(isin=None, codigo_ativo="BODY12", nome_emissor="A Bodytech")


class _FakeBcb:
    def __init__(self, serie: dict[date, float]) -> None:
        self._serie = serie
        self.chamadas: list[tuple[date, date]] = []

    def fetch_serie(self, data_inicial: date, data_final: date):
        self.chamadas.append((data_inicial, data_final))
        return [(d, v) for d, v in self._serie.items() if data_inicial <= d <= data_final]


class _FakeBcbFalha:
    def fetch_serie(self, data_inicial: date, data_final: date):
        raise RuntimeError("bcb.gov.br indisponível")


def _debenture_base(**overrides) -> Debenture:
    deb = Debenture(
        valor_nominal_emissao=SourcedValue("10.000,000000", fonte="SND"),
        data_emissao=SourcedValue(date(2020, 1, 1), fonte="SND"),
        taxa=SourcedValue("DI + 4,3500%", fonte="SND"),
    )
    for campo, valor in overrides.items():
        setattr(deb, campo, valor)
    return deb


def _serie_di(dias: list[int], taxa: float = 13.0) -> dict[date, float]:
    return {date(2020, 1, dia): taxa for dia in dias}


def test_preencher_calcula_pu_par_quando_todos_os_insumos_existem():
    deb = _debenture_base()
    deb.eventos = [
        Event(debenture_ref=REF, tipo=TipoEvento.JUROS, data_prevista=date(2020, 1, 3), valor=SourcedValue(50.0)),
    ]
    deb.precos = [
        MarketPriceSnapshot(
            debenture_ref=REF,
            periodo_referencia="10/01/2020",
            pu_medio=SourcedValue(1000.0, fonte="SND"),
        )
    ]
    bcb = _FakeBcb(_serie_di([4, 5, 6, 7, 8, 9, 10]))
    calculadora = PuParCalculator(bcb=bcb)

    calculadora.preencher(deb)

    esperado = calcular_pu_par(10_000.0, [13.0] * 7, spread_pct_aa=4.35)
    assert deb.precos[0].pu_par.valor == pytest.approx(esperado)
    assert deb.precos[0].pu_par.fonte == FONTE_CALCULADO


def test_preencher_nao_sobrescreve_pu_par_ja_existente():
    deb = _debenture_base()
    deb.precos = [
        MarketPriceSnapshot(
            debenture_ref=REF,
            periodo_referencia="10/01/2020",
            pu_medio=SourcedValue(1000.0, fonte="ANBIMA"),
            pu_par=SourcedValue(987.65, fonte="ANBIMA Feed Preços e Índices Debêntures+"),
        )
    ]
    bcb = _FakeBcb(_serie_di(range(2, 11)))
    calculadora = PuParCalculator(bcb=bcb)

    calculadora.preencher(deb)

    assert deb.precos[0].pu_par.valor == 987.65
    assert deb.precos[0].pu_par.fonte == "ANBIMA Feed Preços e Índices Debêntures+"
    assert bcb.chamadas == []  # nem chegou a consultar o BCB


def test_preencher_sem_vne_fica_indisponivel():
    deb = _debenture_base(valor_nominal_emissao=SourcedValue(None))
    deb.precos = [
        MarketPriceSnapshot(debenture_ref=REF, periodo_referencia="10/01/2020", pu_medio=SourcedValue(1000.0))
    ]
    calculadora = PuParCalculator(bcb=_FakeBcb({}))

    calculadora.preencher(deb)

    assert deb.precos[0].pu_par.disponivel is False


def test_preencher_indexador_nao_di_fica_indisponivel():
    """IPCA/IGP-M/prefixado usam outra fórmula (ver pu_par.py) — nunca
    aplicamos a fórmula de DI a um indexador diferente."""
    deb = _debenture_base(taxa=SourcedValue("IPCA + 6,00%", fonte="SND"))
    deb.precos = [
        MarketPriceSnapshot(debenture_ref=REF, periodo_referencia="10/01/2020", pu_medio=SourcedValue(1000.0))
    ]
    calculadora = PuParCalculator(bcb=_FakeBcb({}))

    calculadora.preencher(deb)

    assert deb.precos[0].pu_par.disponivel is False


def test_preencher_falha_do_bcb_fica_indisponivel_sem_quebrar():
    deb = _debenture_base()
    deb.precos = [
        MarketPriceSnapshot(debenture_ref=REF, periodo_referencia="10/01/2020", pu_medio=SourcedValue(1000.0))
    ]
    calculadora = PuParCalculator(bcb=_FakeBcbFalha())

    calculadora.preencher(deb)  # não deve levantar

    assert deb.precos[0].pu_par.disponivel is False


def test_preencher_sem_precos_nao_chama_bcb():
    deb = _debenture_base()
    bcb = _FakeBcb({})
    calculadora = PuParCalculator(bcb=bcb)

    calculadora.preencher(deb)

    assert bcb.chamadas == []


def test_preencher_ignora_snapshot_com_data_nao_parseavel():
    deb = _debenture_base()
    deb.precos = [
        MarketPriceSnapshot(debenture_ref=REF, periodo_referencia=None, pu_medio=SourcedValue(1000.0)),
    ]
    calculadora = PuParCalculator(bcb=_FakeBcb({}))

    calculadora.preencher(deb)

    assert deb.precos[0].pu_par.disponivel is False
