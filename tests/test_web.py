"""Testa a UI web (Fase 2) via TestClient, sem rede — o aggregator é
injetado com providers fake (mesmo padrão de test_aggregator.py), exceto
nos testes de input manual, que usam o ManualInputProvider real para
validar que o dado colado na tela realmente aparece na ficha com
precedência sobre a fonte automática.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from debenture_search.aggregator import DebentureAggregator
from debenture_search.models import Debenture, DebentureRef, MarketPriceSnapshot, SearchQuery, SourcedValue
from debenture_search.providers.base import ProviderResult
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.web import create_app

REF = DebentureRef(isin="BRBODYDBS000", codigo_ativo="BODY12", nome_emissor="A Bodytech Participações")


class FakeSearchProvider:
    name = "fake-search"

    def __init__(self, refs: list[DebentureRef]) -> None:
        self._refs = refs

    def is_available(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> list[DebentureRef]:
        return self._refs


class FakeCharacteristicsProvider:
    name = "fake-char"

    def is_available(self) -> bool:
        return True

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        deb = Debenture(
            isin=SourcedValue(ref.isin, fonte="SND"),
            codigo_ativo=SourcedValue(ref.codigo_ativo, fonte="SND"),
            emissor_nome=SourcedValue(ref.nome_emissor, fonte="SND"),
            indexador=SourcedValue("DI", fonte="SND"),
            taxa=SourcedValue("DI + 4,3500%", fonte="SND"),
            situacao=SourcedValue("Registrado", fonte="SND"),
        )
        return ProviderResult.ok(self.name, deb)


class FailingSearchProvider:
    name = "failing-search"

    def is_available(self) -> bool:
        return True

    def search(self, query: SearchQuery) -> list[DebentureRef]:
        raise ConnectionError("SND indisponível (simulado)")


def _client_com_refs(refs: list[DebentureRef]) -> TestClient:
    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider(refs)],
            characteristics_providers=[FakeCharacteristicsProvider()],
        )

    return TestClient(create_app(aggregator_factory=factory))


def test_index_renderiza_formulario_de_busca() -> None:
    client = _client_com_refs([])
    r = client.get("/")
    assert r.status_code == 200
    assert "Buscar debênture" in r.text


def test_busca_redireciona_para_ficha_quando_resultado_unico() -> None:
    client = _client_com_refs([REF])
    r = client.get("/busca", params={"q": "BODY12"}, follow_redirects=False)
    assert r.status_code == 303
    assert "/ficha?" in r.headers["location"]
    assert "codigo_ativo=BODY12" in r.headers["location"]


def test_busca_mostra_desambiguacao_com_multiplas_series() -> None:
    outro = DebentureRef(isin="BRBODYDBS999", codigo_ativo="BODY13", nome_emissor="A Bodytech Participações")
    client = _client_com_refs([REF, outro])
    r = client.get("/busca", params={"q": "Bodytech"})
    assert r.status_code == 200
    assert "Mais de uma série encontrada" in r.text
    assert "BODY12" in r.text
    assert "BODY13" in r.text


def test_busca_sem_resultado_mostra_nao_encontrado() -> None:
    client = _client_com_refs([])
    r = client.get("/busca", params={"q": "NADAAQUI"})
    assert r.status_code == 200
    assert "Nada encontrado" in r.text


def test_busca_com_falha_de_fonte_mostra_erro_sem_inventar_dado() -> None:
    def factory() -> DebentureAggregator:
        return DebentureAggregator(search_providers=[FailingSearchProvider()], characteristics_providers=[])

    client = TestClient(create_app(aggregator_factory=factory))
    r = client.get("/busca", params={"q": "BODY12"})
    assert r.status_code == 200
    assert "Não foi possível concluir a busca" in r.text
    assert "SND indisponível" in r.text


def test_ficha_mostra_campos_disponiveis_e_indisponiveis() -> None:
    client = _client_com_refs([REF])
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})
    assert r.status_code == 200
    assert "DI + 4,3500%" in r.text
    assert "Registrado" in r.text
    # rating não veio de nenhuma fonte nesse fake -> tem que aparecer como indisponível
    assert "indisponível" in r.text
    assert "Nenhum dado de negociação disponível" in r.text
    assert "Nenhum evento futuro disponível" in r.text
    assert "Nenhum documento disponível" in r.text


def test_manual_form_renderiza_campos_suportados() -> None:
    client = _client_com_refs([REF])
    r = client.get("/manual", params={"codigo_ativo": "BODY12"})
    assert r.status_code == 200
    assert "Rating" in r.text
    assert "BODY12" in r.text


def test_manual_submit_persiste_e_ficha_reflete_override(tmp_path, monkeypatch) -> None:
    manual_db = tmp_path / "manual.sqlite3"
    monkeypatch.setattr("debenture_search.web.MANUAL_INPUT_DB_PATH", manual_db)

    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider([REF])],
            # Manual por último -> maior precedência, igual em compose.py
            characteristics_providers=[FakeCharacteristicsProvider(), ManualInputProvider(manual_db)],
        )

    client = TestClient(create_app(aggregator_factory=factory))

    r = client.post(
        "/manual",
        data={"codigo_ativo": "BODY12", "campo": "rating", "valor": "AA-", "fonte": "Fitch, 03/2026"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})
    assert "AA-" in r.text
    assert "Fitch, 03/2026" in r.text
    # e o dado automático que a fonte manual não sobrescreveu continua lá
    assert "DI + 4,3500%" in r.text
