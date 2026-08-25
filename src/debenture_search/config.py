"""Configuração central: onde ficam os arquivos SQLite locais."""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DEBENTURE_SEARCH_DATA_DIR", Path.home() / ".debenture_search"))
CACHE_DB_PATH = DATA_DIR / "cache.sqlite3"
MANUAL_INPUT_DB_PATH = DATA_DIR / "manual_input.sqlite3"

# Presença desta env var liga o AnbimaAPIProvider (Fase 3). Ausente = a
# fonte fica indisponível e o aggregator simplesmente não a inclui.
ANBIMA_API_KEY = os.environ.get("ANBIMA_API_KEY")
