"""Orquestra os providers registrados: resolve a busca, dispara o fan-out nas
fontes disponíveis e faz o merge dos resultados respeitando precedência.

Regra central: nenhum provider indisponível (ex.: ANBIMA sem credencial)
ou que falhe numa chamada específica derruba a ficha inteira — o campo
correspondente simplesmente permanece "indisponível", com a fonte que
tentou (e falhou) registrada para quem for depurar.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from debenture_search.models import Debenture, DebentureRef, SearchQuery, SourcedValue
from debenture_search.providers.base import (
    CharacteristicsProvider,
    DocumentsProvider,
    EventsProvider,
    MarketDataProvider,
    SearchProvider,
)

# Campos de Debenture, na ordem em que os providers de características são
# consultados (menor índice = menor precedência, sobrescrito pelos que vêm
# depois). Providers "Manual" devem sempre ser registrados por último.
_CAMPOS_CARACTERISTICAS = [
    f.name
    for f in fields(Debenture)
    if f.name not in {"precos", "eventos", "documentos"}
]


@dataclass
class Ambiguous:
    """Sinaliza que a busca encontrou mais de uma série e a UI precisa
    pedir para o usuário escolher antes de montar a ficha."""

    candidatos: list[DebentureRef]


class DebentureAggregator:
    def __init__(
        self,
        search_providers: list[SearchProvider],
        characteristics_providers: list[CharacteristicsProvider],
        market_data_providers: list[MarketDataProvider] | None = None,
        events_providers: list[EventsProvider] | None = None,
        documents_providers: list[DocumentsProvider] | None = None,
    ) -> None:
        # Precedência = ordem da lista. Documentar isso na chamada de
        # composição (ex.: main.py) é responsabilidade de quem monta o
        # aggregator, não deste construtor.
        self.search_providers = [p for p in search_providers if p.is_available()]
        self.characteristics_providers = [
            p for p in characteristics_providers if p.is_available()
        ]
        self.market_data_providers = [
            p for p in (market_data_providers or []) if p.is_available()
        ]
        self.events_providers = [p for p in (events_providers or []) if p.is_available()]
        self.documents_providers = [p for p in (documents_providers or []) if p.is_available()]

    def resolve(self, query: SearchQuery) -> list[DebentureRef] | Ambiguous:
        candidatos: list[DebentureRef] = []
        vistos: set[tuple[str | None, str | None]] = set()
        for provider in self.search_providers:
            for ref in provider.search(query):
                chave = (ref.isin, ref.codigo_ativo)
                if chave not in vistos:
                    vistos.add(chave)
                    candidatos.append(ref)

        if len(candidatos) > 1:
            return Ambiguous(candidatos)
        return candidatos

    def build_ficha(self, ref: DebentureRef) -> Debenture:
        debenture = Debenture()

        for provider in self.characteristics_providers:
            resultado = provider.fetch_characteristics(ref)
            if not resultado.sucesso or resultado.valor is None:
                continue
            _merge_campos(debenture, resultado.valor)

        for provider in self.market_data_providers:
            resultado = provider.fetch_market_data(ref)
            if resultado.sucesso and resultado.valor:
                debenture.precos.extend(resultado.valor)

        for provider in self.events_providers:
            resultado = provider.fetch_events(ref)
            if resultado.sucesso and resultado.valor:
                debenture.eventos.extend(resultado.valor)

        for provider in self.documents_providers:
            resultado = provider.fetch_documents(ref)
            if resultado.sucesso and resultado.valor:
                debenture.documentos.extend(resultado.valor)

        return debenture

    def search_and_build(self, query: SearchQuery) -> Debenture | Ambiguous:
        resolved = self.resolve(query)
        if isinstance(resolved, Ambiguous):
            return resolved
        if not resolved:
            return Debenture()
        return self.build_ficha(resolved[0])


def _merge_campos(destino: Debenture, origem: Debenture) -> None:
    """Sobrescreve em `destino` todo campo de `origem` que tenha valor
    disponível — providers depois na lista de precedência vencem, mas só
    quando de fato trazem um dado (nunca sobrescrevem com "indisponível")."""
    for campo in _CAMPOS_CARACTERISTICAS:
        valor_origem: SourcedValue = getattr(origem, campo)
        if valor_origem.disponivel:
            setattr(destino, campo, valor_origem)
