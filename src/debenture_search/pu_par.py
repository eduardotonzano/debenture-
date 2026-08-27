"""Motor de cálculo do PU Par de debêntures indexadas ao DI, replicando a
fórmula oficial da ANBIMA — não uma aproximação, não uma estimativa a partir
de outro campo (ver `providers/anbima_api.py` pra por que `pu_par = pu /
percentual_pu_par` foi descartado: são grandezas independentes).

Fonte: ANBIMA, "Metodologias ANBIMA de Precificação" (dez/2023), seção
25.4.1 "Debêntures — Debêntures remuneradas pelo DI", páginas 54-56
(PDF enviado pelo usuário, ver README para o link público):

    PU PAR = VNA × Fator de Juros

    Debênture "DI + spread":
        Fator de Juros = Fator DI × (1 + S/100)

    Debênture "percentual do DI":
        Fator de Juros = ∏ [ (FatorDIdiário_k − 1) × (P/100) + 1 ]

    Fator DI = ∏ FatorDIdiário_k ,  FatorDIdiário_k = (1 + TaxaDI_k/100)^(1/252)

onde `TaxaDI_k` é a Taxa DI-Over divulgada pela B3 (% a.a., base 252) de
cada dia útil `k` entre a data do último evento de pagamento de juros
(inclusive) e a data de referência (exclusive), e VNA é o valor nominal
atualizado da debênture (= VNE — valor nominal de emissão — quando não há
amortização de principal).

Nota sobre o expoente `1/252`: a versão do PDF extraída em texto plano
(`pdftotext -layout`) perde a notação de expoente na seção de debêntures —
a fórmula aparece como `(1 + TaxaDI/100)` sem o `^(1/252)` visível. O mesmo
documento tem, na seção de CRA (página 74), a fórmula equivalente com o
expoente preservado (`(DIk/100 + 1)^(1/252) − 1`), usando a MESMA definição
textual de "Taxa DI" (% a.a., base 252, divulgada diariamente pela B3) da
seção de debêntures. O expoente foi recuperado por essa conferência cruzada
dentro do próprio documento — não por suposição externa — e corresponde à
convenção padrão de capitalização do CDI no mercado brasileiro.
"""

from __future__ import annotations

import re
from datetime import date

from debenture_search.models import TipoEvento

_DIAS_UTEIS_ANO = 252

# Reconhece só o formato aditivo que o SND hoje produz pra indexador DI
# ("DI + 4,3500%", ver providers/snd.py::_parse_caracteristicas_html). O
# SND expõe indexador ("Tipo de Remuneração") e o número ("Taxa de
# Juros/Spread") como campos separados sem indicar se o número é um spread
# aditivo ou um percentual multiplicativo do DI (ex.: "120% do DI") — o
# parser atual sempre monta a string como aditiva. Enquanto essa ambiguidade
# do lado do SND não for resolvida, este parser só reconhece o formato "DI +
# X%" e devolve None pra qualquer outra coisa, em vez de arriscar tratar um
# percentual-do-DI como se fosse spread (ou vice-versa).
_SPREAD_DI_RE = re.compile(r"^DI\s*\+\s*([\d.,]+)\s*%$", re.IGNORECASE)


def parse_spread_di_aa(taxa_texto: str | None) -> float | None:
    """Extrai o spread numérico (% a.a.) do texto `Debenture.taxa` quando
    reconhecidamente uma debênture "DI + spread". Devolve None (nunca um
    palpite) pra qualquer indexador ou formato diferente."""
    if not taxa_texto:
        return None
    m = _SPREAD_DI_RE.match(taxa_texto.strip())
    if not m:
        return None
    bruto = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(bruto)
    except ValueError:
        return None


def fator_di_diario(taxa_di_aa: float) -> float:
    """Fator de capitalização de um único dia útil, a partir da Taxa DI-Over
    anualizada (% a.a., base 252) divulgada pela B3/BCB nesse dia."""
    return (1 + taxa_di_aa / 100) ** (1 / _DIAS_UTEIS_ANO)


