"""Testa o AnbimaAPIProvider — sem rede, usando um http_client falso.

O fluxo de autenticação (Basic auth em base64 -> access_token) e o formato
da URL/headers de dado vêm confirmados de fontes reais do portal ANBIMA
Developers (página de texto "Autenticação" + Swagger real), capturadas via
HAR pelo usuário — ver docstring de providers/anbima_api.py. O que ainda
não foi confirmado é o conteúdo de uma resposta real do endpoint de
mercado secundário (o domínio está bloqueado neste ambiente de
desenvolvimento) — por isso a fixture
`anbima_api_mercado_secundario_sintetico.json` usa nomes/tipos de campo
reais do schema, mas valores inventados.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from debenture_search.models import DebentureRef
from debenture_search.providers.anbima_api import AnbimaAPIProvider

FIXTURES = Path(__file__).parent / "fixtures"


class _FakeResponse:
    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")

    def json(self) -> object:
        return self._payload


class _FakeHttpClient:
    """`lista_mais_payload` fica vazia por padrão — a maioria dos testes só
    se importa com o endpoint "normal"; o teste dedicado ao endpoint
    "Debêntures+" passa uma lista própria."""

    def __init__(
        self, token_payload: dict, lista_payload: object, lista_mais_payload: object = ()
    ) -> None:
        self._token_payload = token_payload
        self._lista_payload = lista_payload
        self._lista_mais_payload = list(lista_mais_payload)
        self.chamadas_post: list[dict] = []
        self.chamadas_get: list[dict] = []

    def post(self, url, data=None, json=None, headers=None):  # noqa: A002 (nome do parâmetro segue http_client real)
        self.chamadas_post.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(self._token_payload)

    def get(self, url, params=None, headers=None):
        self.chamadas_get.append({"url": url, "params": params, "headers": headers})
        if "debentures-mais" in url:
            return _FakeResponse(self._lista_mais_payload)
        return _FakeResponse(self._lista_payload)

    def close(self) -> None:
        pass


def _lista_sintetica() -> list[dict]:
    return json.loads(
        (FIXTURES / "anbima_api_mercado_secundario_sintetico.json").read_text(encoding="utf-8")
    )


def _provider(fake_http: _FakeHttpClient) -> AnbimaAPIProvider:
    return AnbimaAPIProvider(
        client_id="client-teste",
        client_secret="secret-teste",
        http_client=fake_http,
    )


def test_indisponivel_sem_credencial() -> None:
    provider = AnbimaAPIProvider(client_id=None, client_secret=None)
    assert provider.is_available() is False


def test_indisponivel_com_credencial_parcial() -> None:
    assert AnbimaAPIProvider(client_id="x", client_secret=None).is_available() is False
    assert AnbimaAPIProvider(client_id=None, client_secret="y").is_available() is False


def test_disponivel_com_credencial_completa() -> None:
    provider = AnbimaAPIProvider(client_id="x", client_secret="y")
    assert provider.is_available() is True


def test_token_usa_basic_auth_e_grant_type_client_credentials() -> None:
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-123", "token_type": "access_token", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    resultado = provider.fetch_market_data(
        DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor="")
    )

    assert resultado.sucesso
    assert len(fake_http.chamadas_post) == 1
    chamada = fake_http.chamadas_post[0]
    assert chamada["url"] == "https://api.anbima.com.br/oauth/access-token"
    assert chamada["json"] == {"grant_type": "client_credentials"}
    esperado_basic = base64.b64encode(b"client-teste:secret-teste").decode("ascii")
    assert chamada["headers"]["Authorization"] == f"Basic {esperado_basic}"
    assert chamada["headers"]["Content-Type"] == "application/json"


def test_chamada_de_dados_usa_headers_client_id_e_access_token() -> None:
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    provider.fetch_market_data(DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor=""))

    # Um GET pro endpoint normal e outro pro "+" (Debêntures incentivadas)
    # — a busca consulta os dois, já que não dá pra saber de antemão em
    # qual lista o ativo está.
    assert len(fake_http.chamadas_get) == 2
    urls = {c["url"] for c in fake_http.chamadas_get}
    assert any(u.endswith("/v1/debentures/mercado-secundario") for u in urls)
    assert any(u.endswith("/v1/debentures-mais/mercado-secundario") for u in urls)
    for chamada in fake_http.chamadas_get:
        assert chamada["headers"] == {"client_id": "client-teste", "access_token": "tok-abc"}


def test_fetch_market_data_filtra_pelo_codigo_ativo_buscado() -> None:
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    resultado = provider.fetch_market_data(
        DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor="")
    )

    assert resultado.sucesso
    assert len(resultado.valor) == 1
    snapshot = resultado.valor[0]
    assert snapshot.pu_medio.valor == 1048.987654
    assert snapshot.taxa_indicativa.valor == 12.3456
    assert snapshot.periodo_referencia == "2026-08-24"
    assert "2026-08-24" in snapshot.pu_medio.fonte
    # min/máximo não existem na resposta da ANBIMA (só um PU indicativo por
    # dia) — nunca devem ser inventados a partir do único valor disponível.
    assert snapshot.pu_minimo.disponivel is False
    assert snapshot.pu_maximo.disponivel is False


def test_fetch_market_data_encontra_no_endpoint_debentures_mais() -> None:
    """Debêntures incentivadas (Lei 12.431) só aparecem em
    /v1/debentures-mais/mercado-secundario, nunca no endpoint normal."""
    item_mais = {
        "codigo_ativo": "INCT11",
        "data_referencia": "2026-08-24",
        "pu": 1500.50,
        "taxa_indicativa": 6.789,
    }
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),  # não tem INCT11
        lista_mais_payload=[item_mais],
    )
    provider = _provider(fake_http)

    resultado = provider.fetch_market_data(
        DebentureRef(isin=None, codigo_ativo="INCT11", nome_emissor="")
    )

    assert resultado.sucesso
    assert len(resultado.valor) == 1
    snapshot = resultado.valor[0]
    assert snapshot.pu_medio.valor == 1500.50
    assert "Debêntures+" in snapshot.pu_medio.fonte


def test_fetch_market_data_falha_num_endpoint_nao_descarta_o_outro() -> None:
    class _HttpFalhaSoNoMais:
        def post(self, url, data=None, json=None, headers=None):
            return _FakeResponse({"access_token": "tok-abc", "expires_in": 3600})

        def get(self, url, params=None, headers=None):
            if "debentures-mais" in url:
                raise RuntimeError("endpoint + fora do ar")
            return _FakeResponse(_lista_sintetica())

        def close(self) -> None:
            pass

    provider = AnbimaAPIProvider(
        client_id="client-teste", client_secret="secret-teste", http_client=_HttpFalhaSoNoMais()
    )

    resultado = provider.fetch_market_data(
        DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor="")
    )

    assert resultado.sucesso
    assert len(resultado.valor) == 1


def test_fetch_market_data_sem_codigo_ativo_nao_bate_rede() -> None:
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    resultado = provider.fetch_market_data(
        DebentureRef(isin="BRTESTDBS001", codigo_ativo=None, nome_emissor="")
    )

    assert resultado.sucesso
    assert resultado.valor == []
    assert fake_http.chamadas_get == []
    assert fake_http.chamadas_post == []


def test_reutiliza_lista_do_dia_e_token_entre_buscas() -> None:
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    provider.fetch_market_data(DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor=""))
    provider.fetch_market_data(DebentureRef(isin=None, codigo_ativo="OUTR11", nome_emissor=""))

    assert len(fake_http.chamadas_post) == 1
    # 2 chamadas na primeira busca (normal + "+"), 0 chamadas novas na
    # segunda — tudo já em cache do dia.
    assert len(fake_http.chamadas_get) == 2


def test_token_expirado_dispara_nova_chamada_de_token() -> None:
    # Testado direto em _obter_access_token: via fetch_market_data o cache
    # da lista do dia evitaria qualquer nova chamada de rede (nem chegaria
    # a checar o token de novo), então isso teria que ser um teste do
    # comportamento de token isoladamente.
    fake_http = _FakeHttpClient(
        token_payload={"access_token": "tok-abc", "expires_in": 3600},
        lista_payload=_lista_sintetica(),
    )
    provider = _provider(fake_http)

    provider._obter_access_token()
    provider._token_expira_monotonic = 0.0  # força expiração
    provider._obter_access_token()

    assert len(fake_http.chamadas_post) == 2


def test_falha_de_rede_vira_resultado_falho_sem_excecao() -> None:
    class _HttpQueFalha:
        def post(self, *args, **kwargs):
            raise RuntimeError("rede indisponível")

        def close(self) -> None:
            pass

    provider = AnbimaAPIProvider(
        client_id="client-teste", client_secret="secret-teste", http_client=_HttpQueFalha()
    )

    resultado = provider.fetch_market_data(
        DebentureRef(isin=None, codigo_ativo="TEST12", nome_emissor="")
    )

    assert resultado.sucesso is False
    assert "rede indisponível" in resultado.erro
