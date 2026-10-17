"""Agente 1 — Diário de obra.

O mestre manda áudio/texto/foto no WhatsApp no fim do dia. O agente transcreve,
organiza por etapa e registra o que foi feito, quem trabalhou e o que travou.
Ele alimenta todos os outros agentes, por isso é o primeiro a ser implantado.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from ..models import DiarioRegistro, Evento, Obra, Severidade
from ..servicos.llm import DiarioExtraido, extrair_diario
from .base import achar_etapa, registrar_alerta

NOME = "diario"


def processar_relato(db: Session, obra: Obra, texto: str, *, dia: date, evento: Evento | None = None) -> list[DiarioRegistro]:
    extraido: DiarioExtraido = extrair_diario(texto, [e.nome for e in obra.etapas])
    travou = "; ".join(extraido.travou) or None
    registros: list[DiarioRegistro] = []

    for atv in extraido.atividades:
        etapa = achar_etapa(obra, atv.etapa)
        if etapa:
            if etapa.inicio_real is None:
                etapa.inicio_real = dia
            if atv.percentual_concluido is not None:
                etapa.percentual = max(etapa.percentual, min(100.0, atv.percentual_concluido))
            if etapa.percentual >= 100 and etapa.fim_real is None:
                etapa.fim_real = dia
        registros.append(DiarioRegistro(
            obra_id=obra.id, etapa_id=etapa.id if etapa else None, data=dia, feito=atv.feito,
            equipe=extraido.equipe, travou=travou, percentual_informado=atv.percentual_concluido,
            evento_id=evento.id if evento else None,
        ))

    if not registros:  # dia sem atividade identificável ainda vira registro (ex.: "choveu, não teve serviço")
        registros.append(DiarioRegistro(
            obra_id=obra.id, data=dia, feito=texto[:500], equipe=extraido.equipe, travou=travou,
            evento_id=evento.id if evento else None,
        ))

    for material in extraido.materiais_faltando:
        registrar_alerta(
            db, obra_id=obra.id, agente=NOME, tipo="impedimento", severidade=Severidade.critico,
            mensagem=f"{obra.nome}: obra travou por falta de {material} ({dia:%d/%m}).",
            dados={"material": material, "data": dia.isoformat()}, chave=f"falta:{material}",
        )

    db.add_all(registros)
    if evento:
        evento.processado = True
    db.flush()
    return registros
