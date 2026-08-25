"""Provider da API oficial paga da ANBIMA ("ANBIMA Developers API").

STATUS: NÃO implementado contra a API real — o usuário ainda não tem
credencial, e mesmo que tivesse, o domínio da documentação
(`developers.anbima.com.br`, `api.anbima.com.br`) está bloqueado pela
política de rede deste ambiente (mesmo bloqueio que afetou o SND e o
ANBIMA Data — ver README). Este módulo é a contraparte oficial e paga
daquele portal: diferente do ANBIMA Data público (que tem API interna
protegida por reCAPTCHA e que decidimos, com o usuário, não automatizar —
ver seção "Decisão" no README), esta é uma API comercial com contrato
formal; não há problema em integrá-la quando a credencial existir.

O que este arquivo é, hoje: a interface pronta pra plugar (`is_available()`
retorna `False` sem `ANBIMA_API_KEY`, e o `DebentureAggregator` já ignora
providers indisponíveis — ver `aggregator.py`), com TUDO que depende do
contrato real da API (URL base, forma de autenticação, nomes de campo no
JSON de resposta) marcado como placeholder e isolado em `_Endpoints` e nas
funções `_parse_*`, exatamente como fizemos com o SND antes de termos
acesso a HTML real (ver histórico do projeto).

Quando a credencial existir, os passos são:
  1. Abrir a documentação oficial da ANBIMA Developers API (fora deste
     ambiente) e confirmar: URL base, forma de autenticação (a hipótese
     aqui é passar a api_key direto como Bearer token — se a API real
     exigir OAuth2 client_credentials, com troca prévia de
     client_id/client_secret por um token de curta duração, é preciso
     adicionar essa etapa antes da chamada em `fetch_characteristics`),
     e o endpoint de características de debêntures.
  2. Ajustar `_Endpoints` e o header de autenticação em
     `fetch_characteristics`.
  3. Substituir a fixture sintética em `tests/fixtures/anbima_api_*.json`
     por uma resposta real (com dados sensíveis/de conta removidos) e
     ajustar `_parse_caracteristicas_json` até os testes passarem com o
     schema real.

O mapeamento de campos abaixo é uma HIPÓTESE razoável (inspirada na
estrutura de dados que a ANBIMA usa no seu próprio site, observada
incidentalmente durante a investigação do ANBIMA Data — não é o schema
confirmado da API paga, que é um produto tecnicamente separado).
"""

from __future__ import annotations

from datetime import date, datetime

from debenture_search.http_client import RateLimitedHttpClient
from debenture_search.models import Debenture, DebentureRef, SourcedValue
from debenture_search.providers.base import ProviderResult

FONTE = "ANBIMA API"


class _Endpoints:
    """TODO(confirmar): nenhuma destas URLs foi verificada contra a
    documentação real — são hipóteses baseadas em padrões comuns de API
    financeira B2B brasileira, a confirmar quando houver credencial."""

    BASE = "https://api.anbima.com.br"
    DEBENTURES_CARACTERISTICAS = f"{BASE}/feed/precos-indices/v1/titulos-privados/debentures"


class AnbimaAPIProvider:
    """Implementa CharacteristicsProvider. Fica indisponível (e o
    aggregator a ignora) sem `api_key` configurada — nunca derruba o
    resto do sistema."""

    name = FONTE

    def __init__(self, api_key: str | None, http_client: RateLimitedHttpClient | None = None) -> None:
        self._api_key = api_key
        # API paga não precisa do rate limit conservador do scraping do
        # SND — mas ainda vale não abrir requisições em paralelo.
        self._http = http_client or RateLimitedHttpClient(min_interval_seconds=0.3)

    def is_available(self) -> bool:
        return bool(self._api_key)

    def fetch_characteristics(self, ref: DebentureRef) -> ProviderResult[Debenture]:
        codigo = ref.codigo_ativo or ref.isin
        if not codigo:
            return ProviderResult.ok(self.name, Debenture())
        try:
            response = self._http.get(
                _Endpoints.DEBENTURES_CARACTERISTICAS,
                params={"codigo": codigo},  # TODO(confirmar): nome real do parâmetro de busca
                # TODO(confirmar): forma real de autenticação — hipótese de
                # Bearer token direto com a api_key; se a API real usar
                # OAuth2 client_credentials (client_id + client_secret
                # trocados por um token de curta duração), isso precisa de
                # uma etapa de autenticação prévia antes desta chamada.
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            response.raise_for_status()
            debenture = _parse_caracteristicas_json(response.json())
            return ProviderResult.ok(self.name, debenture)
        except Exception as exc:  # noqa: BLE001
            return ProviderResult.falha(self.name, str(exc))

    def close(self) -> None:
        self._http.close()


def _parse_caracteristicas_json(payload: dict) -> Debenture:  # type: ignore[type-arg]
    """TODO(confirmar): mapeamento de campos não verificado contra a API
    paga real — ver docstring do módulo. `payload.get(...)` em vez de
    acesso direto porque não sabemos ainda quais campos são garantidos."""
    emissao = payload.get("emissao", {}) or {}
    emissor = emissao.get("emissor", {}) or {}
    indexador = payload.get("indexador", {}) or {}

    return Debenture(
        isin=SourcedValue(payload.get("isin"), fonte=FONTE),
        codigo_ativo=SourcedValue(payload.get("codigo_b3"), fonte=FONTE),
        emissor_nome=SourcedValue(emissor.get("nome"), fonte=FONTE),
        emissor_cnpj=SourcedValue(emissor.get("cnpj"), fonte=FONTE),
        numero_emissao=SourcedValue(emissao.get("numero_emissao"), fonte=FONTE),
        numero_serie=SourcedValue(payload.get("numero_serie"), fonte=FONTE),
        indexador=SourcedValue(indexador.get("nome"), fonte=FONTE),
        taxa=SourcedValue(payload.get("remuneracao"), fonte=FONTE),
        data_emissao=SourcedValue(_parse_data_iso(emissao.get("data_emissao")), fonte=FONTE),
        data_vencimento=SourcedValue(_parse_data_iso(payload.get("data_vencimento")), fonte=FONTE),
        especie=SourcedValue(emissao.get("garantia"), fonte=FONTE),
        classe=SourcedValue(payload.get("classe"), fonte=FONTE),
        quantidade_emitida=SourcedValue(emissao.get("quantidade_emitida"), fonte=FONTE),
        quantidade_mercado=SourcedValue(payload.get("quantidade_mercado"), fonte=FONTE),
        valor_nominal_unitario=SourcedValue(payload.get("valor_nominal_atual"), fonte=FONTE),
        preco_indicativo=SourcedValue(payload.get("preco_indicativo"), fonte=FONTE),
    )


def _parse_data_iso(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None
