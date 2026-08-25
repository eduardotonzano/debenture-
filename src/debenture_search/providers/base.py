"""Interfaces de provider de dados de debêntures.

Segregadas por capacidade em vez de uma interface monolítica: nenhuma fonte
cobre tudo (SND não tem mais características completas, ANBIMA exige
credencial paga, CVM só cobre documentos de companhias abertas), então cada
provider implementa apenas os Protocols que a fonte realmente sustenta.

Trocar uma fonte por outra (ex.: SND scraping -> API paga) é implementar os
mesmos Protocols numa classe nova e registrá-la no DebentureAggregator — o
resto do sistema (modelo, merge, UI) não muda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Generic, Protocol, TypeVar, runtime_checkable

from debenture_search.models import (
    Debenture,
    DebentureRef,
    Document,
    Event,
    MarketPriceSnapshot,
    SearchQuery,
)

T = TypeVar("T")


@dataclass
class ProviderResult(Generic[T]):
    """Embrulha o retorno de um provider com proveniência e status.

    Erro parcial de uma fonte nunca deve derrubar a ficha inteira — o
    aggregator trata `sucesso=False` como "esta fonte não contribuiu com
    este campo/seção agora", não como exceção.
    """

    provider: str
    valor: T | None
    sucesso: bool
    coletado_em: datetime = field(default_factory=datetime.utcnow)
    erro: str | None = None

    @classmethod
    def ok(cls, provider: str, valor: T) -> "ProviderResult[T]":
        return cls(provider=provider, valor=valor, sucesso=True)

    @classmethod
    def falha(cls, provider: str, erro: str) -> "ProviderResult[T]":
        return cls(provider=provider, valor=None, sucesso=False, erro=erro)


@runtime_checkable
class Provider(Protocol):
    """Todo provider tem nome e pode se declarar indisponível (ex.: falta de
    credencial) sem que isso derrube o resto do sistema."""

    name: str

    def is_available(self) -> bool: ...


@runtime_checkable
class SearchProvider(Provider, Protocol):
    def search(self, query: SearchQuery) -> list[DebentureRef]:
        """Resolve a busca do usuário em uma ou mais referências de série.

        Mais de uma ref para o mesmo emissor sinaliza ao aggregator que a
        UI precisa desambiguar antes de montar a ficha.
        """
        ...


@runtime_checkable
class CharacteristicsProvider(Provider, Protocol):
    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]: ...


@runtime_checkable
class MarketDataProvider(Provider, Protocol):
    def fetch_market_data(
        self, ref: DebentureRef
    ) -> ProviderResult[list[MarketPriceSnapshot]]: ...


@runtime_checkable
class EventsProvider(Provider, Protocol):
    def fetch_events(self, ref: DebentureRef) -> ProviderResult[list[Event]]: ...


@runtime_checkable
class DocumentsProvider(Provider, Protocol):
    def fetch_documents(self, ref: DebentureRef) -> ProviderResult[list[Document]]: ...
