"""Agente 4 — Custo.

Cada nota fiscal que entra é lançada contra o orçamento da etapa. Estourou 5%
em alguma etapa, ele avisa na hora e mostra o motivo: preço acima da referência
("cimento 12% mais caro") ou consumo acima da composição.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Etapa, Material, NotaFiscal, NotaItem, Obra, Severidade
from .base import brl, registrar_alerta, resolver_alertas
from .compras import calcular_materiais

NOME = "custo"


@dataclass
class ItemNota:
    descricao: str
    quantidade: float
    preco_unitario: float
    material_id: int | None = None


@dataclass
class SituacaoCusto:
    etapa: Etapa
    gasto: float
    desvio: float  # (gasto / orçamento) - 1
    motivo: str | None


def lancar_nota(db: Session, obra: Obra, etapa: Etapa, *, numero: str, data: date, itens: list[ItemNota],
                fornecedor_id: int | None = None, evento_id: int | None = None) -> tuple[NotaFiscal, SituacaoCusto]:
    nota = NotaFiscal(
        obra_id=obra.id, etapa_id=etapa.id, fornecedor_id=fornecedor_id, numero=numero, data=data,
        valor_total=round(sum(i.quantidade * i.preco_unitario for i in itens), 2), evento_id=evento_id,
        itens=[NotaItem(descricao=i.descricao, material_id=i.material_id or _material_por_nome(db, i.descricao),
                        quantidade=i.quantidade, preco_unitario=i.preco_unitario) for i in itens],
    )
    db.add(nota)
    db.flush()
    return nota, avaliar_etapa(db, obra, etapa)


def _material_por_nome(db: Session, descricao: str) -> int | None:
    from .base import normaliza

    desc = normaliza(descricao)
    for m in db.scalars(select(Material)):
        if normaliza(m.nome) in desc:
            return m.id
    return None


def gasto_etapa(db: Session, etapa_id: int) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(NotaFiscal.valor_total), 0)).where(NotaFiscal.etapa_id == etapa_id)))


def explicar_desvio(db: Session, etapa: Etapa) -> str | None:
    itens = db.scalars(select(NotaItem).join(NotaFiscal).where(NotaFiscal.etapa_id == etapa.id)).all()
    qtd, valor = defaultdict(float), defaultdict(float)
    for i in itens:
        if i.material:
            qtd[i.material] += i.quantidade
            valor[i.material] += i.quantidade * i.preco_unitario

    # 1) preço: material com maior excesso em R$ sobre o preço de referência
    excesso_preco = {m: valor[m] - qtd[m] * m.preco_referencia for m in qtd if m.preco_referencia}
    if excesso_preco and (m := max(excesso_preco, key=excesso_preco.get)) and excesso_preco[m] > 0:
        pct = (valor[m] / qtd[m]) / m.preco_referencia - 1
        return f"{m.nome} {pct:.0%} mais caro que a referência ({brl(excesso_preco[m])} a mais)"

    # 2) consumo: comprou mais do que a composição prevê para a etapa inteira
    previsto = {m: q for m, q in calcular_materiais(db, etapa)}
    excesso_qtd = {m: qtd[m] / previsto[m] - 1 for m in qtd if previsto.get(m)}
    if excesso_qtd and (m := max(excesso_qtd, key=excesso_qtd.get)) and excesso_qtd[m] > 0:
        return f"consumo de {m.nome} {excesso_qtd[m]:.0%} acima da composição"
    return None


def avaliar_etapa(db: Session, obra: Obra, etapa: Etapa) -> SituacaoCusto:
    gasto = gasto_etapa(db, etapa.id)
    desvio = gasto / etapa.orcamento - 1 if etapa.orcamento else 0.0
    motivo = None
    if desvio >= settings.tolerancia_custo:
        motivo = explicar_desvio(db, etapa) or "motivo não identificado nas notas — conferir lançamentos"
        registrar_alerta(
            db, obra_id=obra.id, etapa_id=etapa.id, agente=NOME, tipo="estouro_custo",
            severidade=Severidade.critico if desvio >= 2 * settings.tolerancia_custo else Severidade.atencao,
            mensagem=f"{obra.nome}: {etapa.nome} estourou {desvio:+.0%} do orçamento "
                     f"({brl(gasto)} gasto × {brl(etapa.orcamento)} orçado). Motivo: {motivo}.",
            dados={"gasto": gasto, "orcamento": etapa.orcamento, "desvio": round(desvio, 4), "motivo": motivo},
        )
    else:
        resolver_alertas(db, obra_id=obra.id, tipo="estouro_custo", etapa_id=etapa.id)
    db.flush()
    return SituacaoCusto(etapa, gasto, desvio, motivo)


def executar(db: Session, obra: Obra) -> list[SituacaoCusto]:
    return [avaliar_etapa(db, obra, e) for e in obra.etapas]
