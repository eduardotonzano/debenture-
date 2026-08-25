"""Provider do SND (debentures.com.br) — estoque, situação, mercado secundário
e (enquanto durar) características completas.

STATUS: parsing verificado contra HTML real, capturado via HAR pelo usuário
em 25/08/2026 (ver tests/fixtures/snd_*.html). Fluxo real do site,
mapeado a partir do tráfego de rede:

  1. `estoqueporativo_f.asp` (sem parâmetros) embute um <select name="emissor">
     ESTÁTICO com todos os ~1.466 emissores do SND (nome -> CNPJ). Isso é
     baixado e cacheado uma única vez — a busca por nome de emissor é feita
     localmente sobre essa lista, não bate no servidor a cada busca.
  2. `estoqueporativo_f.asp?emissor=<CNPJ>&op_exc=` retorna o mesmo formulário
     com um <select name="ativo"> populado só com os ativos daquele emissor.
  3. `caracteristicas_d.asp?tip_deb={publicas|privadas}&selecao=<código>` é a
     melhor rota: busca DIRETA por código de ativo, sem precisar resolver o
     emissor antes, e retorna uma ficha completa (ISIN, situação, indexador,
     spread, garantia, classe, quantidades, valor nominal, agentes,
     classificação de risco quando houver). Confirmado que NÃO redireciona
     para o ANBIMA Data (só tem um <link rel="canonical"> apontando pra lá,
     que é inofensivo — é só um dado de SEO, não é seguido pelo navegador).
  4. `precosdenegociacao_r.asp` (POST) retorna o histórico de PU mín/médio/
     máx e quantidade negociada. Exige o CNPJ do emissor no payload — como
     `caracteristicas_d.asp` embute esse CNPJ no `<link rel="canonical">`,
     extraímos de lá em vez de fazer o usuário resolver o emissor de novo.

O que ainda é incerto (marcado com TODO(verificar) no código):
  - Se `selecao=` em `caracteristicas_d.asp` aceita ISIN além de código de
    ativo — só testamos com código de ativo.
  - Se existe alguma forma de busca "global" por ISIN/código sem passar por
    emissor ou por esse endpoint de detalhe — não encontramos uma no
    tráfego capturado, então busca só por ISIN quando o usuário não sabe o
    código de ativo pode simplesmente não resolver via SND (retorna lista
    vazia, não é erro).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime

from bs4 import BeautifulSoup

from debenture_search.cache import SqliteCache
from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import (
    Debenture,
    DebentureRef,
    MarketPriceSnapshot,
    SearchQuery,
    SourcedValue,
)
from debenture_search.providers.base import ProviderResult

FONTE = "SND"
_ENCODING = "windows-1252"


class SndParsingError(Exception):
    """A página do SND respondeu, mas o layout esperado não foi encontrado —
    sinal de que o site mudou, não de que o ativo não existe (ver
    SndNaoEncontrado para esse segundo caso)."""


class SndNaoEncontrado(Exception):
    """Consulta bem-sucedida, mas o ativo/emissor buscado não existe no
    SND — resultado vazio legítimo, nunca deve virar erro pro usuário."""


class _Endpoints:
    BASE = "https://www.debentures.com.br/exploreosnd"
    ESTOQUE_FORM = f"{BASE}/consultaadados/estoque/estoqueporativo_f.asp"
    ESTOQUE_RESULT = f"{BASE}/consultaadados/estoque/estoqueporativo_r.asp"
    PRECOS_RESULT = f"{BASE}/consultaadados/mercadosecundario/precosdenegociacao_r.asp"
    CARACTERISTICAS_DETALHE = (
        f"{BASE}/consultaadados/emissoesdedebentures/caracteristicas_d.asp"
    )


def _normalize(texto: str) -> str:
    """Remove acentos e caixa para comparação de busca (não para exibição)."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.upper().strip()


def _pad_ativo(codigo: str) -> str:
    """O SND espera o código de ativo alinhado à esquerda em 10 caracteres
    (ex.: "BODY12    ") nos parâmetros de formulário — confirmado no payload
    real capturado."""
    return codigo.strip().ljust(10)


