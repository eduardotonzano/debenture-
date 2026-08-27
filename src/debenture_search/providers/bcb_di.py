"""Cliente da API pública do Banco Central (SGS — Sistema Gerenciador de
Séries Temporais), série 12 ("Taxa de juros - CDI"), usada como fonte da
Taxa DI-Over exigida pela fórmula oficial da ANBIMA pra PU Par (ver
`pu_par.py`).

Contrato público, sem autenticação, estável e documentado há mais de uma
década — usado por dezenas de projetos abertos brasileiros de finanças
(ex.: biblioteca `python-bcb`) — e confirmado por busca (não é a API paga
ANBIMA nem exige credencial nenhuma):

    GET https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados?formato=json
        &dataInicial=DD/MM/AAAA&dataFinal=DD/MM/AAAA

    -> [{"data": "02/01/2024", "valor": "11.65"}, ...]

`valor` é a Taxa DI-Over do dia, em % a.a., base 252 dias úteis — a mesma
unidade e definição que a fórmula ANBIMA usa como "Taxa DI" (ver
pu_par.py). A série só tem um ponto por dia útil (fins de semana/feriados
não aparecem), o que já casa com "produtório sobre os dias úteis" da
fórmula sem filtragem extra deste lado.

Ainda NÃO validado por uma chamada real: bcb.gov.br está bloqueado neste
sandbox de desenvolvimento — mesma restrição que afetou SND/ANBIMA
Data/ANBIMA Feed/CVM ao longo deste projeto. Precisa ser confirmado fora
daqui (mesmo processo já usado pra validar a ANBIMA Feed: setar nada de
credencial — essa API é aberta — e rodar de um ambiente com rede liberada)
antes de se confiar no resultado em produção.
"""

from __future__ import annotations

from datetime import date, datetime

from debenture_search.http_client import RateLimitedHttpClient

_URL_SERIE_12 = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"


class BancoCentralDiProvider:
    """Busca a série histórica da Taxa DI-Over (% a.a., base 252) publicada
    diariamente pelo Banco Central/B3 — não implementa nenhum dos Protocols
    de `providers/base.py` (não é um dado de característica, mercado,
    evento ou documento da debênture em si, é um insumo de cálculo
    compartilhado entre debêntures), é consumido diretamente por quem
    monta o PU Par calculado (ver aggregator/compose)."""

    name = "Banco Central (SGS série 12 — Taxa DI)"

    def __init__(self, http_client: RateLimitedHttpClient | None = None) -> None:
        # API pública sem necessidade de rate limit agressivo, mas ainda
        # assim respeitosa — mesmo padrão dos outros clientes do projeto.
        self._http = http_client or RateLimitedHttpClient(min_interval_seconds=0.5)

    def is_available(self) -> bool:
        return True

    def close(self) -> None:
        self._http.close()

    def fetch_serie(self, data_inicial: date, data_final: date) -> list[tuple[date, float]]:
        """Retorna (data, taxa_di_aa) por dia útil no intervalo, em ordem
        cronológica. Levanta a exceção HTTP em caso de falha — quem chama
        decide se isso vira 'PU Par indisponível' (nunca um valor
        calculado com dado faltante)."""
        resposta = self._http.get(
            _URL_SERIE_12,
            params={
                "formato": "json",
                "dataInicial": data_inicial.strftime("%d/%m/%Y"),
                "dataFinal": data_final.strftime("%d/%m/%Y"),
            },
        )
        resposta.raise_for_status()
        pontos = [
            (datetime.strptime(item["data"], "%d/%m/%Y").date(), float(item["valor"]))
            for item in resposta.json()
        ]
        pontos.sort(key=lambda par: par[0])
        return pontos
