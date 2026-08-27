"""Monta o DebentureAggregator com os providers concretos disponíveis.

Ponto único de composição — CLI e API (Fase 2) chamam `build_aggregator()`
em vez de instanciar providers na mão, então adicionar/trocar uma fonte
muda só este arquivo.
"""

from __future__ import annotations

from debenture_search.cache import SqliteCache
from debenture_search.config import (
    ANBIMA_AMBIENTE,
    ANBIMA_CLIENT_ID,
    ANBIMA_CLIENT_SECRET,
    CACHE_DB_PATH,
    DATA_DIR,
    MANUAL_INPUT_DB_PATH,
)
from debenture_search.aggregator import DebentureAggregator
from debenture_search.providers.anbima_api import AnbimaAPIProvider
from debenture_search.providers.bcb_di import BancoCentralDiProvider
from debenture_search.providers.cvm import CvmDocumentsProvider
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.providers.snd import SndScraperProvider
from debenture_search.pu_par_calculator import PuParCalculator


def build_aggregator() -> DebentureAggregator:
    cache = SqliteCache(CACHE_DB_PATH)
    snd = SndScraperProvider(cache=cache)
    anbima_api = AnbimaAPIProvider(
        client_id=ANBIMA_CLIENT_ID,
        client_secret=ANBIMA_CLIENT_SECRET,
        ambiente=ANBIMA_AMBIENTE,
    )
    manual = ManualInputProvider(MANUAL_INPUT_DB_PATH)
    cvm = CvmDocumentsProvider(cache_dir=DATA_DIR / "cvm_ipe")
    # API pública do BCB (sem credencial) — fonte da Taxa DI histórica
    # exigida pelo cálculo de PU Par (ver pu_par.py/pu_par_calculator.py).
    pu_par_calculator = PuParCalculator(bcb=BancoCentralDiProvider())

    return DebentureAggregator(
        search_providers=[snd],
        # Manual sempre por último — é o override do analista, sempre
        # vence quando presente.
        characteristics_providers=[snd, manual],
        # ANBIMA API entra aqui, não em characteristics_providers: o único
        # endpoint de Debêntures confirmado (mercado-secundario) é preço,
        # não cadastro. Sem ANBIMA_CLIENT_ID/SECRET, is_available()=False e
        # o aggregator a ignora automaticamente.
        market_data_providers=[snd, anbima_api],
        events_providers=[snd],
        # CVM precisa do CNPJ que o SND resolve em characteristics_providers
        # (roda antes) — sem CNPJ disponível, simplesmente não retorna nada.
        documents_providers=[cvm],
        pu_par_calculator=pu_par_calculator,
    )
