"""Testa o merge de precedência do DebentureAggregator com providers fake —
sem rede, sem depender do SND real. É a parte do sistema com maior
confiança neste ambiente (o proxy bloqueia debentures.com.br)."""

from __future__ import annotations

from debenture_search.aggregator import Ambiguous, DebentureAggregator
from debenture_search.models import Debenture, DebentureRef, SearchQuery, SourcedValue
from debenture_search.providers.base import ProviderResult

REF = DebentureRef(isin="BRTEPADBS001", codigo_ativo="TEPA23", nome_emissor="Tegma")


class FakeSearchProvider:
    name = "fake-search"

    def __init__(self, refs: list[DebentureRef]) -> None:
        self._refs = refs

    def is_available(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> list[DebentureRef]:
        return self._refs


class FakeCharacteristicsProvider:
    def __init__(self, name: str, debenture: Debenture, available: bool = True) -> None:
        self.name = name
        self._debenture = debenture
        self._available = available

    def is_available(self) -> bool:
        return self._available

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        return ProviderResult.ok(self.name, self._debenture)


class FailingCharacteristicsProvider:
    name = "failing"

    def is_available(self) -> bool:
        return True

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        return ProviderResult.falha(self.name, "erro simulado de rede")


def test_resolve_sem_ambiguidade() -> None:
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF])],
        characteristics_providers=[],
    )
    resultado = aggregator.resolve(SearchQuery(isin=REF.isin))
    assert resultado == [REF]


def test_resolve_com_ambiguidade() -> None:
    outro = DebentureRef(isin="BRTEPADBS002", codigo_ativo="TEPA24", nome_emissor="Tegma")
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF, outro])],
        characteristics_providers=[],
    )
    resultado = aggregator.resolve(SearchQuery(nome_emissor="Tegma"))
    assert isinstance(resultado, Ambiguous)
    assert resultado.candidatos == [REF, outro]


def test_merge_provider_posterior_sobrescreve_quando_disponivel() -> None:
    snd = Debenture(
        taxa=SourcedValue("CDI + 1.5%", fonte="SND"),
        rating=SourcedValue(None),  # SND não tem rating
    )
    manual = Debenture(
        rating=SourcedValue("AA-", fonte="Manual (Fitch, 03/2026)"),
    )
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF])],
        characteristics_providers=[
            FakeCharacteristicsProvider("SND", snd),
            FakeCharacteristicsProvider("Manual", manual),
        ],
    )
    ficha = aggregator.build_ficha(REF)

    # Manual não tinha taxa -> valor do SND sobrevive (nunca sobrescrito por "indisponível")
    assert ficha.taxa.valor == "CDI + 1.5%"
    assert ficha.taxa.fonte == "SND"
    # Manual tinha rating -> vence por vir depois na lista de precedência
    assert ficha.rating.valor == "AA-"
    assert ficha.rating.fonte == "Manual (Fitch, 03/2026)"


def test_provider_indisponivel_e_ignorado_sem_quebrar() -> None:
    indisponivel = FakeCharacteristicsProvider(
        "ANBIMA", Debenture(taxa=SourcedValue("nao deveria aparecer")), available=False
    )
    snd = FakeCharacteristicsProvider("SND", Debenture(taxa=SourcedValue("CDI + 1.5%")))
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF])],
        characteristics_providers=[indisponivel, snd],
    )
    assert indisponivel not in aggregator.characteristics_providers
    ficha = aggregator.build_ficha(REF)
    assert ficha.taxa.valor == "CDI + 1.5%"


def test_provider_com_falha_nao_derruba_ficha() -> None:
    snd = FakeCharacteristicsProvider("SND", Debenture(taxa=SourcedValue("CDI + 1.5%")))
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF])],
        characteristics_providers=[FailingCharacteristicsProvider(), snd],
    )
    ficha = aggregator.build_ficha(REF)
    assert ficha.taxa.valor == "CDI + 1.5%"


def test_campo_sem_nenhuma_fonte_fica_indisponivel() -> None:
    aggregator = DebentureAggregator(
        search_providers=[FakeSearchProvider([REF])],
        characteristics_providers=[FakeCharacteristicsProvider("SND", Debenture())],
    )
    ficha = aggregator.build_ficha(REF)
    assert ficha.rating.disponivel is False
    assert ficha.rating.valor is None
