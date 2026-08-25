"""Testa o SndScraperProvider ponta a ponta (search -> characteristics ->
market data) sem rede, pré-populando o cache com os HTML reais capturados
via HAR. Isso valida a "fiação" (chaves de cache, parâmetros passados pro
HTTP client) que os testes de parsing puro em test_snd_provider.py não
cobrem — um erro de digitação numa chave de cache ou parâmetro passaria
despercebido lá, mas não aqui.
"""

from __future__ import annotations

from pathlib import Path

from debenture_search.cache import SqliteCache
from debenture_search.models import DebentureRef, SearchQuery
from debenture_search.providers.snd import FONTE, SndScraperProvider

FIXTURES = Path(__file__).parent / "fixtures"


def _seed_cache(cache: SqliteCache) -> None:
    cache.set(
        FONTE, "emissores_lista", "",
        (FIXTURES / "snd_estoqueporativo_f.html").read_text(encoding="utf-8"),
    )
    cache.set(
        FONTE, "ativos_por_emissor", "07737623000190",
        (FIXTURES / "snd_estoqueporativo_f_emissor.html").read_text(encoding="utf-8"),
    )
    for tip_deb in ("publicas",):
        cache.set(
            FONTE, "caracteristicas", f"{tip_deb}:BODY12",
            (FIXTURES / "snd_caracteristicas_d.html").read_text(encoding="utf-8"),
        )
    cache.set(
        FONTE, "precos", "BODY12",
        (FIXTURES / "snd_precosdenegociacao_r.html").read_text(encoding="utf-8"),
    )


def test_search_por_emissor_sem_rede(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    _seed_cache(cache)
    provider = SndScraperProvider(cache=cache)

    refs = provider.search(SearchQuery(nome_emissor="Bodytech"))

    codigos = {r.codigo_ativo.strip() for r in refs}
    assert "BODY12" in codigos
    assert "BODY13" in codigos
    assert all("BODYTECH" in r.nome_emissor.upper() for r in refs)


def test_search_por_codigo_ativo_sem_rede(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    _seed_cache(cache)
    provider = SndScraperProvider(cache=cache)

    refs = provider.search(SearchQuery(codigo_ativo="BODY12"))

    assert len(refs) == 1
    assert refs[0].isin == "BRBODYDBS000"


def test_fetch_characteristics_sem_rede(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    _seed_cache(cache)
    provider = SndScraperProvider(cache=cache)
    ref = DebentureRef(isin="BRBODYDBS000", codigo_ativo="BODY12", nome_emissor="A Bodytech")

    resultado = provider.fetch_characteristics(ref)

    assert resultado.sucesso
    assert resultado.valor.isin.valor == "BRBODYDBS000"
    assert resultado.valor.indexador.valor == "DI"
    assert resultado.valor.situacao.valor == "Registrado"


def test_fetch_market_data_sem_rede(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    _seed_cache(cache)
    provider = SndScraperProvider(cache=cache)
    ref = DebentureRef(isin="BRBODYDBS000", codigo_ativo="BODY12", nome_emissor="A Bodytech")

    resultado = provider.fetch_market_data(ref)

    assert resultado.sucesso
    assert len(resultado.valor) >= 30
    assert resultado.valor[0].pu_medio.valor == 4466.874270


def test_fetch_characteristics_ativo_inexistente_nao_falha(tmp_path) -> None:
    cache = SqliteCache(tmp_path / "cache.sqlite3")
    cache.set(FONTE, "caracteristicas", "publicas:NAOEXISTE9", "<html><body>nada aqui</body></html>")
    cache.set(FONTE, "caracteristicas", "privadas:NAOEXISTE9", "<html><body>nada aqui</body></html>")
    provider = SndScraperProvider(cache=cache)
    ref = DebentureRef(isin=None, codigo_ativo="NAOEXISTE9", nome_emissor="")

    resultado = provider.fetch_characteristics(ref)

    assert resultado.sucesso  # busca funcionou, só não achou o ativo
    assert resultado.valor.isin.disponivel is False
