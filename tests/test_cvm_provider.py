"""Testa o CvmDocumentsProvider — sem rede, usando um ZIP sintético montado
em memória com o CABEÇALHO REAL confirmado (baixado de verdade pelo usuário
em dados.cvm.gov.br) e linhas de dado inventadas só pra exercitar o filtro.

Ver docstring de providers/cvm.py pro contrato completo.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

from debenture_search.models import DebentureRef, TipoDocumento
from debenture_search.providers.cvm import CvmDocumentsProvider

REF = DebentureRef(isin="BRBODYDBS000", codigo_ativo="BODY12", nome_emissor="A Bodytech")

CSV_HEADER = (
    "CNPJ_Companhia;Nome_Companhia;Codigo_CVM;Data_Referencia;Categoria;Tipo;"
    "Especie;Assunto;Data_Entrega;Tipo_Apresentacao;Protocolo_Entrega;Versao;"
    "Link_Download"
)


def _linha_csv(
    cnpj="07.737.623/0001-90",
    nome="A BODYTECH PARTICIPACOES S.A.",
    categoria="Fato Relevante",
    assunto="Aviso aos Debenturistas",
    data_entrega="2025-03-10",
    link="https://www.rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?numSequencia=1",
) -> str:
    return ";".join(
        [
            cnpj, nome, "27030", "2025-03-10", categoria, "Comunicado", "",
            assunto, data_entrega, "AP - Apresentação", "PROTOCOLO123", "1", link,
        ]
    )


class _FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200) -> None:
        self.content = content
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"status {self.status_code}")


class _FakeHttpClient:
    """Só o ano corrente tem as linhas de teste — os outros anos da
    cobertura de 5 anos voltam um CSV vazio (só cabeçalho), simulando o
    caso real onde cada documento aparece em exatamente um arquivo anual
    (mas o arquivo do ano em si sempre existe, o que é o caso normal)."""

    def __init__(self, zip_bytes: bytes) -> None:
        self._zip_bytes = zip_bytes
        self.chamadas: list[str] = []

    def get(self, url, params=None, headers=None):
        self.chamadas.append(url)
        if str(date.today().year) in url:
            return _FakeResponse(self._zip_bytes)
        return _FakeResponse(_zip_com_linhas())

    def close(self) -> None:
        pass


def _zip_com_linhas(*linhas: str) -> bytes:
    csv_texto = "\n".join([CSV_HEADER, *linhas])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ipe_cia_aberta_2025.csv", csv_texto)
    return buf.getvalue()


def _provider(tmp_path: Path, zip_bytes: bytes) -> CvmDocumentsProvider:
    fake_http = _FakeHttpClient(zip_bytes)
    return CvmDocumentsProvider(cache_dir=tmp_path / "cvm_cache", http_client=fake_http)


def test_sem_cnpj_nao_bate_rede(tmp_path) -> None:
    provider = _provider(tmp_path, _zip_com_linhas(_linha_csv()))
    resultado = provider.fetch_documents(REF, emissor_cnpj=None)
    assert resultado.sucesso
    assert resultado.valor == []
    assert provider._http.chamadas == []


def test_filtra_por_cnpj_e_categoria_fato_relevante(tmp_path) -> None:
    zip_bytes = _zip_com_linhas(
        _linha_csv(cnpj="07.737.623/0001-90", categoria="Fato Relevante", assunto="Aviso aos Debenturistas"),
        _linha_csv(cnpj="07.737.623/0001-90", categoria="Assembleia", assunto="Ata de AGE"),
        _linha_csv(cnpj="11.111.111/0001-11", categoria="Fato Relevante", assunto="De outra empresa"),
    )
    provider = _provider(tmp_path, zip_bytes)

    resultado = provider.fetch_documents(REF, emissor_cnpj="07737623000190")

    assert resultado.sucesso
    assert len(resultado.valor) == 1
    doc = resultado.valor[0]
    assert doc.tipo == TipoDocumento.FATO_RELEVANTE
    assert doc.descricao == "Aviso aos Debenturistas"
    assert doc.data_publicacao.isoformat() == "2025-03-10"
    assert doc.url.startswith("https://www.rad.cvm.gov.br/")
    assert doc.fonte == "CVM (Dados Abertos IPE)"


def test_cnpj_com_ou_sem_pontuacao_casa_igual(tmp_path) -> None:
    zip_bytes = _zip_com_linhas(_linha_csv(cnpj="07.737.623/0001-90"))
    provider = _provider(tmp_path, zip_bytes)

    resultado = provider.fetch_documents(REF, emissor_cnpj="07737623000190")

    assert len(resultado.valor) == 1


def test_usa_csv_em_cache_sem_nova_chamada_de_rede(tmp_path) -> None:
    zip_bytes = _zip_com_linhas(_linha_csv())
    provider = _provider(tmp_path, zip_bytes)

    provider.fetch_documents(REF, emissor_cnpj="07737623000190")
    # 5 anos de cobertura -> 5 chamadas na primeira busca (todas cacheadas
    # em disco), e nenhuma chamada nova na segunda busca.
    assert len(provider._http.chamadas) == 5
    provider.fetch_documents(REF, emissor_cnpj="07737623000190")
    assert len(provider._http.chamadas) == 5


def test_falha_de_rede_vira_resultado_falho_sem_excecao(tmp_path) -> None:
    class _HttpQueFalha:
        def get(self, *args, **kwargs):
            raise RuntimeError("rede indisponível")

        def close(self) -> None:
            pass

    provider = CvmDocumentsProvider(cache_dir=tmp_path / "cvm_cache", http_client=_HttpQueFalha())

    resultado = provider.fetch_documents(REF, emissor_cnpj="07737623000190")

    assert resultado.sucesso is False
    assert "rede indisponível" in resultado.erro
