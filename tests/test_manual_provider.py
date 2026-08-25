from __future__ import annotations

import pytest

from debenture_search.models import DebentureRef
from debenture_search.providers.manual import ManualInputProvider

REF = DebentureRef(isin="BRTEPADBS001", codigo_ativo="TEPA23", nome_emissor="Tegma")


def test_set_field_depois_fetch_retorna_valor(tmp_path) -> None:
    provider = ManualInputProvider(tmp_path / "manual.sqlite3")
    provider.set_field(REF, "rating", "AA-", "Fitch, 03/2026")

    resultado = provider.fetch_characteristics(REF)
    assert resultado.sucesso
    assert resultado.valor.rating.valor == "AA-"
    assert "Fitch" in resultado.valor.rating.fonte


def test_campo_nao_suportado_levanta_erro(tmp_path) -> None:
    provider = ManualInputProvider(tmp_path / "manual.sqlite3")
    with pytest.raises(ValueError):
        provider.set_field(REF, "campo_inexistente", "x", "fonte")


def test_fetch_sem_overrides_retorna_debenture_vazia(tmp_path) -> None:
    provider = ManualInputProvider(tmp_path / "manual.sqlite3")
    resultado = provider.fetch_characteristics(REF)
    assert resultado.sucesso
    assert resultado.valor.rating.disponivel is False


def test_set_field_atualiza_valor_existente(tmp_path) -> None:
    provider = ManualInputProvider(tmp_path / "manual.sqlite3")
    provider.set_field(REF, "rating", "AA-", "Fitch, 03/2026")
    provider.set_field(REF, "rating", "AA", "Fitch, 06/2026")

    resultado = provider.fetch_characteristics(REF)
    assert resultado.valor.rating.valor == "AA"
