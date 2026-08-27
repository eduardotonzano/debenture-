"""Provider da API oficial paga da ANBIMA ("ANBIMA Feed - Preços e Índices").

Contrato CONFIRMADO (não é mais placeholder) a partir de duas fontes reais
do portal ANBIMA Developers, capturadas via HAR pelo próprio usuário:

1. Página de texto "Autenticação"
   (developers.anbima.com.br/pt/documentacao/visao-geral/autenticacao/):

   Passo 1 — obter o access_token (OAuth2 client_credentials):

       POST https://api.anbima.com.br/oauth/access-token
       Content-Type: application/json
       Authorization: Basic base64("<client_id>:<client_secret>")

       {"grant_type": "client_credentials"}

   Resposta:

       {"access_token": "...", "token_type": "access_token", "expires_in": 3600}

   `expires_in` é em segundos. Passado esse tempo o token expira e o passo
   1 deve ser repetido.

   Passo 2 — toda chamada de dado exige DOIS headers (não é
   `Authorization: Bearer`, é um par de `apiKey` conforme o `securitySchemes`
   do Swagger real):

       client_id: <client_id>
       access_token: <token obtido no passo 1>

2. Swagger real ("Portal Swagger - ANBIMA Feed Preços & Índices",
   documentacao/precos-indices/swagger-precos-e-indices/), spec OpenAPI
   3.0.1 embutida na própria página:

   - `servers`: produção `https://api.anbima.com.br/feed/precos-indices`,
     sandbox `https://api-sandbox.anbima.com.br/feed/precos-indices`.
     (A página de texto de Autenticação usa `api.sandbox.anbima.com.br`
     com ponto, em vez de hífen — só o Swagger foi usado aqui porque é o
     valor que a própria ANBIMA usa para montar as chamadas de teste; se
     não bater na prática, é o primeiro lugar a conferir.)
   - `GET /v1/debentures/mercado-secundario?data=YYYY-MM-DD`: parâmetro
     `data` opcional (sem ele, retorna o dia mais recente disponível).
     IMPORTANTE: não existe filtro por ativo/ISIN na própria API — o
     endpoint devolve a lista inteira do dia (schema
     `MercadoSecundarioDebenturesLista`), e o filtro pelo `codigo_ativo`
     buscado é feito localmente neste módulo. Por isso a lista do dia é
     cacheada em memória por instância: uma chamada de rede serve qualquer
     busca feita no mesmo dia, em vez de uma chamada por busca.
   - Cada item (`MercadoSecundarioDebentures`) é dado de PRECIFICAÇÃO de
     mercado — `codigo_ativo`, `emissor`, `data_vencimento`, `pu`,
     `taxa_indicativa`, `taxa_compra`, `taxa_venda`, `desvio_padrao`,
     `duration`, `val_min_intervalo`, `val_max_intervalo`, `grupo`,
     `data_referencia`, `percent_pu_par` (percentual PU/par), entre
     outros — não características de emissão. Por isso este provider
     implementa `MarketDataProvider`, não `CharacteristicsProvider`
     (diferente da versão anterior deste arquivo, escrita antes de ter
     acesso ao contrato real). IMPORTANTE: esse schema NÃO tem `pu_par`
     em valor absoluto — só o percentual. Calcular o valor absoluto a
     partir do percentual seria inventar um dado que a fonte não afirma
     diretamente, então `pu_par` fica indisponível pra debêntures normais
     (só o schema "+" abaixo tem o valor pronto).
   - `GET /v1/debentures-mais/mercado-secundario`: mesmo formato (schema
     `MercadoSecundarioDebenturesMais`, campos idênticos aos de cima),
     mas pra "Debêntures+" — a categoria de debêntures incentivadas
     (isentas de IR, Lei 12.431/11). Uma debênture incentivada NÃO
     aparece na lista de `/v1/debentures/mercado-secundario` — por isso
     este provider consulta os dois endpoints e funde os resultados; sem
     isso, qualquer debênture incentivada ficaria sem preço da ANBIMA
     mesmo com credencial válida.

O que ainda NÃO foi confirmado por uma chamada real (o domínio da API está
bloqueado no sandbox de desenvolvimento — mesma restrição que afetou
SND/ANBIMA Data/CVM): o usuário tem credencial sandbox aprovada
("Motor de Busca de Debêntures"), mas nunca foi possível efetivamente
disparar a chamada a partir daqui. Rodar isso de verdade requer setar
ANBIMA_CLIENT_ID/ANBIMA_CLIENT_SECRET localmente (nunca no repositório) e
testar fora deste ambiente.
"""

from __future__ import annotations

import base64
import time
from datetime import date
from threading import Lock

from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import DebentureRef, MarketPriceSnapshot, SourcedValue
from debenture_search.providers.base import ProviderResult

FONTE = "ANBIMA Feed Preços e Índices"

_TOKEN_URL = "https://api.anbima.com.br/oauth/access-token"
_BASE_URL_PRODUCAO = "https://api.anbima.com.br/feed/precos-indices"
_BASE_URL_SANDBOX = "https://api-sandbox.anbima.com.br/feed/precos-indices"
_RECURSO_MERCADO_SECUNDARIO = "/v1/debentures/mercado-secundario"
_RECURSO_MERCADO_SECUNDARIO_MAIS = "/v1/debentures-mais/mercado-secundario"

# Margem de segurança antes do vencimento real do token, pra nunca usar um
# access_token no instante exato em que expira.
_MARGEM_EXPIRACAO_SEGUNDOS = 60


