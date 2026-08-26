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

import html
import re
import unicodedata
from datetime import date, datetime

from bs4 import BeautifulSoup

from debenture_search.cache import SqliteCache
from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import (
    Debenture,
    DebentureRef,
    Event,
    MarketPriceSnapshot,
    SearchQuery,
    SourcedValue,
    TipoEvento,
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
    REGISTROS_EXCLUIDOS_RESULT = (
        f"{BASE}/consultaadados/emissoesdedebentures/registrosexcluidos_r.asp"
    )
    REPACTUACOES_RESULT = f"{BASE}/consultaadados/emissoesdedebentures/repactuacoes_r.asp"
    VENCIMENTOS_ANTECIPADOS_RESULT = (
        f"{BASE}/consultaadados/emissoesdedebentures/vencimentosantecipados_r.asp"
    )
    INADIMPLENCIAS_RESULT = f"{BASE}/consultaadados/emissoesdedebentures/inadimplencias_r.asp"


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
            cnpj = _extrair_cnpj_do_canonical(html)
            if cnpj:
                # Já extraído de qualquer forma pra alimentar a consulta de
                # preços — só faltava expor no modelo (usado também pelo
                # CvmDocumentsProvider pra casar documentos por CNPJ).
                debenture.emissor_cnpj = SourcedValue(cnpj, fonte=FONTE)
            self._marcar_registro_excluido(debenture, codigo)
            self._marcar_vencimento_antecipado(debenture, codigo)
            self._marcar_inadimplencia(debenture, codigo)
            return ProviderResult.ok(self.name, debenture)
        except SndNaoEncontrado:
            # Não é falha de fonte — o ativo genuinamente não está aqui.
            return ProviderResult.ok(self.name, Debenture())
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def _marcar_registro_excluido(self, debenture: Debenture, codigo: str) -> None:
        """Confere se o ativo está na lista de Registros Excluídos do SND —
        um dos sinais mais diretos de 'problema com a debênture' que o SND
        expõe (motivo de exclusão às vezes indica processo na CVM, e o
        próprio nome do emissor às vezes já vem com 'EM RECUPERAÇÃO
        JUDICIAL'). Falha aqui nunca derruba a ficha — só deixa os campos
        de exclusão indisponíveis."""
        try:
            registros = self._fetch_registros_excluidos()
        except Exception:  # noqa: BLE001
            return
        alvo = codigo.strip().upper()
        for data_exclusao, codigo_ativo, _emissor, motivo in registros:
            if codigo_ativo.strip().upper() == alvo:
                debenture.data_exclusao_registro = SourcedValue(
                    data_exclusao, fonte=f"{FONTE} (Registros Excluídos)"
                )
                if motivo:
                    debenture.motivo_saida = SourcedValue(
                        motivo, fonte=f"{FONTE} (Registros Excluídos)"
                    )
                return

    def _fetch_registros_excluidos(self) -> list[tuple[date, str, str, str | None]]:
        """Lista GLOBAL (todos os emissores) de registros excluídos —
        buscada e cacheada uma única vez (não por ativo), depois filtrada
        localmente. `mes_ini` é obrigatório no formulário real; usamos uma
        data bem antiga como padrão pra pegar o histórico completo."""
        mes_fim = datetime.utcnow().strftime("%m/%Y")
        html = self._post_cached_or_fetch(
            "registros_excluidos", f"01/2000-{mes_fim}",
            _Endpoints.REGISTROS_EXCLUIDOS_RESULT,
            data={"mes_ini": "01/2000", "mes_fim": mes_fim, "Submit3.x": "1", "Submit3.y": "1"},
        )
        return _parse_registros_excluidos_html(html)

    def _marcar_vencimento_antecipado(self, debenture: Debenture, codigo: str) -> None:
        """Confere se o ativo teve vencimento antecipado declarado — sinal
        de problema ainda mais direto que registro excluído (normalmente
        indica quebra de covenant ou evento de default). Só confiamos na
        detecção de 'nenhum resultado' por enquanto — ver
        _parse_vencimentos_antecipados_html e o README."""
        try:
            eventos = self._fetch_vencimentos_antecipados()
        except Exception:  # noqa: BLE001
            return
        alvo = codigo.strip().upper()
        for data_declaracao, codigo_ativo, _emissor in eventos:
            if codigo_ativo.strip().upper() == alvo:
                debenture.data_vencimento_antecipado = SourcedValue(
                    data_declaracao, fonte=f"{FONTE} (Vencimentos Antecipados)"
                )
                return

    def _fetch_vencimentos_antecipados(self) -> list[tuple[date, str, str]]:
        """Lista GLOBAL de vencimentos antecipados declarados. `dt_ini` é
        obrigatório no formulário real. Toda consulta feita até agora
        (2020 em diante) voltou vazia — ver _parse_vencimentos_antecipados_html."""
        hoje = datetime.utcnow().strftime("%d/%m/%Y")
        html = self._post_cached_or_fetch(
            "vencimentos_antecipados", f"01/01/1995-{hoje}",
            _Endpoints.VENCIMENTOS_ANTECIPADOS_RESULT,
            data={
                "op_exc": "False", "emissor": "", "ativo": "",
                "dt_ini": "01/01/1995", "dt_fim": hoje,
                "Submit3.x": "1", "Submit3.y": "1",
            },
        )
        return _parse_vencimentos_antecipados_html(html)

    def _marcar_inadimplencia(self, debenture: Debenture, codigo: str) -> None:
        """Confere se o ativo está na lista de Inadimplências correntes do
        SND — o sinal de problema mais direto de todos (é literalmente
        'este ativo está inadimplente agora'). Diferente das outras duas
        listas, não tem data — é um retrato do estado atual, não um
        histórico."""
        try:
            inadimplencias = self._fetch_inadimplencias()
        except Exception:  # noqa: BLE001
            return
        alvo = codigo.strip().upper()
        for codigo_ativo, motivo in inadimplencias:
            if codigo_ativo.strip().upper() == alvo:
                debenture.motivo_inadimplencia = SourcedValue(
                    motivo or "Inadimplente (motivo não informado pela fonte)",
                    fonte=f"{FONTE} (Inadimplências)",
                )
                return

    def _fetch_inadimplencias(self) -> list[tuple[str, str | None]]:
        """Lista GLOBAL de inadimplências correntes. Diferente das outras
        duas, o envio em branco funcionou sem exigir data — confirmado
        contra página real (retornou vazio: nenhuma inadimplência no
        momento da captura)."""
        html = self._post_cached_or_fetch(
            "inadimplencias", "global",
            _Endpoints.INADIMPLENCIAS_RESULT,
            data={
                "op_exc": "False", "emissor": "", "ativo": "",
                "dt_ini": "", "dt_fim": "", "Submit.x": "1", "Submit.y": "1",
            },
        )
        return _parse_inadimplencias_html(html)

    # -- EventsProvider ------------------------------------------------------

    def fetch_events(self, ref: DebentureRef) -> ProviderResult[list[Event]]:
        codigo = ref.codigo_ativo
        if not codigo:
            return ProviderResult.ok(self.name, [])
        try:
            repactuacoes = self._fetch_repactuacoes()
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))
        alvo = codigo.strip().upper()
        eventos = [
            Event(
                debenture_ref=ref,
                tipo=TipoEvento.REPACTUACAO,
                data_prevista=data,
                valor=SourcedValue(deliberacao, fonte=FONTE) if deliberacao else SourcedValue(None),
                fonte=FONTE,
            )
            for data, codigo_ativo, _emissor, deliberacao in repactuacoes
            if codigo_ativo.strip().upper() == alvo
        ]
        return ProviderResult.ok(self.name, eventos)

    def _fetch_repactuacoes(self) -> list[tuple[date, str, str, str | None]]:
        """Lista GLOBAL de repactuações históricas — busca uma única vez,
        filtrada localmente por ativo. Campos em branco funcionaram na
        consulta real (não exige dt_ini como as outras duas)."""
        html = self._post_cached_or_fetch(
            "repactuacoes", "global",
            _Endpoints.REPACTUACOES_RESULT,
            data={
                "op_exc": "False", "emissor": "", "ativo": "",
                "dt_ini": "", "dt_fim": "", "evento": "",
                "Submit.x": "1", "Submit.y": "1",
            },
        )
        return _parse_repactuacoes_html(html)

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


