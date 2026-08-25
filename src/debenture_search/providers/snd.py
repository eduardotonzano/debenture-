"""Provider do SND (debentures.com.br) — estoque, situação e mercado secundário.

STATUS: stub estruturado, NÃO verificado contra o site real.

Este ambiente de desenvolvimento tem o egress de rede bloqueado para
debentures.com.br (política do ambiente remoto), então não foi possível
abrir as páginas reais para confirmar:

  1. As URLs exatas das páginas de "Estoque por Ativo" e "Preços de
     Negociação" dentro de /exploreosnd/consultaadados/mercadosecundario/.
  2. Se a busca é GET ou POST, e os nomes exatos dos campos de formulário
     (ex.: o parâmetro de ISIN pode se chamar "isin", "cod_isin",
     "emissor" etc. — não adivinhei um nome específico onde importava).
  3. A estrutura HTML das tabelas de resultado (ids/classes CSS, ordem das
     colunas) para extrair estoque, situação e PU mín/médio/máx.

Em vez de adivinhar esses detalhes e arriscar um scraper que parece
funcionar mas extrai o campo errado silenciosamente, este módulo:

  - Centraliza tudo que precisa ser confirmado em `_Endpoints` e nas
    funções `_parse_*`, isoladas e testáveis com HTML de exemplo.
  - Falha alto e claro (`SndParsingError`) quando um seletor esperado não
    é encontrado, em vez de retornar um valor parcial/errado.
  - Já implementa corretamente a parte que NÃO depende da estrutura da
    página: rate limiting, cache, contrato com o restante do sistema
    (Protocols de `providers.base`).

Próximo passo (fora deste ambiente, onde há acesso à internet): rodar
`scripts/capture_snd_fixtures.py` (a criar) ou salvar manualmente o HTML de
uma busca real por ISIN/código de ativo em `tests/fixtures/`, então ajustar
`_Endpoints` e os seletores em `_parse_estoque_html` / `_parse_precos_html`
até os testes em `tests/test_snd_provider.py` passarem com dados reais.
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from debenture_search.cache import SqliteCache
from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import (
    Debenture,
    DebentureRef,
    MarketPriceSnapshot,
    SearchQuery,
    Situacao,
    SourcedValue,
)
from debenture_search.providers.base import ProviderResult

FONTE = "SND"


class SndParsingError(Exception):
    """A página do SND respondeu, mas o layout esperado não foi encontrado.

    Levantada em vez de retornar dados parciais/errados silenciosamente —
    sinal de que o site mudou ou que os seletores em _parse_* ainda não
    foram verificados contra o HTML real (ver docstring do módulo).
    """


class _Endpoints:
    """URLs candidatas — NÃO confirmadas contra o site real (ver docstring do módulo)."""

    BASE = "https://www.debentures.com.br"
    ESTOQUE = f"{BASE}/exploreosnd/consultaadados/mercadosecundario/estoque_e.asp"
    PRECOS_NEGOCIACAO = (
        f"{BASE}/exploreosnd/consultaadados/mercadosecundario/precosdenegociacao_e.asp"
    )


class SndScraperProvider:
    """Implementa SearchProvider, CharacteristicsProvider (parcial) e MarketDataProvider."""

    name = FONTE

    def __init__(self, cache: SqliteCache, http_client: RateLimitedHttpClient | None = None) -> None:
        self._cache = cache
        self._http = http_client or RateLimitedHttpClient()

    def is_available(self) -> bool:
        # Sempre "ligado" — é a fonte pública de base. Se a requisição falhar
        # em tempo de uso, o método correspondente retorna ProviderResult.falha,
        # não uma exceção que derruba o restante da ficha.
        return True

    # -- SearchProvider ----------------------------------------------------

    def search(self, query: SearchQuery) -> list[DebentureRef]:
        cache_key = query.isin or query.codigo_ativo or query.nome_emissor or ""
        query_type = "isin" if query.isin else "codigo_ativo" if query.codigo_ativo else "nome_emissor"

        cached_html = self._cache.get(self.name, f"search:{query_type}", cache_key)
        if cached_html is not None:
            html = cached_html
        else:
            response = self._http.get(
                _Endpoints.ESTOQUE,
                params=_build_search_params(query),
            )
            response.raise_for_status()
            html = response.text
            self._cache.set(self.name, f"search:{query_type}", cache_key, html)

        return _parse_search_results_html(html)

    # -- CharacteristicsProvider (parcial: só o que o SND ainda expõe) -----

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        cache_key = ref.isin or ref.codigo_ativo or ""
        try:
            cached_html = self._cache.get(self.name, "estoque", cache_key)
            if cached_html is not None:
                html = cached_html
            else:
                response = self._http.get(_Endpoints.ESTOQUE, params=_ref_params(ref))
                response.raise_for_status()
                html = response.text
                self._cache.set(self.name, "estoque", cache_key, html)

            debenture = _parse_estoque_html(html, ref)
            return ProviderResult.ok(self.name, debenture)
        except Exception as exc:  # noqa: BLE001 - queremos capturar qualquer falha de fonte externa
            return ProviderResult.falha(self.name, str(exc))

    # -- MarketDataProvider --------------------------------------------------

    def fetch_market_data(self, ref: DebentureRef) -> ProviderResult[list[MarketPriceSnapshot]]:
        cache_key = ref.isin or ref.codigo_ativo or ""
        try:
            cached_html = self._cache.get(self.name, "precos_negociacao", cache_key)
            if cached_html is not None:
                html = cached_html
            else:
                response = self._http.get(_Endpoints.PRECOS_NEGOCIACAO, params=_ref_params(ref))
                response.raise_for_status()
                html = response.text
                self._cache.set(self.name, "precos_negociacao", cache_key, html)

            snapshots = _parse_precos_html(html, ref)
            return ProviderResult.ok(self.name, snapshots)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def close(self) -> None:
        self._http.close()


def _build_search_params(query: SearchQuery) -> dict[str, str]:
    # TODO(verificar): nomes reais dos parâmetros de busca do formulário SND.
    if query.isin:
        return {"isin": query.isin}
    if query.codigo_ativo:
        return {"emissor": query.codigo_ativo}
    return {"emissor": query.nome_emissor or ""}


def _ref_params(ref: DebentureRef) -> dict[str, str]:
    if ref.isin:
        return {"isin": ref.isin}
    return {"emissor": ref.codigo_ativo or ref.nome_emissor}


def _parse_search_results_html(html: str) -> list[DebentureRef]:
    """TODO(verificar): seletor real da tabela/lista de resultados de busca."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table.resultado-busca")  # placeholder
    if table is None:
        raise SndParsingError(
            "Tabela de resultados de busca não encontrada — seletor "
            "'table.resultado-busca' é um placeholder, ajustar após capturar "
            "HTML real do SND (ver docstring de providers/snd.py)."
        )
    refs: list[DebentureRef] = []
    for row in table.select("tbody tr"):
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 3:
            continue
        codigo_ativo, isin, nome_emissor = cells[0], cells[1], cells[2]
        refs.append(
            DebentureRef(
                isin=isin or None,
                codigo_ativo=codigo_ativo or None,
                nome_emissor=nome_emissor,
            )
        )
    return refs