class AnbimaAPIProvider:
    """MarketDataProvider contra a API paga oficial da ANBIMA.

    Não implementa CharacteristicsProvider: o único endpoint de Debêntures
    confirmado (`mercado-secundario`) não traz dado cadastral, só preço de
    mercado — quem cobre características continua sendo o SND.
    """

    name = FONTE

    def __init__(
        self,
        client_id: str | None,
        client_secret: str | None,
        ambiente: str = "sandbox",
        http_client: RateLimitedHttpClient | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._base_url = _BASE_URL_PRODUCAO if ambiente == "producao" else _BASE_URL_SANDBOX
        # Rate limit modesto: é uma API paga com contrato formal, não
        # scraping de página pública — mas ainda assim não faz sentido
        # martelar sem necessidade.
        self._http = http_client or RateLimitedHttpClient(min_interval_seconds=0.5)
        self._lock = Lock()
        self._access_token: str | None = None
        self._token_expira_monotonic: float = 0.0
        # Uma entrada de cache por recurso (endpoint "normal" e "+"), cada
        # uma com sua própria data de captura — uma chamada de rede por
        # recurso serve qualquer busca feita no mesmo dia.
        self._listas_do_dia: dict[str, tuple[date, list[dict]]] = {}

    def is_available(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def close(self) -> None:
        self._http.close()

    def _obter_access_token(self) -> str:
        with self._lock:
            if self._access_token and time.monotonic() < self._token_expira_monotonic:
                return self._access_token

            credencial = f"{self._client_id}:{self._client_secret}".encode("utf-8")
            auth_basic = base64.b64encode(credencial).decode("ascii")

            resposta = self._http.post(
                _TOKEN_URL,
                json={"grant_type": "client_credentials"},
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Basic {auth_basic}",
                },
            )
            resposta.raise_for_status()
            payload = resposta.json()

            token = payload["access_token"]
            expira_em_segundos = payload.get("expires_in", 3600)
            self._access_token = token
            self._token_expira_monotonic = time.monotonic() + max(
                expira_em_segundos - _MARGEM_EXPIRACAO_SEGUNDOS, 0
            )
            return token

    def _obter_lista_do_dia(self, recurso: str) -> list[dict]:
        hoje = date.today()
        em_cache = self._listas_do_dia.get(recurso)
        if em_cache is not None and em_cache[0] == hoje:
            return em_cache[1]

        token = self._obter_access_token()
        resposta = self._http.get(
            f"{self._base_url}{recurso}",
            headers={"client_id": self._client_id or "", "access_token": token},
        )
        resposta.raise_for_status()
        lista = resposta.json()

        self._listas_do_dia[recurso] = (hoje, lista)
        return lista

    def fetch_market_data(self, ref: DebentureRef) -> ProviderResult[list[MarketPriceSnapshot]]:
        codigo = ref.codigo_ativo
        if not codigo:
            # A API só permite filtrar localmente por codigo_ativo — sem
            # ele (busca só por ISIN ou nome) não há como casar o item da
            # lista do dia com a referência buscada.
            return ProviderResult.ok(self.name, [])

        snapshots: list[MarketPriceSnapshot] = []
        erros: list[str] = []
        # Consulta os dois endpoints (Debêntures normal + Debêntures+
        # incentivadas) — uma debênture só aparece em um dos dois, mas não
        # sabemos qual de antemão. Falha em um não descarta o outro.
        for recurso, categoria in (
            (_RECURSO_MERCADO_SECUNDARIO, ""),
            (_RECURSO_MERCADO_SECUNDARIO_MAIS, "Debêntures+"),
        ):
            try:
                lista = self._obter_lista_do_dia(recurso)
            except Exception as exc:  # noqa: BLE001
                erros.append(str(exc))
                continue
            snapshots.extend(
                _parse_snapshot(ref, item, categoria=categoria)
                for item in lista
                if item.get("codigo_ativo") == codigo
            )

        if not snapshots and erros:
            return ProviderResult.falha(self.name, "; ".join(erros))
        return ProviderResult.ok(self.name, snapshots)


def _parse_snapshot(ref: DebentureRef, item: dict, categoria: str = "") -> MarketPriceSnapshot:
    data_referencia = item.get("data_referencia")
    sufixo = f" {categoria}" if categoria else ""
    fonte = f"{FONTE}{sufixo} (ref. {data_referencia})" if data_referencia else f"{FONTE}{sufixo}"
    return MarketPriceSnapshot(
        debenture_ref=ref,
        periodo_referencia=data_referencia,
        # A API dá um único PU indicativo por dia, não min/médio/máximo de
        # negociações reais (isso é o SND) — mapeado em pu_medio por ser o
        # campo mais próximo semanticamente; min/máximo ficam indisponíveis
        # de propósito, nunca inventados a partir do único valor que existe.
        pu_medio=SourcedValue(item.get("pu"), fonte=fonte),
        taxa_indicativa=SourcedValue(item.get("taxa_indicativa"), fonte=fonte),
        # pu_par (R$) só existe no schema de Debêntures+ (item.get retorna
        # None pro endpoint normal, que só tem o percentual) — nunca
        # calculado a partir de percentual_pu_par.
        pu_par=SourcedValue(item.get("pu_par"), fonte=fonte),
        percentual_pu_par=SourcedValue(item.get("percent_pu_par"), fonte=fonte),
    )
