"""CvmDocumentsProvider — Fatos Relevantes via Dados Abertos da CVM (IPE).

Contrato CONFIRMADO por teste real do usuário (o domínio `dados.cvm.gov.br`
está bloqueado neste ambiente de desenvolvimento, mesma restrição de
SND/ANBIMA — mas aqui a fonte é um arquivo estático público, sem scraping,
sem login e sem API interativa):

    https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip

Um ZIP por ano civil, contendo um único CSV com ';' como separador e as
colunas confirmadas por download real (ano 2025, ~19,2 MB):

    CNPJ_Companhia; Nome_Companhia; Codigo_CVM; Data_Referencia; Categoria;
    Tipo; Especie; Assunto; Data_Entrega; Tipo_Apresentacao;
    Protocolo_Entrega; Versao; Link_Download

`Link_Download` aponta pro RAD da CVM
(rad.cvm.gov.br/ENET/frmDownloadDocumento.aspx?...) — testado manualmente
pelo usuário: abre o PDF direto, sem login, documento público.

Cobertura desta fonte: só a categoria "Fato Relevante" (o que foi pedido).
Prospecto e Escritura de Emissão vêm de outro sistema da CVM (documentos de
oferta pública/ANBIMA Input), ainda não investigado — ficam "indisponível",
nunca fabricados. Ver README.

Casamento por CNPJ, nunca por nome: `Nome_Companhia` (texto livre da CVM) e
`nome_emissor` do SND divergem em formatação com frequência (abreviação,
pontuação, acento) — um match por nome arriscaria atribuir o Fato Relevante
de uma empresa errada. Por isso esta fonte só retorna algo quando o
`emissor_cnpj` já foi resolvido por outra fonte de características (SND).

Escopo de anos: cobre o ano corrente e os `_ANOS_COBERTURA - 1` anteriores.
Fatos Relevantes mais antigos que isso ficam de fora deliberadamente — não
escondido, documentado aqui e no README — para não obrigar a primeira busca
de cada sessão a baixar dezenas de arquivos anuais (~15-20 MB cada).
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import DebentureRef, Document, TipoDocumento
from debenture_search.providers.base import ProviderResult

FONTE = "CVM (Dados Abertos IPE)"

_URL_TEMPLATE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip"
_CATEGORIA_ALVO = "Fato Relevante"
_ANOS_COBERTURA = 5
_IDADE_MAX_CACHE_HORAS = 24.0


def _somente_digitos(valor: str) -> str:
    return re.sub(r"\D", "", valor)


class CvmDocumentsProvider:
    name = FONTE

    def __init__(
        self,
        cache_dir: Path,
        http_client: RateLimitedHttpClient | None = None,
    ) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Timeout maior que o padrão: arquivo estático de ~15-20 MB, não
        # scraping de página — não precisa de rate limit agressivo, mas
        # ainda assim não bate a esmo (cache de 24h por ano baixado).
        self._http = http_client or RateLimitedHttpClient(min_interval_seconds=1.0, timeout_seconds=60.0)

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        self._http.close()

    def fetch_documents(
        self, ref: DebentureRef, emissor_cnpj: str | None
    ) -> ProviderResult[list[Document]]:
        if not emissor_cnpj:
            # Sem CNPJ resolvido por outra fonte, não arrisca casar por
            # nome — fica "indisponível" de propósito.
            return ProviderResult.ok(self.name, [])

        cnpj_alvo = _somente_digitos(emissor_cnpj)
        ano_atual = date.today().year
        documentos: list[Document] = []
        try:
            for ano in range(ano_atual, ano_atual - _ANOS_COBERTURA, -1):
                caminho_csv = self._obter_csv_do_ano(ano)
                if caminho_csv is None:
                    continue
                documentos.extend(self._filtrar_fatos_relevantes(caminho_csv, cnpj_alvo, ref))
            return ProviderResult.ok(self.name, documentos)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def _obter_csv_do_ano(self, ano: int) -> Path | None:
        destino = self._cache_dir / f"cvm_ipe_{ano}.csv"
        if destino.exists():
            idade_horas = (datetime.now().timestamp() - destino.stat().st_mtime) / 3600
            if idade_horas < _IDADE_MAX_CACHE_HORAS:
                return destino

        resposta = self._http.get(_URL_TEMPLATE.format(ano=ano))
        if resposta.status_code == 404:
            # Ano sem arquivo publicado (ex.: ano corrente muito no início,
            # ou ano futuro) — não é falha, usa cache velho se existir.
            return destino if destino.exists() else None
        resposta.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resposta.content)) as zf:
            nomes_csv = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not nomes_csv:
                raise ValueError(f"ZIP da CVM pro ano {ano} não contém nenhum .csv")
            conteudo = zf.read(nomes_csv[0])

        destino.write_bytes(conteudo)
        return destino

    def _filtrar_fatos_relevantes(
        self, caminho_csv: Path, cnpj_alvo: str, ref: DebentureRef
    ) -> list[Document]:
        texto = _ler_texto_tolerante(caminho_csv)
        leitor = csv.DictReader(io.StringIO(texto), delimiter=";")
        encontrados: list[Document] = []
        for linha in leitor:
            cnpj_linha = _somente_digitos(linha.get("CNPJ_Companhia") or "")
            if cnpj_linha != cnpj_alvo:
                continue
            if (linha.get("Categoria") or "").strip() != _CATEGORIA_ALVO:
                continue
            url = (linha.get("Link_Download") or "").strip()
            if not url:
                continue
            encontrados.append(
                Document(
                    tipo=TipoDocumento.FATO_RELEVANTE,
                    url=url,
                    data_publicacao=_parse_data_iso(linha.get("Data_Entrega")),
                    descricao=(linha.get("Assunto") or "").strip() or None,
                    fonte=FONTE,
                    debenture_ref=ref,
                )
            )
        return encontrados


def _ler_texto_tolerante(caminho: Path) -> str:
    """CSVs de portais de governo brasileiros oscilam entre UTF-8 e
    Latin-1 — tenta na ordem, sem travar a busca por causa de acentuação."""
    dados = caminho.read_bytes()
    for codificacao in ("utf-8-sig", "latin-1"):
        try:
            return dados.decode(codificacao)
        except UnicodeDecodeError:
            continue
    return dados.decode("utf-8", errors="replace")


def _parse_data_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None