def _parse_estoque_html(html: str, ref: DebentureRef) -> Debenture:
    """TODO(verificar): seletores reais da página de estoque por ativo."""
    soup = BeautifulSoup(html, "lxml")
    campos = soup.select_one("div#ficha-estoque")  # placeholder
    if campos is None:
        raise SndParsingError(
            "Bloco de dados de estoque não encontrado — seletor "
            "'div#ficha-estoque' é um placeholder, ajustar após capturar "
            "HTML real do SND (ver docstring de providers/snd.py)."
        )

    def campo(label: str) -> str | None:
        el = campos.find(string=lambda s: s and label in s)  # type: ignore[arg-type]
        if el is None:
            return None
        valor_el = el.find_next("td")
        return valor_el.get_text(strip=True) if valor_el else None

    situacao_raw = campo("Situação")
    situacao = _map_situacao(situacao_raw)

    return Debenture(
        isin=SourcedValue(ref.isin, fonte=FONTE),
        codigo_ativo=SourcedValue(ref.codigo_ativo, fonte=FONTE),
        emissor_nome=SourcedValue(ref.nome_emissor, fonte=FONTE),
        situacao=SourcedValue(situacao, fonte=FONTE),
        motivo_saida=SourcedValue(campo("Motivo"), fonte=FONTE),
        quantidade_mercado=SourcedValue(campo("Quantidade em Mercado"), fonte=FONTE),
        quantidade_emitida=SourcedValue(campo("Quantidade Emitida"), fonte=FONTE),
    )


def _parse_precos_html(html: str, ref: DebentureRef) -> list[MarketPriceSnapshot]:
    """TODO(verificar): seletores reais da página de preços de negociação."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.select_one("table#precos-negociacao")  # placeholder
    if table is None:
        raise SndParsingError(
            "Tabela de preços de negociação não encontrada — seletor "
            "'table#precos-negociacao' é um placeholder, ajustar após "
            "capturar HTML real do SND (ver docstring de providers/snd.py)."
        )

    snapshots: list[MarketPriceSnapshot] = []
    for row in table.select("tbody tr"):
        cells = [c.get_text(strip=True) for c in row.select("td")]
        if len(cells) < 6:
            continue
        periodo, pu_min, pu_med, pu_max, qtd, n_negocios = cells[:6]
        snapshots.append(
            MarketPriceSnapshot(
                debenture_ref=ref,
                periodo_referencia=periodo,
                pu_minimo=SourcedValue(_parse_decimal(pu_min), fonte=FONTE),
                pu_medio=SourcedValue(_parse_decimal(pu_med), fonte=FONTE),
                pu_maximo=SourcedValue(_parse_decimal(pu_max), fonte=FONTE),
                quantidade_negociada=SourcedValue(_parse_int(qtd), fonte=FONTE),
                numero_negocios=SourcedValue(_parse_int(n_negocios), fonte=FONTE),
            )
        )
    return snapshots


def _map_situacao(raw: str | None) -> Situacao | None:
    if raw is None:
        return None
    normalizado = raw.strip().lower()
    if "ativ" in normalizado:
        return Situacao.ATIVA
    if "venc" in normalizado:
        return Situacao.VENCIDA
    if "resgat" in normalizado:
        return Situacao.RESGATADA
    return None


def _parse_decimal(raw: str) -> float | None:
    limpo = raw.replace(".", "").replace(",", ".").strip()
    try:
        return float(limpo)
    except ValueError:
        return None


def _parse_int(raw: str) -> int | None:
    limpo = raw.replace(".", "").strip()
    try:
        return int(limpo)
    except ValueError:
        return None
