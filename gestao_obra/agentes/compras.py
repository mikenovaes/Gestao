"""Agente 3 — Compras.

Olha o cronograma, calcula o material da próxima etapa (quantidade × composição
+ perda) e pede cotação para 3 fornecedores com antecedência. Quando as
respostas chegam, escolhe o melhor preço que entrega a tempo e deixa a compra
como decisão pendente para o dono aprovar.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Composicao, Cotacao, CotacaoItem, CotacaoResposta, Etapa, Fornecedor, Material, Obra, Severidade
from ..servicos.whatsapp import notificador
from .base import brl, registrar_alerta

NOME = "compras"


def calcular_materiais(db: Session, etapa: Etapa) -> list[tuple[Material, float]]:
    composicoes = db.scalars(select(Composicao).where(Composicao.servico == etapa.servico)).all()
    return [(c.material, math.ceil(etapa.quantidade * c.consumo * (1 + c.perda))) for c in composicoes]


def _ja_cotada(db: Session, etapa: Etapa) -> bool:
    return db.scalar(select(Cotacao.id).where(Cotacao.etapa_id == etapa.id, Cotacao.status != "cancelada")) is not None


def executar(db: Session, obra: Obra, hoje: date) -> list[Cotacao]:
    janela = hoje + timedelta(days=settings.antecedencia_compra_dias)
    criadas = []
    for etapa in obra.etapas:
        if etapa.inicio_real or etapa.concluida or etapa.inicio_previsto > janela or _ja_cotada(db, etapa):
            continue
        materiais = calcular_materiais(db, etapa)
        if materiais:
            criadas.append(abrir_cotacao(db, obra, etapa, materiais, precisa_ate=etapa.inicio_previsto - timedelta(days=1)))
    return criadas


def abrir_cotacao(db: Session, obra: Obra, etapa: Etapa | None, materiais: list[tuple[Material, float]],
                  *, precisa_ate: date | None, motivo: str | None = None) -> Cotacao:
    cot = Cotacao(obra_id=obra.id, etapa_id=etapa.id if etapa else None, precisa_ate=precisa_ate,
                  motivo=motivo or (f"Próxima etapa: {etapa.nome}" if etapa else None))
    cot.itens = [CotacaoItem(material_id=m.id, quantidade=q, material=m) for m, q in materiais]
    fornecedores = db.scalars(
        select(Fornecedor).where(Fornecedor.ativo.is_(True)).order_by(Fornecedor.id).limit(settings.fornecedores_por_cotacao)
    ).all()
    cot.respostas = [CotacaoResposta(fornecedor_id=f.id, fornecedor=f) for f in fornecedores]
    db.add(cot)
    db.flush()

    lista = "\n".join(f"• {q:g} {m.unidade} de {m.nome}" for m, q in materiais)
    prazo = f" com entrega até {precisa_ate:%d/%m}" if precisa_ate else ""
    for f in fornecedores:
        notificador.enviar(f.whatsapp, f"Olá, {f.nome}! Pedido de cotação nº {cot.id} — obra {obra.nome}{prazo}:\n"
                                       f"{lista}\nPor favor, responda com valor total e prazo de entrega.")
    return cot


def registrar_resposta(db: Session, cotacao: Cotacao, fornecedor_id: int, valor_total: float,
                       prazo_entrega_dias: int, hoje: date) -> Cotacao:
    resp = next((r for r in cotacao.respostas if r.fornecedor_id == fornecedor_id), None)
    if resp is None:
        resp = CotacaoResposta(fornecedor_id=fornecedor_id)
        cotacao.respostas.append(resp)
    resp.valor_total, resp.prazo_entrega_dias = valor_total, prazo_entrega_dias
    db.flush()
    if all(r.valor_total is not None for r in cotacao.respostas):
        escolher(db, cotacao, hoje)
    return cotacao


def escolher(db: Session, cotacao: Cotacao, hoje: date) -> CotacaoResposta | None:
    validas = [r for r in cotacao.respostas if r.valor_total is not None]
    if not validas:
        return None
    no_prazo = [r for r in validas if cotacao.precisa_ate is None
                or hoje + timedelta(days=r.prazo_entrega_dias or 0) <= cotacao.precisa_ate]
    melhor = min(no_prazo or validas, key=lambda r: r.valor_total)
    for r in cotacao.respostas:
        r.escolhida = r is melhor
    cotacao.status = "escolhida"

    comparativo = " | ".join(f"{r.fornecedor.nome}: {brl(r.valor_total)}" for r in sorted(validas, key=lambda r: r.valor_total))
    aviso = "" if no_prazo else " ATENÇÃO: nenhum fornecedor entrega no prazo."
    obra = db.get(Obra, cotacao.obra_id)
    registrar_alerta(
        db, obra_id=cotacao.obra_id, etapa_id=cotacao.etapa_id, agente=NOME, tipo="decisao",
        severidade=Severidade.atencao if no_prazo else Severidade.critico,
        mensagem=f"{obra.nome}: aprovar compra da cotação {cotacao.id} com {melhor.fornecedor.nome} "
                 f"por {brl(melhor.valor_total)} (melhor preço). {comparativo}.{aviso}",
        dados={"cotacao_id": cotacao.id, "resposta_id": melhor.id}, chave=f"cotacao:{cotacao.id}",
    )
    db.flush()
    return melhor
