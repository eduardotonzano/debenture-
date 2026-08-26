"""Configuração central: onde ficam os arquivos SQLite locais."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DEBENTURE_SEARCH_DATA_DIR", Path.home() / ".debenture_search"))
CACHE_DB_PATH = DATA_DIR / "cache.sqlite3"
MANUAL_INPUT_DB_PATH = DATA_DIR / "manual_input.sqlite3"

# Credenciais do app cadastrado no ANBIMA Developers (Fase 3). Ausentes =
# AnbimaAPIProvider.is_available() retorna False e o aggregator simplesmente
# não o inclui. NUNCA hardcodear valores reais aqui nem no repositório —
# somente via env var, definida localmente por quem for rodar o projeto.
ANBIMA_CLIENT_ID = os.environ.get("ANBIMA_CLIENT_ID")
ANBIMA_CLIENT_SECRET = os.environ.get("ANBIMA_CLIENT_SECRET")
# "sandbox" (padrão, seguro) ou "producao" — troque só se o app tiver
# assinatura de produção aprovada para o pacote Preços e Índices.
ANBIMA_AMBIENTE = os.environ.get("ANBIMA_AMBIENTE", "sandbox")

# HTTP Basic Auth para a UI web, pensado pra hospedagem pública (ex.:
# Render) de um projeto de uso pessoal — sem as duas env vars a UI fica
# aberta (comportamento de desenvolvimento local, sem mudança). NUNCA
# hardcodear valores reais aqui nem no repositório.
WEB_AUTH_USERNAME = os.environ.get("WEB_AUTH_USERNAME")
WEB_AUTH_PASSWORD = os.environ.get("WEB_AUTH_PASSWORD")
