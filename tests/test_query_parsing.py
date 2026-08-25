"""Testa a heurística de inferência de tipo de busca — ISIN vs código de
ativo vs nome de emissor. Bug real encontrado durante validação da Fase 2:
"BODYTECH" (8 letras maiúsculas, sem dígito) batia no regex de código de
ativo e disparava uma consulta ao SND como se fosse ticker, em vez de cair
na busca por nome de emissor.
"""

from __future__ import annotations

from debenture_search.query_parsing import infer_query


def test_isin_reconhecido() -> None:
    query = infer_query("BRBODYDBS000")
    assert query.isin == "BRBODYDBS000"


def test_codigo_ativo_com_digito_reconhecido() -> None:
    query = infer_query("body12")
    assert query.codigo_ativo == "BODY12"


def test_nome_emissor_todo_maiusculo_sem_digito_nao_vira_codigo_ativo() -> None:
    query = infer_query("BODYTECH")
    assert query.nome_emissor == "BODYTECH"
    assert query.codigo_ativo is None


def test_nome_emissor_com_espaco_e_sempre_nome() -> None:
    query = infer_query("Tegma Gestão")
    assert query.nome_emissor == "Tegma Gestão"


def test_tipo_forcado_prevalece_sobre_heuristica() -> None:
    query = infer_query("BODYTECH", tipo="emissor")
    assert query.nome_emissor == "BODYTECH"

    query = infer_query("qualquercoisa", tipo="codigo_ativo")
    assert query.codigo_ativo == "QUALQUERCOISA"
