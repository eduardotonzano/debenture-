"""Monta o DebentureAggregator com os providers concretos disponíveis.

Ponto único de composição — CLI e API (Fase 2) chamam `build_aggregator()`
em vez de instanciar providers na mão, então adicionar/trocar uma fonte
(ex.: ligar o AnbimaAPIProvider quando a Fase 3 chegar) muda só este
arquivo.
"""

from __future__ import annotations

from debenture_search.cache import SqliteCache
from debenture_search.config import CACHE_DB_PATH, MANUAL_INPUT_DB_PATH
from debenture_search.aggregator import DebentureAggregator
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.providers.snd import SndScraperProvider


def build_aggregator() -> DebentureAggregator:
    cache = SqliteCache(CACHE_DB_PATH)
    snd = SndScraperProvider(cache=cache)
    manual = ManualInputProvider(MANUAL_INPUT_DB_PATH)

    return DebentureAggregator(
        search_providers=[snd],
        # Ordem = precedência: SND primeiro, Manual por último (sempre vence
        # quando presente). ANBIMA entra aqui na Fase 3, entre os dois.
        characteristics_providers=[snd, manual],
        market_data_providers=[snd],
    )
