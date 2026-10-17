"""Base única da obra.

Tudo que chega (áudio, foto, nota fiscal, planilha, e-mail) vira um `Evento`.
Os agentes leem e escrevem apenas nestas tabelas — nunca conversam entre si
diretamente. Assim cada agente pode ser ligado/testado isoladamente.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

Dinheiro = Numeric(14, 2, asdecimal=False)


class TipoEvento(str, enum.Enum):
    texto = "texto"
    audio = "audio"
    foto = "foto"
    nota_fiscal = "nota_fiscal"
    planilha = "planilha"
    email = "email"


class Severidade(str, enum.Enum):
    info = "info"
    atencao = "atencao"
    critico = "critico"


class Obra(Base):
    __tablename__ = "obras"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120), unique=True)
    endereco: Mapped[str | None] = mapped_column(String(255))
    cliente_nome: Mapped[str | None] = mapped_column(String(120))
    cliente_whatsapp: Mapped[str | None] = mapped_column(String(20))
    mestre_whatsapp: Mapped[str | None] = mapped_column(String(20), index=True)
    engenheiro_whatsapp: Mapped[str | None] = mapped_column(String(20))
    ativa: Mapped[bool] = mapped_column(Boolean, default=True)

    etapas: Mapped[list[Etapa]] = relationship(back_populates="obra", order_by="Etapa.ordem", cascade="all, delete-orphan")


class Etapa(Base):
    __tablename__ = "etapas"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    nome: Mapped[str] = mapped_column(String(120))  # ex.: "Alvenaria bloco B"
    servico: Mapped[str] = mapped_column(String(60))  # chave da composição: "alvenaria", "estrutura"...
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    quantidade: Mapped[float] = mapped_column(Float, default=0)  # ex.: 64 (m²)
    unidade: Mapped[str] = mapped_column(String(10), default="un")
    orcamento: Mapped[float] = mapped_column(Dinheiro, default=0)
    inicio_previsto: Mapped[date] = mapped_column(Date)
    fim_previsto: Mapped[date] = mapped_column(Date)
    inicio_real: Mapped[date | None] = mapped_column(Date)
    fim_real: Mapped[date | None] = mapped_column(Date)
    percentual: Mapped[float] = mapped_column(Float, default=0)  # avanço físico 0-100

    obra: Mapped[Obra] = relationship(back_populates="etapas")

    @property
    def concluida(self) -> bool:
        return self.percentual >= 100


class Evento(Base):
    """Caixa de entrada única: o que chegou, de quem, e se já foi processado."""

    __tablename__ = "eventos"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int | None] = mapped_column(ForeignKey("obras.id"), index=True)
    tipo: Mapped[TipoEvento] = mapped_column(Enum(TipoEvento))
    origem: Mapped[str] = mapped_column(String(30), default="whatsapp")
    remetente: Mapped[str | None] = mapped_column(String(60))
    conteudo: Mapped[str | None] = mapped_column(Text)  # texto ou transcrição
    arquivo: Mapped[str | None] = mapped_column(String(255))
    recebido_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processado: Mapped[bool] = mapped_column(Boolean, default=False)
    erro: Mapped[str | None] = mapped_column(Text)


class DiarioRegistro(Base):
    __tablename__ = "diario"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    etapa_id: Mapped[int | None] = mapped_column(ForeignKey("etapas.id"))
    data: Mapped[date] = mapped_column(Date, index=True)
    feito: Mapped[str] = mapped_column(Text)
    equipe: Mapped[int | None] = mapped_column(Integer)
    travou: Mapped[str | None] = mapped_column(Text)
    percentual_informado: Mapped[float | None] = mapped_column(Float)
    evento_id: Mapped[int | None] = mapped_column(ForeignKey("eventos.id"))

    etapa: Mapped[Etapa | None] = relationship()


class Foto(Base):
    __tablename__ = "fotos"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    etapa_id: Mapped[int | None] = mapped_column(ForeignKey("etapas.id"))
    data: Mapped[date] = mapped_column(Date, index=True)
    arquivo: Mapped[str] = mapped_column(String(255))
    legenda: Mapped[str | None] = mapped_column(String(255))
    evento_id: Mapped[int | None] = mapped_column(ForeignKey("eventos.id"))


class Material(Base):
    __tablename__ = "materiais"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(80), unique=True)
    unidade: Mapped[str] = mapped_column(String(10))
    preco_referencia: Mapped[float] = mapped_column(Dinheiro)  # base para detectar "cimento 12% mais caro"


class Composicao(Base):
    """Consumo de material por unidade de serviço (ex.: alvenaria → 12,5 blocos/m²)."""

    __tablename__ = "composicoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    servico: Mapped[str] = mapped_column(String(60), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materiais.id"))
    consumo: Mapped[float] = mapped_column(Float)  # material por unidade do serviço
    perda: Mapped[float] = mapped_column(Float, default=0.05)

    material: Mapped[Material] = relationship()


class Fornecedor(Base):
    __tablename__ = "fornecedores"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome: Mapped[str] = mapped_column(String(120))
    whatsapp: Mapped[str | None] = mapped_column(String(20))
    email: Mapped[str | None] = mapped_column(String(120))
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


class Cotacao(Base):
    __tablename__ = "cotacoes"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    etapa_id: Mapped[int | None] = mapped_column(ForeignKey("etapas.id"))
    status: Mapped[str] = mapped_column(String(20), default="aguardando")  # aguardando | escolhida | cancelada
    precisa_ate: Mapped[date | None] = mapped_column(Date)
    criada_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    motivo: Mapped[str | None] = mapped_column(String(255))

    itens: Mapped[list[CotacaoItem]] = relationship(cascade="all, delete-orphan")
    respostas: Mapped[list[CotacaoResposta]] = relationship(cascade="all, delete-orphan")
    etapa: Mapped[Etapa | None] = relationship()


class CotacaoItem(Base):
    __tablename__ = "cotacao_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    cotacao_id: Mapped[int] = mapped_column(ForeignKey("cotacoes.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("materiais.id"))
    quantidade: Mapped[float] = mapped_column(Float)

    material: Mapped[Material] = relationship()


class CotacaoResposta(Base):
    __tablename__ = "cotacao_respostas"

    id: Mapped[int] = mapped_column(primary_key=True)
    cotacao_id: Mapped[int] = mapped_column(ForeignKey("cotacoes.id"))
    fornecedor_id: Mapped[int] = mapped_column(ForeignKey("fornecedores.id"))
    valor_total: Mapped[float | None] = mapped_column(Dinheiro)  # None = pedido enviado, sem resposta
    prazo_entrega_dias: Mapped[int | None] = mapped_column(Integer)
    escolhida: Mapped[bool] = mapped_column(Boolean, default=False)

    fornecedor: Mapped[Fornecedor] = relationship()


class NotaFiscal(Base):
    __tablename__ = "notas_fiscais"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    etapa_id: Mapped[int] = mapped_column(ForeignKey("etapas.id"), index=True)
    fornecedor_id: Mapped[int | None] = mapped_column(ForeignKey("fornecedores.id"))
    numero: Mapped[str] = mapped_column(String(40))
    data: Mapped[date] = mapped_column(Date, index=True)
    valor_total: Mapped[float] = mapped_column(Dinheiro)
    evento_id: Mapped[int | None] = mapped_column(ForeignKey("eventos.id"))

    itens: Mapped[list[NotaItem]] = relationship(cascade="all, delete-orphan")
    etapa: Mapped[Etapa] = relationship()


class NotaItem(Base):
    __tablename__ = "nota_itens"

    id: Mapped[int] = mapped_column(primary_key=True)
    nota_id: Mapped[int] = mapped_column(ForeignKey("notas_fiscais.id"))
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materiais.id"))
    descricao: Mapped[str] = mapped_column(String(160))
    quantidade: Mapped[float] = mapped_column(Float)
    preco_unitario: Mapped[float] = mapped_column(Dinheiro)

    material: Mapped[Material | None] = relationship()


class Alerta(Base):
    """Saída comum dos agentes. `tipo='decisao'` = decisão pendente do dono."""

    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    etapa_id: Mapped[int | None] = mapped_column(ForeignKey("etapas.id"))
    agente: Mapped[str] = mapped_column(String(20))
    tipo: Mapped[str] = mapped_column(String(30))  # atraso | estouro_custo | impedimento | decisao
    severidade: Mapped[Severidade] = mapped_column(Enum(Severidade), default=Severidade.atencao)
    mensagem: Mapped[str] = mapped_column(Text)
    dados: Mapped[dict] = mapped_column(JSON, default=dict)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    resolvido: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notificado: Mapped[bool] = mapped_column(Boolean, default=False)


class Relatorio(Base):
    __tablename__ = "relatorios"

    id: Mapped[int] = mapped_column(primary_key=True)
    obra_id: Mapped[int] = mapped_column(ForeignKey("obras.id"), index=True)
    data: Mapped[date] = mapped_column(Date)
    resumo: Mapped[dict] = mapped_column(JSON)
    texto_interno: Mapped[str] = mapped_column(Text)
    texto_cliente: Mapped[str] = mapped_column(Text)
    criado_em: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
