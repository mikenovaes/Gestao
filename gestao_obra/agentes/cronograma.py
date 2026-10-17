"""Agente 2 — Cronograma.

Cruza o diário (avanço real) com o planejado e avisa na primeira semana do
atraso: "alvenaria do bloco B está 6 dias atrasada".

Projeção: ritmo real = avanço / dias trabalhados desde o início real; o fim
projetado é hoje + (o que falta / ritmo). Etapa que deveria ter começado e não
começou atrasa um dia por dia parado.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Etapa, Obra, Severidade
from .base import registrar_alerta, resolver_alertas

NOME = "cronograma"


@dataclass
class Situacao:
    etapa: Etapa
    previsto_hoje: float  # % que deveria estar pronto hoje
    fim_projetado: date | None
    atraso_dias: int


def percentual_previsto(etapa: Etapa, hoje: date) -> float:
    if hoje <= etapa.inicio_previsto:
        return 0.0
    if hoje >= etapa.fim_previsto:
        return 100.0
    total = (etapa.fim_previsto - etapa.inicio_previsto).days or 1
    return 100.0 * (hoje - etapa.inicio_previsto).days / total


def analisar_etapa(etapa: Etapa, hoje: date) -> Situacao:
    previsto = percentual_previsto(etapa, hoje)
    if etapa.fim_real:
        return Situacao(etapa, previsto, etapa.fim_real, max(0, (etapa.fim_real - etapa.fim_previsto).days))

    if etapa.inicio_real is None:
        if hoje <= etapa.inicio_previsto:
            return Situacao(etapa, previsto, None, 0)
        duracao = etapa.fim_previsto - etapa.inicio_previsto
        fim = hoje + duracao
        return Situacao(etapa, previsto, fim, (fim - etapa.fim_previsto).days)

    dias_trabalhados = max(1, (hoje - etapa.inicio_real).days)
    if etapa.percentual <= 0:
        fim = hoje + (etapa.fim_previsto - etapa.inicio_previsto)
    else:
        ritmo = etapa.percentual / dias_trabalhados
        fim = hoje + timedelta(days=math.ceil((100 - etapa.percentual) / ritmo))
    return Situacao(etapa, previsto, fim, max(0, (fim - etapa.fim_previsto).days))


def executar(db: Session, obra: Obra, hoje: date) -> list[Situacao]:
    situacoes = [analisar_etapa(e, hoje) for e in obra.etapas]
    for s in situacoes:
        if s.atraso_dias >= settings.tolerancia_atraso_dias and not s.etapa.concluida:
            sev = Severidade.critico if s.atraso_dias >= 2 * settings.tolerancia_atraso_dias + 1 else Severidade.atencao
            registrar_alerta(
                db, obra_id=obra.id, etapa_id=s.etapa.id, agente=NOME, tipo="atraso", severidade=sev,
                mensagem=f"{obra.nome}: {s.etapa.nome} está {s.atraso_dias} dias atrasada "
                         f"(real {s.etapa.percentual:.0f}% × previsto {s.previsto_hoje:.0f}%; "
                         f"fim projetado {s.fim_projetado:%d/%m}, planejado {s.etapa.fim_previsto:%d/%m}).",
                dados={"atraso_dias": s.atraso_dias, "fim_projetado": s.fim_projetado.isoformat() if s.fim_projetado else None},
            )
        elif s.atraso_dias == 0 or s.etapa.concluida:
            resolver_alertas(db, obra_id=obra.id, tipo="atraso", etapa_id=s.etapa.id)
    db.flush()
    return situacoes


def atraso_da_obra(situacoes: list[Situacao]) -> int:
    """Atraso da obra = maior atraso entre as etapas não concluídas (aproximação sem caminho crítico)."""
    return max((s.atraso_dias for s in situacoes if not s.etapa.concluida), default=0)