def fator_juros_spread(taxas_di_aa: list[float], spread_pct_aa: float) -> float:
    """Fator de Juros de uma debênture "DI + spread" (ex.: 'DI + 4,35%').

    `taxas_di_aa`: uma Taxa DI (% a.a.) por dia útil, na ordem cronológica,
    desde o último pagamento de juros (inclusive) até a data de referência
    (exclusive). `spread_pct_aa`: o spread contratual, já em % a.a.
    """
    fator_di = 1.0
    for taxa in taxas_di_aa:
        fator_di *= fator_di_diario(taxa)
    return fator_di * (1 + spread_pct_aa / 100)


def fator_juros_percentual(taxas_di_aa: list[float], percentual_di: float) -> float:
    """Fator de Juros de uma debênture "percentual do DI" (ex.: '120% do DI').

    `percentual_di` em pontos percentuais (120 representa 120%).
    """
    fator = 1.0
    for taxa in taxas_di_aa:
        fator *= (fator_di_diario(taxa) - 1) * (percentual_di / 100) + 1
    return fator


def calcular_pu_par(
    vna: float,
    taxas_di_aa: list[float],
    *,
    spread_pct_aa: float | None = None,
    percentual_di: float | None = None,
) -> float:
    """PU PAR = VNA × Fator de Juros — exige exatamente um entre
    `spread_pct_aa` (debênture "DI + spread") e `percentual_di` (debênture
    "percentual do DI"), nunca os dois nem nenhum: são contratos distintos
    na escritura, misturar os dois produziria um número sem base real."""
    if (spread_pct_aa is None) == (percentual_di is None):
        raise ValueError(
            "calcular_pu_par exige exatamente um entre spread_pct_aa e percentual_di"
        )
    fator_juros = (
        fator_juros_spread(taxas_di_aa, spread_pct_aa)
        if spread_pct_aa is not None
        else fator_juros_percentual(taxas_di_aa, percentual_di)
    )
    return vna * fator_juros


def calcular_serie_pu_par(
    vne: float,
    data_emissao: date,
    eventos: list[tuple[date, TipoEvento, float | None]],
    taxas_di_por_dia: dict[date, float],
    datas_referencia: list[date],
    *,
    spread_pct_aa: float | None = None,
    percentual_di: float | None = None,
) -> dict[date, float | None]:
    """PU Par histórico pra uma lista de datas de referência, reconstruindo
    VNA e o início de cada período de capitalização a partir do histórico
    real de eventos (ver `providers/snd.py::_fetch_eventos_pagamento`, o
    "PU de Eventos" do SND — dados JÁ PAGOS, não uma projeção).

    Pra cada data de referência:
    - VNA = VNE − soma dos valores de Amortização REALMENTE pagos (o valor
      que o `PU de Evento` do SND já publica, nunca recalculado a partir
      do percentual contratual) em eventos até essa data (inclusive);
    - o início do período de capitalização é a data do último evento de
      Juros até essa data (inclusive), ou `data_emissao` se ainda não
      houve nenhum;
    - a Taxa DI de cada dia útil estritamente entre esse início e a data
      de referência (inclusive) vem de `taxas_di_por_dia` (ver
      `providers/bcb_di.py`).

    Uma data de referência sem NENHUM dado de Taxa DI no período (e que
    precisaria de pelo menos um) fica com valor `None` — nunca acumulada
    com um buraco no meio, que produziria um número plausível mas errado.
    """
    eventos_ordenados = sorted(eventos, key=lambda e: e[0])
    resultado: dict[date, float | None] = {}

    for data_ref in datas_referencia:
        eventos_ate = [e for e in eventos_ordenados if e[0] <= data_ref]
        amortizado = sum(
            valor
            for _data, tipo, valor in eventos_ate
            if tipo is TipoEvento.AMORTIZACAO and valor is not None
        )
        vna = vne - amortizado

        datas_juros_ate = [data for data, tipo, _valor in eventos_ate if tipo is TipoEvento.JUROS]
        inicio_acumulo = max(datas_juros_ate) if datas_juros_ate else data_emissao

        dias_uteis = sorted(d for d in taxas_di_por_dia if inicio_acumulo < d <= data_ref)
        if inicio_acumulo < data_ref and not dias_uteis:
            resultado[data_ref] = None
            continue

        taxas = [taxas_di_por_dia[d] for d in dias_uteis]
        try:
            resultado[data_ref] = calcular_pu_par(
                vna, taxas, spread_pct_aa=spread_pct_aa, percentual_di=percentual_di
            )
        except ValueError:
            resultado[data_ref] = None

    return resultado
