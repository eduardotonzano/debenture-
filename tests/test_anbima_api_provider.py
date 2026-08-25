"""Testa o AnbimaAPIProvider — SEM rede, e sem uma amostra real de resposta
da API paga (o usuário não tem credencial ainda). A fixture usada aqui
(`anbima_api_caracteristicas_sintetico.json`) é sintética — escrita à mão
como hipótese de schema, não confirmada contra a API real. Ver aviso no
topo do arquivo e docstring de providers/anbima_api.py.

Esses testes provam que a lógica de merge/parsing funciona estruturalmente
e que a fonte fica corretamente desligada sem credencial — não provam que
o schema bate com a API real, o que só pode ser confirmado quando houver
acesso.
"""

from __future__ import annotations

import json
from pathlib import Path

from debenture_search.models import DebentureRef
from debenture_search.providers.anbima_api import AnbimaAPIProvider, _parse_caracteristicas_json

FIXTURES = Path(__file__).parent / "fixtures"


def test_indisponivel_sem_api_key() -> None:
    provider = AnbimaAPIProvider(api_key=None)
    assert provider.is_available() is False


def test_disponivel_com_api_key() -> None:
    provider = AnbimaAPIProvider(api_key="qualquer-coisa")
    assert provider.is_available() is True


def test_parse_caracteristicas_json_fixture_sintetica() -> None:
    payload = json.loads(
        (FIXTURES / "anbima_api_caracteristicas_sintetico.json").read_text(encoding="utf-8")
    )
    deb = _parse_caracteristicas_json(payload)

    assert deb.isin.valor == "BRTESTDBS001"
    assert deb.codigo_ativo.valor == "TEST12"
    assert deb.emissor_nome.valor == "EMPRESA TESTE FIXTURE S.A."
    assert deb.emissor_cnpj.valor == "00000000000191"
    assert deb.indexador.valor == "DI"
    assert deb.taxa.valor == "DI + 2.0000%"
    assert deb.data_emissao.valor.isoformat() == "2020-01-15"
    assert deb.data_vencimento.valor.isoformat() == "2030-01-15"
    assert deb.especie.valor == "Quirografária"
    assert deb.preco_indicativo.valor == "1048.987654"
    for campo in (deb.isin, deb.codigo_ativo, deb.emissor_nome, deb.indexador, deb.taxa):
        assert campo.fonte == "ANBIMA API"


def test_parse_caracteristicas_json_campos_ausentes_ficam_indisponiveis() -> None:
    deb = _parse_caracteristicas_json({})
    assert deb.isin.disponivel is False
    assert deb.preco_indicativo.disponivel is False


def test_fetch_characteristics_sem_ref_valida_nao_falha() -> None:
    provider = AnbimaAPIProvider(api_key="qualquer-coisa")
    resultado = provider.fetch_characteristics(DebentureRef(isin=None, codigo_ativo=None, nome_emissor=""))
    assert resultado.sucesso
    assert resultado.valor.isin.disponivel is False