def _flatten(pagina_html: str) -> str:
    """Achata HTML pra texto simples pra extração por rótulo.

    Colapsa toda quebra de linha FÍSICA do HTML fonte (que o navegador
    também ignora — só vira espaço) antes de introduzir nossas próprias
    quebras de linha semânticas (uma por <br> e por fronteira de tag). Sem
    isso, um valor que quebra linha no meio do HTML fonte (comum nas
    tabelas do SND, ex.: "Atos Societários") ficava truncado no primeiro \\n
    físico, que `_campo` confundia com o fim do valor.
    """
    sem_quebras_fisicas = re.sub(r"\s+", " ", pagina_html)
    texto = re.sub(r"<br\s*/?>", "\n", sem_quebras_fisicas, flags=re.I)
    texto = re.sub(r"<[^>]+>", "\n", texto)
    texto = html.unescape(texto)
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


def _campo_regime_registro(texto: str) -> str | None:
    """'Registro CVM da Emissão' é servido em células de tabela separadas
    ('DISPENSA ICVM 476/09' | 'em' | '10/06/2013') em vez de uma linha só —
    junta as duas partes quando a segunda existir."""
    m = re.search(
        r"Registro CVM da Emiss[^:\n]{0,10}:\s*\n?\s*([^\n]+)\s*\n\s*em\s*\n?\s*([^\n]+)",
        texto,
    )
    if m:
        regime, data_str = m.group(1).strip(), m.group(2).strip()
        return f"{regime} em {data_str}" if data_str else regime
    return _campo(texto, "Registro CVM da Emiss")


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
        forma=SourcedValue(_campo(texto, "Forma"), fonte=FONTE),
        registro_cvm_emissao=SourcedValue(_campo_regime_registro(texto), fonte=FONTE),
        ato_societario=SourcedValue(_campo(texto, "Atos Societ"), fonte=FONTE),
        inicio_distribuicao=SourcedValue(_campo_data(texto, "cio de Distribui"), fonte=FONTE),
        banco_mandatario=SourcedValue(_campo(texto, "Banco Mandat"), fonte=FONTE),
        agente_fiduciario=SourcedValue(_campo(texto, "Agente Fiduci"), fonte=FONTE),
        instituicao_depositaria=SourcedValue(_campo(texto, "Institui"), fonte=FONTE),
        coordenador_lider=SourcedValue(_campo(texto, "Coordenador L"), fonte=FONTE),
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


