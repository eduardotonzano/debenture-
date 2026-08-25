"""Modelos de domínio.

Praticamente todo campo de negócio é Optional: nenhuma fonte (SND, ANBIMA,
CVM, input manual) garante cobertura completa, e o sistema nunca inventa um
valor para preencher um campo ausente — ele fica None e a UI mostra
"indisponível".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Situacao(str, Enum):
    ATIVA = "ativa"
    VENCIDA = "vencida"
    RESGATADA = "resgatada"
    INDISPONIVEL = "indisponivel"


class TipoEvento(str, Enum):
    REPACTUACAO = "repactuacao"
    AMORTIZACAO = "amortizacao"
    JUROS = "juros"
    VENCIMENTO = "vencimento"


class TipoDocumento(str, Enum):
    PROSPECTO = "prospecto"
    ESCRITURA = "escritura"
    FATO_RELEVANTE = "fato_relevante"


@dataclass(frozen=True)
class SourcedValue:
    """Um valor de campo com proveniência: de onde veio e quando foi coletado.

    É o que sustenta o requisito de nunca esconder a fonte de um dado e
    nunca fingir que um campo ausente tem um valor.
    """

    valor: object | None
    fonte: str | None = None
    coletado_em: datetime | None = None

    @property
    def disponivel(self) -> bool:
        return self.valor is not None


@dataclass
class SearchQuery:
    """Entrada normalizada da busca do usuário — exatamente um campo setado."""

    isin: str | None = None
    codigo_ativo: str | None = None
    nome_emissor: str | None = None

    def __post_init__(self) -> None:
        setados = [v for v in (self.isin, self.codigo_ativo, self.nome_emissor) if v]
        if len(setados) != 1:
            raise ValueError(
                "SearchQuery precisa de exatamente um entre isin, codigo_ativo, nome_emissor"
            )


@dataclass(frozen=True)
class DebentureRef:
    """Referência leve a uma série de debênture, suficiente para desambiguação
    e para os providers buscarem os dados completos."""

    isin: str | None
    codigo_ativo: str | None
    nome_emissor: str


@dataclass
class Issuer:
    id: str | None = None
    cnpj: str | None = None
    nome: str = ""
    nome_fantasia: str | None = None
    cvm_code: str | None = None
    setor: str | None = None


@dataclass
class MarketPriceSnapshot:
    debenture_ref: DebentureRef
    periodo_referencia: str | None = None
    pu_minimo: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    pu_medio: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    pu_maximo: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    quantidade_negociada: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    numero_negocios: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    coletado_em: datetime | None = None


@dataclass
class Event:
    debenture_ref: DebentureRef
    tipo: TipoEvento
    data_prevista: date | None = None
    valor: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    fonte: str | None = None


@dataclass
class Document:
    tipo: TipoDocumento
    url: str
    data_publicacao: date | None = None
    fonte: str = "CVM"
    debenture_ref: DebentureRef | None = None
    issuer_id: str | None = None


@dataclass
class ManualInput:
    """Override manual de um campo — sempre a fonte de maior precedência."""

    debenture_ref: DebentureRef
    campo: str
    valor: object
    fonte_descricao: str
    inserido_em: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Debenture:
    """Ficha completa de uma série de debênture — o resultado final da busca.

    Cada campo de característica é um SourcedValue para que a UI mostre a
    fonte (ou "indisponível") campo a campo, em vez de um objeto plano onde
    a proveniência se perde.
    """

    isin: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    codigo_ativo: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    emissor_nome: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    emissor_cnpj: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    numero_emissao: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    numero_serie: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    indexador: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    taxa: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    data_emissao: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    data_vencimento: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    especie: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    classe: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    quantidade_emitida: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    quantidade_mercado: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    valor_nominal_unitario: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    situacao: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    motivo_saida: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    data_exclusao_registro: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    data_vencimento_antecipado: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    motivo_inadimplencia: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    rating: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    # Como a emissão foi realizada — forma, regime de registro na CVM,
    # atos societários que aprovaram, início de distribuição.
    forma: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    registro_cvm_emissao: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    ato_societario: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    inicio_distribuicao: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    # Agentes contratados na emissão.
    banco_mandatario: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    agente_fiduciario: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    instituicao_depositaria: SourcedValue = field(default_factory=lambda: SourcedValue(None))
    coordenador_lider: SourcedValue = field(default_factory=lambda: SourcedValue(None))

    precos: list[MarketPriceSnapshot] = field(default_factory=list)
    eventos: list[Event] = field(default_factory=list)
    documentos: list[Document] = field(default_factory=list)
