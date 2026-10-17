"""Liga a caixa de entrada única aos agentes e decide quem é avisado.

Fluxo: mensagem chega → vira Evento → agente certo processa → alertas novos
saem no WhatsApp do engenheiro/dono na hora (não no fim do mês).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agentes import compras, cronograma, custo, diario
from .agentes.base import achar_etapa
from .config import settings
from .models import Alerta, Evento, Foto, Obra, Severidade, TipoEvento
from .servicos import transcricao
from .servicos.whatsapp import notificador


def obra_do_remetente(db: Session, telefone: str) -> Obra | None:
    return db.scalar(select(Obra).where(Obra.mestre_whatsapp == telefone, Obra.ativa.is_(True)))


def receber(db: Session, obra: Obra, tipo: TipoEvento, *, dia: date, conteudo: str | None = None,
            arquivo: str | None = None, remetente: str | None = None, origem: str = "whatsapp") -> Evento:
    evento = Evento(obra_id=obra.id, tipo=tipo, conteudo=conteudo, arquivo=arquivo, remetente=remetente, origem=origem)
    db.add(evento)
    db.flush()

    if tipo is TipoEvento.audio and not conteudo and arquivo:
        try:
            evento.conteudo = transcricao.transcrever(arquivo)
        except transcricao.TranscricaoIndisponivel as exc:
            evento.erro = str(exc)
            return evento

    if tipo in (TipoEvento.texto, TipoEvento.audio) and evento.conteudo:
        diario.processar_relato(db, obra, evento.conteudo, dia=dia, evento=evento)
        cronograma.executar(db, obra, dia)
    elif tipo is TipoEvento.foto and arquivo:
        etapa = achar_etapa(obra, conteudo) if conteudo else None
        db.add(Foto(obra_id=obra.id, etapa_id=etapa.id if etapa else None, data=dia, arquivo=arquivo,
                    legenda=conteudo, evento_id=evento.id))
        evento.processado = True
    notificar_alertas(db)
    return evento


def rotina_diaria(db: Session, hoje: date) -> None:
    """Roda todo dia cedo: atualiza atrasos, dispara cotações e reavalia custos."""
    for obra in db.scalars(select(Obra).where(Obra.ativa.is_(True))):
        cronograma.executar(db, obra, hoje)
        compras.executar(db, obra, hoje)
        custo.executar(db, obra)
    notificar_alertas(db)


def notificar_alertas(db: Session) -> list[Alerta]:
    novos = db.scalars(select(Alerta).where(Alerta.notificado.is_(False), Alerta.resolvido.is_(False))).all()
    for a in novos:
        obra = db.get(Obra, a.obra_id)
        destino = obra.engenheiro_whatsapp or settings.dono_whatsapp
        icone = {"critico": "🔴", "atencao": "🟠", "info": "🔵"}[Severidade(a.severidade).value]
        notificador.enviar(destino, f"{icone} [{a.agente}] {a.mensagem}")
        if a.severidade == Severidade.critico and settings.dono_whatsapp and settings.dono_whatsapp != destino:
            notificador.enviar(settings.dono_whatsapp, f"{icone} [{a.agente}] {a.mensagem}")
        a.notificado = True
    db.flush()
    return novos