# -- parsing: registrosexcluidos_r.asp --------------------------------------


def _parse_registros_excluidos_html(html: str) -> list[tuple[date, str, str, str | None]]:
    """Lista global: Data de Exclusão | Ativo | Emissor | Motivo. O Motivo
    vem em branco na maioria das linhas (SND não preenche sempre) — isso é
    dado real, não falha de parsing."""
    soup = BeautifulSoup(html, "lxml")
    data_regex = re.compile(r"^\d{2}/\d{2}/\d{4}$")

    resultado: list[tuple[date, str, str, str | None]] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 6:
            continue
        textos = [td.get_text(strip=True) for td in tds]
        data_str, codigo_ativo, emissor, _sep, motivo, _sep2 = textos
        if not data_regex.match(data_str):
            continue
        data_exclusao = _parse_data_br(data_str)
        if data_exclusao is None:
            continue
        resultado.append((data_exclusao, codigo_ativo, emissor, motivo or None))
    return resultado


# -- parsing: repactuacoes_r.asp --------------------------------------------

_ATIVO_HREF_RE = re.compile(r"[?&]ativo=([A-Z0-9]+)")


def _parse_repactuacoes_html(html: str) -> list[tuple[date, str, str, str | None]]:
    """Lista global: Data de Repactuação | Ativo (link com o código na
    querystring) | Emissor - Código | Deliberação. O código de ativo vem
    da querystring do link, não do texto visível (que mistura emissor e
    código com ' - ', e o nome do emissor às vezes já tem ' - ' dentro,
    ex.: 'NOVONOR ENERGIA S.A - EM RECUPERACAO JUDICIAL')."""
    soup = BeautifulSoup(html, "lxml")
    resultado: list[tuple[date, str, str, str | None]] = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 4:
            continue
        link = tds[0].find("a", href=_ATIVO_HREF_RE)
        if link is None:
            continue
        m = _ATIVO_HREF_RE.search(link["href"])
        codigo_ativo = m.group(1)
        data_repactuacao = _parse_data_br(link.get_text(strip=True))
        if data_repactuacao is None:
            continue
        emissor_e_codigo = tds[1].get_text(strip=True)
        emissor = emissor_e_codigo.rsplit(f" - {codigo_ativo}", 1)[0]
        deliberacao = tds[3].get_text(strip=True) or None
        resultado.append((data_repactuacao, codigo_ativo, emissor, deliberacao))
    return resultado


# -- parsing: vencimentosantecipados_r.asp ----------------------------------


def _parse_vencimentos_antecipados_html(html: str) -> list[tuple[date, str, str]]:
    """STATUS: só a detecção de 'nenhum resultado' foi verificada contra
    página real — toda consulta feita até agora (2020 em diante) voltou
    vazia. Levanta SndParsingError se a página tiver conteúdo que não seja
    esse caso vazio conhecido, em vez de arriscar um parsing de linha
    nunca confirmado contra dado real. Ver README para o que falta."""
    if "existe resposta para os itens selecionados" in html:
        return []
    raise SndParsingError(
        "vencimentosantecipados_r.asp retornou conteúdo com possíveis "
        "registros, mas o parsing de linhas populadas nunca foi validado "
        "contra uma amostra real (toda consulta até agora voltou vazia) — "
        "ver README, seção de páginas de alerta pendentes."
    )


# -- parsing: inadimplencias_r.asp ------------------------------------------


def _parse_inadimplencias_html(html: str) -> list[tuple[str, str | None]]:
    """Lista GLOBAL de inadimplências correntes: cabeçalho confirmado como
    Ativo (*) | Motivo, sem data — é um retrato do estado atual, não
    histórico. Mesma cautela das outras duas: só confirmamos o caso vazio
    ('Não existe resposta...') contra página real; nunca vimos uma
    populada, então levantamos SndParsingError pra qualquer outro
    conteúdo em vez de arriscar um parsing de linha nunca confirmado."""
    if "existe resposta para os itens selecionados" in html:
        return []
    raise SndParsingError(
        "inadimplencias_r.asp retornou conteúdo com possíveis registros, "
        "mas o parsing de linha ainda não foi validado contra uma amostra "
        "real com inadimplência de verdade (toda consulta até agora "
        "voltou vazia) — ver README, seção de páginas de alerta pendentes."
    )
