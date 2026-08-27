"""Testa a UI web (Fase 2) via TestClient, sem rede — o aggregator é
injetado com providers fake (mesmo padrão de test_aggregator.py), exceto
nos testes de input manual, que usam o ManualInputProvider real para
validar que o dado colado na tela realmente aparece na ficha com
precedência sobre a fonte automática.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from datetime import date

from debenture_search.aggregator import DebentureAggregator
from debenture_search.models import (
    Debenture,
    DebentureRef,
    Document,
    Event,
    MarketPriceSnapshot,
    SearchQuery,
    SourcedValue,
    TipoDocumento,
    TipoEvento,
)
from debenture_search.providers.base import ProviderResult
from debenture_search.providers.manual import ManualInputProvider
from debenture_search.web import _grafico_eventos_json, _grafico_precos_json, create_app

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


class FakeDocumentsProvider:
    name = "fake-docs"

    def is_available(self) -> bool:
        return True

    def fetch_documents(self, ref: DebentureRef, emissor_cnpj: str | None) -> ProviderResult[list[Document]]:
        docs = [
            Document(
                tipo=TipoDocumento.FATO_RELEVANTE,
                url="https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?x=1",
                data_publicacao=date(2025, 3, 10),
                descricao="Aviso aos Debenturistas",
                fonte="CVM (Dados Abertos IPE)",
                debenture_ref=ref,
            )
        ]
        return ProviderResult.ok(self.name, docs)


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
    assert "Nenhum evento disponível" in r.text
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


def test_sem_env_vars_de_auth_ui_fica_aberta(monkeypatch) -> None:
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_USERNAME", None)
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_PASSWORD", None)
    client = _client_com_refs([])
    r = client.get("/")
    assert r.status_code == 200


def test_com_env_vars_de_auth_bloqueia_sem_credencial(monkeypatch) -> None:
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_USERNAME", "admin")
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_PASSWORD", "segredo")
    client = _client_com_refs([])
    r = client.get("/")
    assert r.status_code == 401


def test_com_env_vars_de_auth_bloqueia_credencial_errada(monkeypatch) -> None:
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_USERNAME", "admin")
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_PASSWORD", "segredo")
    client = _client_com_refs([])
    r = client.get("/", auth=("admin", "senha-errada"))
    assert r.status_code == 401


def test_com_env_vars_de_auth_libera_credencial_correta(monkeypatch) -> None:
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_USERNAME", "admin")
    monkeypatch.setattr("debenture_search.config.WEB_AUTH_PASSWORD", "segredo")
    client = _client_com_refs([])
    r = client.get("/", auth=("admin", "segredo"))
    assert r.status_code == 200


def test_ficha_mostra_documentos_cvm_com_rotulo_e_descricao() -> None:
    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider([REF])],
            characteristics_providers=[FakeCharacteristicsProvider()],
            documents_providers=[FakeDocumentsProvider()],
        )

    client = TestClient(create_app(aggregator_factory=factory))
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})

    assert r.status_code == 200
    assert "Fato Relevante" in r.text
    assert "Aviso aos Debenturistas" in r.text
    assert "10/03/2025" in r.text


def test_grafico_precos_json_separa_snd_e_anbima_por_data() -> None:
    import json

    precos = [
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="10/03/2025",
            pu_medio=SourcedValue(1000.0, fonte="SND"),
        ),
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="2025-03-11",
            pu_medio=SourcedValue(1050.0, fonte="ANBIMA Feed Preços e Índices (ref. 2025-03-11)"),
        ),
        # sem pu_medio disponível -> ignorado, nunca vira um ponto fantasma
        MarketPriceSnapshot(debenture_ref=REF, periodo_referencia="12/03/2025"),
    ]

    dados = json.loads(_grafico_precos_json(precos))

    assert dados["labels"] == ["10/03/2025", "11/03/2025"]
    assert dados["dates_iso"] == ["2025-03-10", "2025-03-11"]
    assert dados["snd"] == [1000.0, None]
    assert dados["anbima"] == [None, 1050.0]
    assert dados["pu_par"] == [None, None]


def test_grafico_precos_json_inclui_pu_par_quando_disponivel() -> None:
    import json

    precos = [
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="10/03/2025",
            pu_medio=SourcedValue(1000.0, fonte="SND"),
            pu_par=SourcedValue(987.65, fonte="Calculado (fórmula ANBIMA seção 25.4.1 + BCB SGS série 12)"),
        ),
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="11/03/2025",
            pu_medio=SourcedValue(1010.0, fonte="SND"),
            # sem pu_par disponível nesta data -> null, nunca interpolado
        ),
    ]

    dados = json.loads(_grafico_precos_json(precos))

    assert dados["pu_par"] == [987.65, None]


def test_grafico_precos_json_vazio_sem_precos() -> None:
    import json

    assert json.loads(_grafico_precos_json([])) == {
        "labels": [], "dates_iso": [], "snd": [], "anbima": [], "pu_par": [],
    }


def test_ficha_com_precos_renderiza_grafico() -> None:
    class FakeMarketData:
        name = "fake-market"

        def is_available(self):
            return True

        def fetch_market_data(self, ref):
            snaps = [
                MarketPriceSnapshot(
                    debenture_ref=ref, periodo_referencia="10/03/2025",
                    pu_medio=SourcedValue(1000.0, fonte="SND"),
                )
            ]
            return ProviderResult.ok(self.name, snaps)

    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider([REF])],
            characteristics_providers=[FakeCharacteristicsProvider()],
            market_data_providers=[FakeMarketData()],
        )

    client = TestClient(create_app(aggregator_factory=factory))
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})

    assert r.status_code == 200
    assert 'id="grafico-precos"' in r.text
    assert "10/03/2025" in r.text
    assert "/static/vendor/chart.umd.min.js" in r.text


def test_ficha_com_pu_par_mostra_terceira_linha_no_grafico() -> None:
    class FakeMarketData:
        name = "fake-market"

        def is_available(self):
            return True

        def fetch_market_data(self, ref):
            snaps = [
                MarketPriceSnapshot(
                    debenture_ref=ref, periodo_referencia="10/03/2025",
                    pu_medio=SourcedValue(1000.0, fonte="SND"),
                    pu_par=SourcedValue(987.65, fonte="Calculado (fórmula ANBIMA seção 25.4.1 + BCB SGS série 12)"),
                )
            ]
            return ProviderResult.ok(self.name, snaps)

    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider([REF])],
            characteristics_providers=[FakeCharacteristicsProvider()],
            market_data_providers=[FakeMarketData()],
        )

    client = TestClient(create_app(aggregator_factory=factory))
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})

    assert r.status_code == 200
    assert "987.65" in r.text
    assert "PU Par" in r.text


def test_ficha_sem_precos_nao_quebra_grafico() -> None:
    client = _client_com_refs([REF])
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})

    assert r.status_code == 200
    assert "Nenhum dado de negociação disponível." in r.text


def test_grafico_eventos_json_ancora_no_preco_mais_proximo() -> None:
    import json

    precos = [
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="10/03/2025",
            pu_medio=SourcedValue(1000.0, fonte="SND"),
        ),
        MarketPriceSnapshot(
            debenture_ref=REF, periodo_referencia="20/03/2025",
            pu_medio=SourcedValue(1020.0, fonte="SND"),
        ),
    ]
    eventos = [
        Event(debenture_ref=REF, tipo=TipoEvento.REPACTUACAO, data_prevista=date(2025, 3, 12)),
    ]

    marcadores = json.loads(_grafico_eventos_json(eventos, precos))

    assert len(marcadores) == 1
    assert marcadores[0]["label_ancora"] == "10/03/2025"  # 12/03 está mais perto de 10/03 que de 20/03
    assert marcadores[0]["y"] == 1000.0
    assert marcadores[0]["data_evento"] == "12/03/2025"
    assert marcadores[0]["tipo"] == "Repactuação"


def test_grafico_eventos_json_sem_precos_fica_vazio() -> None:
    import json

    eventos = [Event(debenture_ref=REF, tipo=TipoEvento.REPACTUACAO, data_prevista=date(2025, 3, 12))]

    assert json.loads(_grafico_eventos_json(eventos, [])) == []


def test_ficha_com_eventos_e_precos_inclui_marcador_no_grafico() -> None:
    class FakeMarketData:
        name = "fake-market"

        def is_available(self):
            return True

        def fetch_market_data(self, ref):
            return ProviderResult.ok(
                self.name,
                [
                    MarketPriceSnapshot(
                        debenture_ref=ref, periodo_referencia="10/03/2025",
                        pu_medio=SourcedValue(1000.0, fonte="SND"),
                    )
                ],
            )

    class FakeEvents:
        name = "fake-events"

        def is_available(self):
            return True

        def fetch_events(self, ref):
            return ProviderResult.ok(
                self.name,
                [Event(debenture_ref=ref, tipo=TipoEvento.REPACTUACAO, data_prevista=date(2025, 3, 12))],
            )

    def factory() -> DebentureAggregator:
        return DebentureAggregator(
            search_providers=[FakeSearchProvider([REF])],
            characteristics_providers=[FakeCharacteristicsProvider()],
            market_data_providers=[FakeMarketData()],
            events_providers=[FakeEvents()],
        )

    client = TestClient(create_app(aggregator_factory=factory))
    r = client.get("/ficha", params={"codigo_ativo": "BODY12"})

    assert r.status_code == 200
    assert '"tipo": "Repactua' in r.text
    assert "12/03/2025" in r.text
    assert "chartjs-plugin-zoom.min.js" in r.text
