"""Preenche `pu_par` nos `MarketPriceSnapshot` que ainda não têm um (ou
seja, todos exceto os vindos da ANBIMA Feed "Debêntures+" — ver
providers/anbima_api.py), usando o motor de cálculo real (`pu_par.py`) em
vez de deixar indisponível ou estimar a partir do percentual.

Só age quando TODOS os insumos exigidos pela fórmula estão disponíveis —
VNE (`Debenture.valor_nominal_emissao`), data de emissão, um spread "DI +
X%" reconhecível (`parse_spread_di_aa`), e o histórico de eventos de
pagamento (`Debenture.eventos`, populado por
`SndScraperProvider._fetch_eventos_pagamento`). Faltando qualquer um
desses, não calcula nada — os preços ficam com `pu_par` indisponível, o
mesmo resultado honesto de antes desta Fase 5.

Debêntures indexadas a outro indexador (IPCA, IGP-M, prefixado) ou com
contrato "percentual do DI" (que o SND hoje não distingue de "DI +
spread" na extração, ver `parse_spread_di_aa`) NUNCA são calculadas por
este módulo — `parse_spread_di_aa` devolve `None` pra qualquer formato que
não seja reconhecidamente aditivo, e isso já basta pra pular o cálculo.
"""

from __future__ import annotations

from datetime import date, datetime

from debenture_search.models import Debenture, SourcedValue, TipoEvento
from debenture_search.providers.bcb_di import BancoCentralDiProvider
from debenture_search.pu_par import calcular_serie_pu_par, parse_spread_di_aa

FONTE_CALCULADO = "Calculado (fórmula ANBIMA seção 25.4.1 + BCB SGS série 12)"


def _parse_decimal_br(bruto: str | None) -> float | None:
    if not bruto:
        return None
    limpo = bruto.strip().replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _parse_periodo_referencia(bruto: str | None) -> date | None:
    """`periodo_referencia` vem em formatos diferentes por fonte (SND:
    DD/MM/YYYY; ANBIMA: YYYY-MM-DD) — ver mesma necessidade em web.py."""
    if not bruto:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(bruto, formato).date()
        except ValueError:
            continue
    return None


class PuParCalculator:
    """Não é um provider (não implementa nenhum Protocol de
    `providers/base.py`) — é um passo de pós-processamento aplicado depois
    que características, preços e eventos já foram todos mesclados numa
    `Debenture`, porque precisa dos três ao mesmo tempo."""

    def __init__(self, bcb: BancoCentralDiProvider) -> None:
        self._bcb = bcb

    def preencher(self, debenture: Debenture) -> None:
        precos_faltando = [p for p in debenture.precos if not p.pu_par.disponivel]
        if not precos_faltando:
            return

        spread = parse_spread_di_aa(
            debenture.taxa.valor if debenture.taxa.disponivel else None
        )
        if spread is None:
            return

        vne = _parse_decimal_br(
            debenture.valor_nominal_emissao.valor
            if debenture.valor_nominal_emissao.disponivel
            else None
        )
        data_emissao = (
            debenture.data_emissao.valor if debenture.data_emissao.disponivel else None
        )
        if vne is None or data_emissao is None:
            return

        datas_por_snapshot: dict[int, date] = {}
        for i, p in enumerate(precos_faltando):
            data = _parse_periodo_referencia(p.periodo_referencia)
            if data is not None:
                datas_por_snapshot[i] = data
        if not datas_por_snapshot:
            return

        eventos_pagamento = [
            (e.data_prevista, e.tipo, e.valor.valor if isinstance(e.valor.valor, (int, float)) else None)
            for e in debenture.eventos
            if e.tipo in (TipoEvento.JUROS, TipoEvento.AMORTIZACAO) and e.data_prevista is not None
        ]

        datas_referencia = sorted(set(datas_por_snapshot.values()))
        data_inicial = min(data_emissao, datas_referencia[0])
        data_final = datas_referencia[-1]

        try:
            taxas_di_por_dia = dict(self._bcb.fetch_serie(data_inicial, data_final))
        except Exception:  # noqa: BLE001 - falha de fonte externa não derruba a ficha
            return

        serie = calcular_serie_pu_par(
            vne,
            data_emissao,
            eventos_pagamento,
            taxas_di_por_dia,
            datas_referencia,
            spread_pct_aa=spread,
        )

        for i, data in datas_por_snapshot.items():
            valor = serie.get(data)
            if valor is not None:
                precos_faltando[i].pu_par = SourcedValue(valor, fonte=FONTE_CALCULADO)