class SndScraperProvider:
    """Implementa SearchProvider, CharacteristicsProvider e MarketDataProvider."""

    name = FONTE

    def __init__(self, cache: SqliteCache, http_client: RateLimitedHttpClient | None = None) -> None:
        self._cache = cache
        self._http = http_client or RateLimitedHttpClient()

    def is_available(self) -> bool:
        return True

    # -- SearchProvider ------------------------------------------------

    def search(self, query: SearchQuery) -> list[DebentureRef]:
        if query.nome_emissor:
            return self._search_por_emissor(query.nome_emissor)
        codigo = query.codigo_ativo or query.isin
        try:
            _, ref = self._fetch_caracteristicas_html(codigo)
        except SndNaoEncontrado:
            return []
        return [ref]

    def _search_por_emissor(self, nome: str) -> list[DebentureRef]:
        html = self._get_cached_or_fetch(
            "emissores_lista", "", _Endpoints.ESTOQUE_FORM, params=None
        )
        emissores = _parse_emissor_options(html)
        alvo = _normalize(nome)
        refs: list[DebentureRef] = []
        for cnpj, nome_emissor in emissores:
            if alvo not in _normalize(nome_emissor):
                continue
            ativos_html = self._get_cached_or_fetch(
                "ativos_por_emissor", cnpj, _Endpoints.ESTOQUE_FORM,
                params={"emissor": cnpj, "op_exc": ""},
            )
            for codigo_ativo in _parse_ativo_options(ativos_html):
                refs.append(
                    DebentureRef(isin=None, codigo_ativo=codigo_ativo, nome_emissor=nome_emissor)
                )
        return refs

    # -- CharacteristicsProvider -----------------------------------------

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        codigo = ref.codigo_ativo or ref.isin
        try:
            html, _ = self._fetch_caracteristicas_html(codigo)
            debenture = _parse_caracteristicas_html(html, codigo_ativo=codigo.strip())
            return ProviderResult.ok(self.name, debenture)
        except SndNaoEncontrado:
            # Não é falha de fonte — o ativo genuinamente não está aqui.
            return ProviderResult.ok(self.name, Debenture())
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def _fetch_caracteristicas_html(self, codigo: str) -> tuple[str, DebentureRef]:
        """Tenta tip_deb=publicas, depois privadas. Levanta SndNaoEncontrado
        se nenhum dos dois tiver o ativo."""
        for tip_deb in ("publicas", "privadas"):
            html = self._get_cached_or_fetch(
                "caracteristicas", f"{tip_deb}:{codigo}",
                _Endpoints.CARACTERISTICAS_DETALHE,
                params={"tip_deb": tip_deb, "selecao": codigo},
            )
            if _caracteristicas_encontrou_ativo(html):
                ref = DebentureRef(
                    isin=_extrair_campo_texto(html, "ISIN"),
                    codigo_ativo=codigo.strip(),
                    nome_emissor=_extrair_campo_texto(html, "Emissor") or "",
                )
                return html, ref
        raise SndNaoEncontrado(codigo)

    # -- MarketDataProvider ------------------------------------------------

    def fetch_market_data(self, ref: DebentureRef) -> ProviderResult[list[MarketPriceSnapshot]]:
        codigo = ref.codigo_ativo
        if not codigo:
            return ProviderResult.ok(self.name, [])
        try:
            caract_html, resolved_ref = self._fetch_caracteristicas_html(codigo)
            cnpj = _extrair_cnpj_do_canonical(caract_html)
            if cnpj is None:
                raise SndParsingError(
                    "CNPJ do emissor não encontrado no link canônico da página "
                    "de características — necessário para consultar preços."
                )
            isin = resolved_ref.isin or ""
            html = self._post_cached_or_fetch(
                "precos", codigo,
                _Endpoints.PRECOS_RESULT,
                data={
                    "op_exc": "False",
                    "emissor": cnpj,
                    "ativo": _pad_ativo(codigo),
                    "ISIN": isin,
                    "dt_ini": "",
                    "dt_fim": "",
                    "Submit32.x": "1",
                    "Submit32.y": "1",
                },
            )
            snapshots = _parse_precos_html(html, ref)
            return ProviderResult.ok(self.name, snapshots)
        except SndNaoEncontrado:
            return ProviderResult.ok(self.name, [])
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def close(self) -> None:
        self._http.close()

    # -- infra de cache/requisição ------------------------------------------

    def _get_cached_or_fetch(
        self, query_type: str, cache_key: str, url: str, params: dict[str, str] | None
    ) -> str:
        cached = self._cache.get(self.name, query_type, cache_key)
        if cached is not None:
            return cached
        response = self._http.get(url, params=params)
        response.raise_for_status()
        html = response.content.decode(_ENCODING, errors="replace")
        self._cache.set(self.name, query_type, cache_key, html)
        return html

    def _post_cached_or_fetch(
        self, query_type: str, cache_key: str, url: str, data: dict[str, str]
    ) -> str:
        cached = self._cache.get(self.name, query_type, cache_key)
        if cached is not None:
            return cached
        response = self._http.post(url, data=data)
        response.raise_for_status()
        html = response.content.decode(_ENCODING, errors="replace")
        self._cache.set(self.name, query_type, cache_key, html)
        return html


