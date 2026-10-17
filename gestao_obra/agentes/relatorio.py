"""Agente 5 — Relatório.

Toda segunda às 7h chega no WhatsApp do dono um resumo de cada obra: avanço,
atrasos, gastos e decisões pendentes. O cliente final recebe a versão dele
(sem custos), com as fotos da semana.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Alerta, Foto, NotaFiscal, Obra, Relatorio
from ..servicos.whatsapp import notificador
from . import cronograma
from .base import brl

NOME = "relatorio"


def avanco_fisico(obra: Obra) -> float:
    """Avanço ponderado pelo orçamento de cada etapa (peso financeiro, como numa medição)."""
    total = sum(e.orcamento for e in obra.etapas) or 1
    return sum(e.percentual * e.orcamento for e in obra.etapas) / total


def montar_resumo(db: Session, obra: Obra, hoje: date) -> dict:
    inicio_semana = hoje - timedelta(days=7)
    situacoes = cronograma.executar(db, obra, hoje)
    abertos = db.scalars(select(Alerta).where(Alerta.obra_id == obra.id, Alerta.resolvido.is_(False))).all()
    gasto_semana = db.scalar(select(func.coalesce(func.sum(NotaFiscal.valor_total), 0)).where(
        NotaFiscal.obra_id == obra.id, NotaFiscal.data >= inicio_semana, NotaFiscal.data < hoje))
    gasto_total = db.scalar(select(func.coalesce(func.sum(NotaFiscal.valor_total), 0)).where(NotaFiscal.obra_id == obra.id))
    fotos = db.scalars(select(Foto).where(Foto.obra_id == obra.id, Foto.data >= inicio_semana, Foto.data < hoje)
                       .order_by(Foto.data)).all()
    return {
        "obra": obra.nome,
        "data": hoje.isoformat(),
        "avanco": round(avanco_fisico(obra), 1),
        "atraso_dias": cronograma.atraso_da_obra(situacoes),
        "etapas_atrasadas": [{"etapa": s.etapa.nome, "dias": s.atraso_dias} for s in situacoes
                             if s.atraso_dias >= settings.tolerancia_atraso_dias and not s.etapa.concluida],
        "gasto_semana": float(gasto_semana),
        "gasto_total": float(gasto_total),
        "orcamento_total": float(sum(e.orcamento for e in obra.etapas)),
        "decisoes_pendentes": [a.mensagem for a in abertos if a.tipo == "decisao"],
        "alertas": [a.mensagem for a in abertos if a.tipo != "decisao"],
        "fotos": [f.arquivo for f in fotos],
    }


def texto_interno(r: dict) -> str:
    status = f"✅ {r['obra']}: {r['avanco']:.0f}%" if r["atraso_dias"] < settings.tolerancia_atraso_dias \
        else f"❌ {r['obra']}: {r['avanco']:.0f}% · +{r['atraso_dias']} dias"
    linhas = [status, f"   gasto da semana: {brl(r['gasto_semana'])} "
                      f"(acumulado {brl(r['gasto_total'])} de {brl(r['orcamento_total'])})"]
    linhas += [f"   ⏱ {e['etapa']}: +{e['dias']} dias" for e in r["etapas_atrasadas"]]
    linhas += [f"   ⚠ {a}" for a in r["alertas"] if "atrasada" not in a]
    if r["decisoes_pendentes"]:
        linhas.append(f"   🟠 {len(r['decisoes_pendentes'])} decisões pendentes:")
        linhas += [f"      - {d}" for d in r["decisoes_pendentes"]]
    return "\n".join(linhas)


def texto_cliente(obra: Obra, r: dict) -> str:
    nome = obra.cliente_nome or "cliente"
    concluidas = [e.nome for e in obra.etapas if e.concluida]
    andamento = [f"{e.nome} ({e.percentual:.0f}%)" for e in obra.etapas if 0 < e.percentual < 100]
    linhas = [f"Olá, {nome}! Resumo semanal da {obra.nome}:", f"Avanço geral: {r['avanco']:.0f}%"]
    if andamento:
        linhas.append("Em andamento: " + ", ".join(andamento))
    if concluidas:
        linhas.append("Concluído: " + ", ".join(concluidas))
    if r["fotos"]:
        linhas.append(f"Seguem {len(r['fotos'])} fotos da semana.")
    return "\n".join(linhas)


def gerar(db: Session, obra: Obra, hoje: date) -> Relatorio:
    r = montar_resumo(db, obra, hoje)
    rel = Relatorio(obra_id=obra.id, data=hoje, resumo=r, texto_interno=texto_interno(r), texto_cliente=texto_cliente(obra, r))
    db.add(rel)
    db.flush()
    return rel


def executar_semanal(db: Session, hoje: date, *, enviar: bool = True) -> list[Relatorio]:
    obras = db.scalars(select(Obra).where(Obra.ativa.is_(True)).order_by(Obra.nome)).all()
    relatorios = [gerar(db, o, hoje) for o in obras]
    if enviar:
        cabecalho = f"Segunda, {hoje:%d/%m} — resumo das obras"
        notificador.enviar(settings.dono_whatsapp, "\n\n".join([cabecalho] + [r.texto_interno for r in relatorios]))
        for obra, rel in zip(obras, relatorios):
            notificador.enviar(obra.cliente_whatsapp, rel.texto_cliente, midias=rel.resumo["fotos"])
    return relatorios
