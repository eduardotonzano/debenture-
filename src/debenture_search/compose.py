"""Monta o DebentureAggregator com os providers concretos disponíveis.

Ponto único de composição — CLI e API (Fase 2) chamam `build_aggregator()`
em vez de instanciar providers na mão, então adicionar/trocar uma fonte
muda só este arquivo.
"""

from __future__ import annotations

from debenture_search.cache import SqliteCache
from debenture_search.config import ANBIMA_API_KEY, CACHE_DB_PATH, MANUAL_INPUT_DB_PATH
from debenture_search.aggregator import DebentureAggregator
from debenture_search.providers.anbima_api import AnbimaAPIProvider
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.providers.snd import SndScraperProvider


def build_aggregator() -> DebentureAggregator:
    cache = SqliteCache(CACHE_DB_PATH)
    snd = SndScraperProvider(cache=cache)
    anbima_api = AnbimaAPIProvider(api_key=ANBIMA_API_KEY)
    manual = ManualInputProvider(MANUAL_INPUT_DB_PATH)

    return DebentureAggregator(
        search_providers=[snd],
        # Ordem = precedência: SND primeiro (base gratuita), ANBIMA API
        # depois (características mais completas, quando houver
        # credencial — fica indisponível e é ignorada sem
        # ANBIMA_API_KEY), Manual por último (sempre vence quando
        # presente, é o override do analista).
        characteristics_providers=[snd, anbima_api, manual],
        market_data_providers=[snd],
        events_providers=[snd],
    )
