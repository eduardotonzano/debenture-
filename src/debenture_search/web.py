"""UI de busca da Fase 2 — uma tela de busca única + ficha do ativo por
seções, sem grid/dashboard de múltiplos ativos (ver README).

`create_app()` recebe um `AggregatorFactory` (por padrão,
`compose.build_aggregator`) em vez de importar o aggregator direto no nível
do módulo — isso é o que permite os testes injetarem um aggregator com
providers fake, sem precisar de rede nem de um banco de cache real.
"""

from __future__ import annotations

import secrets
import urllib.parse
from datetime import date
from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from debenture_search import config
from debenture_search.aggregator import Ambiguous, DebentureAggregator
from debenture_search.compose import build_aggregator
from debenture_search.config import MANUAL_INPUT_DB_PATH
from debenture_search.models import DebentureRef
from debenture_search.providers.manual import CAMPOS_SUPORTADOS, ManualInputProvider
from debenture_search.query_parsing import infer_query
from debenture_search.ref_params import ref_from_params, ref_to_params

AggregatorFactory = Callable[[], DebentureAggregator]

_security = HTTPBasic(auto_error=False)


def _exigir_autenticacao(
    credenciais: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    """Gate opcional de HTTP Basic Auth pra hospedagem pública.

    Lê `config.WEB_AUTH_USERNAME`/`WEB_AUTH_PASSWORD` (não os importa por
    nome) pra ficar sensível a monkeypatch em teste e a mudança de env var
    em runtime — sem as duas configuradas, a UI fica aberta (uso local).
    """
    if not (config.WEB_AUTH_USERNAME and config.WEB_AUTH_PASSWORD):
        return

    usuario_ok = credenciais is not None and secrets.compare_digest(
        credenciais.username, config.WEB_AUTH_USERNAME
    )
    senha_ok = credenciais is not None and secrets.compare_digest(
        credenciais.password, config.WEB_AUTH_PASSWORD
    )
    if not (usuario_ok and senha_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )

TEMPLATES_DIR = Path(__file__).parent / "templates"

_CAMPO_ROTULOS = {
    "rating": "Rating",
    "taxa": "Taxa",
    "quantidade_mercado": "Quantidade em mercado",
}


def _urlencode_params(params: dict[str, str]) -> str:
    return urllib.parse.urlencode(params)


def _fmt(valor: object) -> str:
    if valor is None:
        return "indisponível"
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float):
        return f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return str(valor)


def create_app(aggregator_factory: AggregatorFactory = build_aggregator) -> FastAPI:
    app = FastAPI(
        title="Motor de Busca de Debêntures",
        dependencies=[Depends(_exigir_autenticacao)],
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.filters["urlencode_params"] = _urlencode_params
    templates.env.globals["fmt"] = _fmt

    def _manual_provider() -> ManualInputProvider:
        return ManualInputProvider(MANUAL_INPUT_DB_PATH)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "busca.html", {"q": ""})

    @app.get("/busca", response_class=HTMLResponse)
    def busca(request: Request, q: str = "", tipo: str | None = None) -> HTMLResponse:
        q = q.strip()
        if not q:
            return templates.TemplateResponse(request, "busca.html", {"q": ""})

        query = infer_query(q, tipo)
        aggregator = aggregator_factory()

        try:
            resultado = aggregator.resolve(query)
        except Exception as exc:  # noqa: BLE001 - falha de fonte externa
            return templates.TemplateResponse(
                request, "erro_busca.html", {"q": q, "erro_detalhe": str(exc)}
            )

        if isinstance(resultado, Ambiguous):
            candidatos = [(ref, ref_to_params(ref)) for ref in resultado.candidatos]
            return templates.TemplateResponse(
                request, "desambiguacao.html", {"q": q, "candidatos": candidatos}
            )

        if not resultado:
            return templates.TemplateResponse(request, "nao_encontrado.html", {"q": q})

        params = ref_to_params(resultado[0])
        return RedirectResponse(url=f"/ficha?{_urlencode_params(params)}", status_code=303)

    @app.get("/ficha", response_class=HTMLResponse)
    def ficha(
        request: Request,
        codigo_ativo: str | None = None,
        isin: str | None = None,
        nome_emissor: str | None = None,
    ) -> HTMLResponse:
        ref = ref_from_params(codigo_ativo, isin, nome_emissor)
        aggregator = aggregator_factory()
        deb = aggregator.build_ficha(ref)
        # Ordenado aqui (não no template) pra nunca comparar None com date
        # no Jinja `sort` se algum documento vier sem data de publicação.
        deb.documentos.sort(key=lambda d: d.data_publicacao or date.min, reverse=True)
        return templates.TemplateResponse(
            request, "ficha.html", {"deb": deb, "ref": ref, "ref_params": ref_to_params(ref)}
        )

    @app.get("/manual", response_class=HTMLResponse)
    def manual_form(
        request: Request,
        codigo_ativo: str | None = None,
        isin: str | None = None,
        nome_emissor: str | None = None,
    ) -> HTMLResponse:
        ref = ref_from_params(codigo_ativo, isin, nome_emissor)
        campos = [(chave, _CAMPO_ROTULOS.get(chave, chave)) for chave in sorted(CAMPOS_SUPORTADOS)]
        return templates.TemplateResponse(
            request,
            "manual.html",
            {"ref": ref, "ref_params": ref_to_params(ref), "campos_suportados": campos},
        )

    @app.post("/manual")
    def manual_submit(
        codigo_ativo: str = Form(""),
        isin: str = Form(""),
        nome_emissor: str = Form(""),
        campo: str = Form(...),
        valor: str = Form(...),
        fonte: str = Form(...),
    ) -> RedirectResponse:
        ref = ref_from_params(codigo_ativo or None, isin or None, nome_emissor or None)
        provider = _manual_provider()
        provider.set_field(ref, campo, valor.strip(), fonte.strip())
        params = ref_to_params(ref)
        return RedirectResponse(url=f"/ficha?{_urlencode_params(params)}", status_code=303)

    return app


app = create_app()
