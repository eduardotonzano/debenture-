"""Testa o BancoCentralDiProvider — sem rede, usando um http_client falso.

O contrato (URL, parâmetros, formato de resposta) é o documentado há mais
de uma década pra API pública do BCB/SGS (não exige credencial) — ver
docstring de providers/bcb_di.py. O que ainda não foi confirmado por uma
chamada real é o mesmo bloqueio de rede deste ambiente que afetou
SND/ANBIMA/CVM ao longo do projeto.
"""

from __future__ import annotations

from datetime import date

from debenture_search.providers.bcb_di import BancoCentralDiProvider


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
    def __init__(self, payload: object) -> None:
        self._payload = payload
        self.chamadas_get: list[dict] = []

    def get(self, url, params=None, headers=None):
        self.chamadas_get.append({"url": url, "params": params, "headers": headers})
        return _FakeResponse(self._payload)

    def close(self) -> None:
        pass


def test_fetch_serie_monta_url_e_parametros_corretos():
    fake = _FakeHttpClient(payload=[{"data": "02/01/2024", "valor": "11.65"}])
    provider = BancoCentralDiProvider(http_client=fake)

    provider.fetch_serie(date(2024, 1, 2), date(2024, 1, 5))

    assert len(fake.chamadas_get) == 1
    chamada = fake.chamadas_get[0]
    assert chamada["url"] == "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
    assert chamada["params"] == {
        "formato": "json",
        "dataInicial": "02/01/2024",
        "dataFinal": "05/01/2024",
    }


def test_fetch_serie_parseia_data_e_valor():
    fake = _FakeHttpClient(
        payload=[
            {"data": "02/01/2024", "valor": "11.65"},
            {"data": "03/01/2024", "valor": "11.66"},
        ]
    )
    provider = BancoCentralDiProvider(http_client=fake)

    serie = provider.fetch_serie(date(2024, 1, 2), date(2024, 1, 3))

    assert serie == [
        (date(2024, 1, 2), 11.65),
        (date(2024, 1, 3), 11.66),
    ]


def test_fetch_serie_ordena_cronologicamente_mesmo_se_api_devolver_fora_de_ordem():
    fake = _FakeHttpClient(
        payload=[
            {"data": "03/01/2024", "valor": "11.66"},
            {"data": "02/01/2024", "valor": "11.65"},
        ]
    )
    provider = BancoCentralDiProvider(http_client=fake)

    serie = provider.fetch_serie(date(2024, 1, 2), date(2024, 1, 3))

    assert [d for d, _ in serie] == [date(2024, 1, 2), date(2024, 1, 3)]


def test_fetch_serie_propaga_erro_http_sem_esconder():
    fake = _FakeHttpClient(payload=[])
    fake_resposta_erro = _FakeResponse(payload=[], status_code=500)

    class _FakeHttpClientErro:
        def get(self, url, params=None, headers=None):
            return fake_resposta_erro

        def close(self):
            pass

    provider = BancoCentralDiProvider(http_client=_FakeHttpClientErro())

    try:
        provider.fetch_serie(date(2024, 1, 2), date(2024, 1, 3))
    except RuntimeError:
        pass
    else:
        raise AssertionError("esperava que o erro HTTP fosse propagado")


def test_is_available_e_sempre_true_api_publica_sem_credencial():
    assert BancoCentralDiProvider(http_client=_FakeHttpClient([])).is_available() is True
