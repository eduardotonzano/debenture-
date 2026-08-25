"""Serialização da ficha de Debenture para JSON (uso pela CLI e, na Fase 2, pela API).

Cada campo de característica vira {"valor": ..., "fonte": ..., "disponivel": ...}
— a UI (ou, aqui, o output da CLI) nunca perde a proveniência nem finge que
um campo ausente tem valor.
"""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from enum import Enum
from typing import Any

from debenture_search.models import Debenture, SourcedValue


def _jsonify(value: Any) -> Any:
    if isinstance(value, SourcedValue):
        return {
            "valor": _jsonify(value.valor),
            "fonte": value.fonte,
            "disponivel": value.disponivel,
            "coletado_em": value.coletado_em.isoformat() if value.coletado_em else None,
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _jsonify(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    return value


def debenture_to_dict(debenture: Debenture) -> dict[str, Any]:
    return _jsonify(debenture)