# -- parsing: lista de emissores / ativos --------------------------------------


def _parse_emissor_options(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", attrs={"name": "emissor"})
    if select is None:
        raise SndParsingError(
            "Select 'emissor' não encontrado em estoqueporativo_f.asp — "
            "layout do SND pode ter mudado."
        )
    resultado = []
    for option in select.find_all("option"):
        valor = (option.get("value") or "").strip()
        texto = option.get_text(strip=True)
        if valor:
            resultado.append((valor, texto))
    return resultado


def _parse_ativo_options(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    select = soup.find("select", attrs={"name": "ativo"})
    if select is None:
        raise SndParsingError(
            "Select 'ativo' não encontrado em estoqueporativo_f.asp?emissor=... "
            "— layout do SND pode ter mudado."
        )
    return [
        (option.get("value") or "").strip()
        for option in select.find_all("option")
        if (option.get("value") or "").strip()
    ]


# -- parsing: caracteristicas_d.asp -----------------------------------------


def _flatten(html: str) -> str:
    texto = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    texto = re.sub(r"<[^>]+>", "\n", texto)
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n[ \t]*\n+", "\n", texto)
    return texto


def _caracteristicas_encontrou_ativo(html: str) -> bool:
    """Heurística de 'não encontrado': uma página de características válida
    sempre tem os blocos 'ISIN:' e 'Situação:'. Não usamos o rótulo
    'Ativo:' aqui porque ele aparece DUAS vezes na página real (um rótulo
    vazio de layout de tabela, seguido do rótulo com o valor de verdade) —
    ambíguo demais pra servir de heurística. Sem uma amostra real de página
    de 'não encontrado' pra confirmar o comportamento exato — ver docstring
    do módulo — então isso é best-effort, documentado como tal."""
    texto = _flatten(html)
    return "ISIN:" in texto and re.search(r"Situa[^:\n]{0,10}:", texto) is not None


def _campo(texto: str, prefixo_sem_acento: str) -> str | None:
    """Extrai o valor de um campo 'Rótulo: valor' usando só o prefixo do
    rótulo sem acentuação — o SND é servido em windows-1252/iso-8859-1 e o
    HAR exportado pelo navegador às vezes corrompe caracteres acentuados
    (viram 'ï¿½'); casar só o prefixo sem acento evita depender de acerto
    de encoding para achar o campo."""
    m = re.search(re.escape(prefixo_sem_acento) + r"[^:\n]{0,15}:\s*\n?\s*([^\n]+)", texto)
    if m is None:
        return None
    valor = m.group(1).strip()
    return valor or None


def _campo_data(texto: str, prefixo_sem_acento: str) -> date | None:
    """Como _campo, mas exige que o valor capturado tenha formato de data —
    desambigua rótulos repetidos (ex.: 'Emissão' aparece em 'Registro CVM
    da Emissão', 'Datas: Emissão' e 'Nominal na Emissão'; só a segunda tem
    uma data como valor imediato)."""
    for m in re.finditer(re.escape(prefixo_sem_acento) + r"[^:\n]{0,10}:\s*\n?\s*(\d{2}/\d{2}/\d{4})", texto):
        return _parse_data_br(m.group(1))
    return None


def _extrair_campo_texto(html: str, label_sem_acento: str) -> str | None:
    return _campo(_flatten(html), label_sem_acento)


def _extrair_cnpj_do_canonical(html: str) -> str | None:
    """O CNPJ do emissor não aparece em texto simples na página, só embutido
    no <link rel="canonical"> que aponta pro ANBIMA Data — é só leitura de
    um dado que o próprio SND publica na sua página, não é uma chamada ao
    ANBIMA."""
    m = re.search(r"debentures/emissores/(\d{14})/emissoes", html)
    return m.group(1) if m else None


def _parse_decimal_br(raw: str) -> float | None:
    limpo = raw.strip().replace(".", "").replace(",", ".")
    try:
        return float(limpo)
    except ValueError:
        return None


def _parse_int_br(raw: str) -> int | None:
    limpo = raw.strip().replace(".", "")
    try:
        return int(limpo)
    except ValueError:
        return None


def _parse_data_br(raw: str) -> date | None:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%d/%m/%Y").date()
    except ValueError:
        return None


def _map_situacao(raw: str | None) -> str | None:
    """Guarda o texto cru do SND (ex.: 'Registrado') em vez de forçar num
    enum que pode não cobrir todos os valores reais do site — ver
    justificativa no README/commit desta mudança: melhor mostrar o dado
    real do que uma categorização inventada."""
    return raw


def _parse_caracteristicas_html(html: str, codigo_ativo: str) -> Debenture:
    texto = _flatten(html)

    indexador = _campo(texto, "Tipo de Remunera")
    spread_m = re.search(r"Taxa de Juros/Spread:\s*\n?\s*([\d.,]+)", texto)
    spread = spread_m.group(1).strip() if spread_m else None
    if indexador and spread:
        taxa_valor = f"{indexador} + {spread}%"
    elif spread:
        taxa_valor = f"{spread}%"
    else:
        taxa_valor = None

    valor_nominal_m = re.search(
        r"Nominal em\s*\n?\s*[\d/]+:\s*\n?\s*R\$\s*([\d.,]+)", texto
    )
    valor_nominal = valor_nominal_m.group(1).strip() if valor_nominal_m else None

    rating = _campo(texto, "Classifica")
    if rating and not re.search(r"[A-Za-z0-9]", rating):
        rating = None

    return Debenture(
        isin=SourcedValue(_campo(texto, "ISIN"), fonte=FONTE),
        codigo_ativo=SourcedValue(codigo_ativo, fonte=FONTE),
        emissor_nome=SourcedValue(_campo(texto, "Emissor"), fonte=FONTE),
        numero_serie=SourcedValue(_campo(texto, "rie/Emiss"), fonte=FONTE),
        indexador=SourcedValue(indexador, fonte=FONTE),
        taxa=SourcedValue(taxa_valor, fonte=FONTE),
        data_emissao=SourcedValue(_campo_data(texto, "Emiss"), fonte=FONTE),
        data_vencimento=SourcedValue(_campo_data(texto, "Vencimento"), fonte=FONTE),
        especie=SourcedValue(_campo(texto, "Garantia/Esp"), fonte=FONTE),
        classe=SourcedValue(_campo(texto, "Classe"), fonte=FONTE),
        quantidade_emitida=SourcedValue(_campo(texto, "Emitida"), fonte=FONTE),
        quantidade_mercado=SourcedValue(_campo(texto, "Mercado"), fonte=FONTE),
        valor_nominal_unitario=SourcedValue(valor_nominal, fonte=FONTE),
        situacao=SourcedValue(_map_situacao(_campo(texto, "Situa")), fonte=FONTE),
        rating=SourcedValue(rating, fonte=FONTE),
    )


# -- parsing: precosdenegociacao_r.asp --------------------------------------


def _parse_precos_html(html: str, ref: DebentureRef) -> list[MarketPriceSnapshot]:
    soup = BeautifulSoup(html, "lxml")
    isin_regex = re.compile(r"^BR[A-Z0-9]{10}$")

    linhas: list[MarketPriceSnapshot] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        textos = [td.get_text(strip=True) for td in tds]
        if len(textos) != 20 or not any(isin_regex.match(t) for t in textos):
            continue
        valores = [t for t in textos if t]
        if len(valores) < 10:
            continue
        data_str, _emissor, _ativo, _isin, qtd, neg, minimo, medio, maximo, _pu_curva = valores[:10]
        linhas.append(
            MarketPriceSnapshot(
                debenture_ref=ref,
                periodo_referencia=data_str,
                pu_minimo=SourcedValue(_parse_decimal_br(minimo), fonte=FONTE),
                pu_medio=SourcedValue(_parse_decimal_br(medio), fonte=FONTE),
                pu_maximo=SourcedValue(_parse_decimal_br(maximo), fonte=FONTE),
                quantidade_negociada=SourcedValue(_parse_int_br(qtd), fonte=FONTE),
                numero_negocios=SourcedValue(_parse_int_br(neg), fonte=FONTE),
            )
        )

    if not linhas:
        # Pode ser "sem negociação no período" (legítimo) ou o layout ter
        # mudado — como não temos uma amostra real de "zero negócios" pra
        # diferenciar, não levantamos erro aqui: lista vazia é honesto nos
        # dois casos (campo fica "indisponível" na ficha, não inventa dado).
        return []
    return linhas
