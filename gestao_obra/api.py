from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import orquestrador
from .agentes import compras, cronograma, custo, relatorio
from .config import settings
from .db import get_session, init_db
from .models import Alerta, Cotacao, DiarioRegistro, Etapa, Obra, TipoEvento
from .servicos.whatsapp import baixar_midia

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Gestão de Obra no Automático", version="0.1.0", lifespan=lifespan)


def _obra(db: Session, obra_id: int) -> Obra:
    obra = db.get(Obra, obra_id)
    if not obra:
        raise HTTPException(404, "Obra não encontrada")
    return obra


# ------------------------------------------------------------------ schemas

class EtapaIn(BaseModel):
    nome: str
    servico: str
    ordem: int = 0
    quantidade: float = 0
    unidade: str = "un"
    orcamento: float = 0
    inicio_previsto: date
    fim_previsto: date


class ObraIn(BaseModel):
    nome: str
    endereco: str | None = None
    cliente_nome: str | None = None
    cliente_whatsapp: str | None = None
    mestre_whatsapp: str | None = None
    engenheiro_whatsapp: str | None = None
    etapas: list[EtapaIn] = []


class MensagemIn(BaseModel):
    tipo: TipoEvento = TipoEvento.texto
    conteudo: str | None = None
    arquivo: str | None = None
    data: date | None = None


class ItemNotaIn(BaseModel):
    descricao: str
    quantidade: float = Field(gt=0)
    preco_unitario: float = Field(ge=0)
    material_id: int | None = None


class NotaIn(BaseModel):
    etapa_id: int
    numero: str
    data: date
    fornecedor_id: int | None = None
    itens: list[ItemNotaIn]


class RespostaCotacaoIn(BaseModel):
    fornecedor_id: int
    valor_total: float = Field(gt=0)
    prazo_entrega_dias: int = Field(ge=0)


# ------------------------------------------------------------------ obras

@app.get("/", include_in_schema=False)
def painel_html():
    return FileResponse(STATIC / "index.html")


@app.get("/obras")
def listar_obras(db: Session = Depends(get_session)):
    hoje = date.today()
    saida = []
    for o in db.scalars(select(Obra).where(Obra.ativa.is_(True)).order_by(Obra.nome)):
        situacoes = [cronograma.analisar_etapa(e, hoje) for e in o.etapas]
        saida.append({"id": o.id, "nome": o.nome, "avanco": round(relatorio.avanco_fisico(o), 1),
                      "atraso_dias": cronograma.atraso_da_obra(situacoes)})
    return saida


@app.post("/obras", status_code=201)
def criar_obra(dados: ObraIn, db: Session = Depends(get_session)):
    obra = Obra(**dados.model_dump(exclude={"etapas"}), etapas=[Etapa(**e.model_dump()) for e in dados.etapas])
    db.add(obra)
    db.commit()
    return {"id": obra.id}


@app.get("/obras/{obra_id}")
def detalhe_obra(obra_id: int, hoje: date | None = None, db: Session = Depends(get_session)):
    obra, hoje = _obra(db, obra_id), hoje or date.today()
    etapas = []
    for e in obra.etapas:
        s = cronograma.analisar_etapa(e, hoje)
        gasto = custo.gasto_etapa(db, e.id)
        etapas.append({
            "id": e.id, "nome": e.nome, "percentual": e.percentual, "previsto_hoje": round(s.previsto_hoje, 1),
            "inicio_previsto": e.inicio_previsto, "fim_previsto": e.fim_previsto, "fim_projetado": s.fim_projetado,
            "atraso_dias": s.atraso_dias, "orcamento": e.orcamento, "gasto": gasto,
            "desvio_custo": round(gasto / e.orcamento - 1, 4) if e.orcamento else None,
        })
    return {"id": obra.id, "nome": obra.nome, "cliente": obra.cliente_nome,
            "avanco": round(relatorio.avanco_fisico(obra), 1), "etapas": etapas}


@app.get("/obras/{obra_id}/diario")
def diario_obra(obra_id: int, limite: int = 50, db: Session = Depends(get_session)):
    regs = db.scalars(select(DiarioRegistro).where(DiarioRegistro.obra_id == obra_id)
                      .order_by(DiarioRegistro.data.desc(), DiarioRegistro.id.desc()).limit(limite))
    return [{"data": r.data, "etapa": r.etapa.nome if r.etapa else None, "feito": r.feito, "equipe": r.equipe,
             "travou": r.travou, "percentual_informado": r.percentual_informado} for r in regs]


# ------------------------------------------------------------------ entrada única

@app.post("/obras/{obra_id}/mensagens", status_code=201)
def receber_mensagem(obra_id: int, msg: MensagemIn, db: Session = Depends(get_session)):
    obra = _obra(db, obra_id)
    ev = orquestrador.receber(db, obra, msg.tipo, dia=msg.data or date.today(), conteudo=msg.conteudo,
                              arquivo=msg.arquivo, origem="api")
    db.commit()
    return {"evento_id": ev.id, "processado": ev.processado, "erro": ev.erro}


@app.get("/webhook/whatsapp", response_class=PlainTextResponse)
def verificar_webhook(modo: str = Query(alias="hub.mode"), token: str = Query(alias="hub.verify_token"),
                      desafio: str = Query(alias="hub.challenge")):
    if modo == "subscribe" and token == settings.whatsapp_verify_token:
        return desafio
    raise HTTPException(403)


@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, db: Session = Depends(get_session)):
    """Recebe o payload da WhatsApp Cloud API. O remetente identifica a obra (mestre cadastrado)."""
    payload = await request.json()
    processados = 0
    for entrada in payload.get("entry", []):
        for mudanca in entrada.get("changes", []):
            for m in mudanca.get("value", {}).get("messages", []):
                obra = orquestrador.obra_do_remetente(db, m.get("from", ""))
                if not obra:
                    continue
                tipo = m.get("type")
                if tipo == "text":
                    orquestrador.receber(db, obra, TipoEvento.texto, dia=date.today(),
                                         conteudo=m["text"]["body"], remetente=m["from"])
                elif tipo in ("audio", "image"):
                    arquivo = baixar_midia(m[tipo]["id"], settings.upload_dir)
                    orquestrador.receber(db, obra, TipoEvento.audio if tipo == "audio" else TipoEvento.foto,
                                         dia=date.today(), arquivo=arquivo, remetente=m["from"],
                                         conteudo=m[tipo].get("caption"))
                else:
                    continue
                processados += 1
    db.commit()
    return {"processados": processados}


# ------------------------------------------------------------------ custo

@app.post("/obras/{obra_id}/notas", status_code=201)
def lancar_nota(obra_id: int, nota: NotaIn, db: Session = Depends(get_session)):
    obra = _obra(db, obra_id)
    etapa = db.get(Etapa, nota.etapa_id)
    if not etapa or etapa.obra_id != obra.id:
        raise HTTPException(422, "Etapa não pertence à obra")
    nf, sit = custo.lancar_nota(db, obra, etapa, numero=nota.numero, data=nota.data, fornecedor_id=nota.fornecedor_id,
                                itens=[custo.ItemNota(**i.model_dump()) for i in nota.itens])
    orquestrador.notificar_alertas(db)
    db.commit()
    return {"nota_id": nf.id, "valor_total": nf.valor_total, "gasto_etapa": sit.gasto,
            "desvio": round(sit.desvio, 4), "motivo": sit.motivo}


# ------------------------------------------------------------------ compras

@app.get("/cotacoes")
def listar_cotacoes(status: str | None = None, db: Session = Depends(get_session)):
    q = select(Cotacao).order_by(Cotacao.id.desc())
    if status:
        q = q.where(Cotacao.status == status)
    return [{
        "id": c.id, "obra_id": c.obra_id, "etapa": c.etapa.nome if c.etapa else None, "status": c.status,
        "precisa_ate": c.precisa_ate,
        "itens": [{"material": i.material.nome, "quantidade": i.quantidade, "unidade": i.material.unidade} for i in c.itens],
        "respostas": [{"fornecedor": r.fornecedor.nome, "fornecedor_id": r.fornecedor_id, "valor_total": r.valor_total,
                       "prazo_entrega_dias": r.prazo_entrega_dias, "escolhida": r.escolhida} for r in c.respostas],
    } for c in db.scalars(q)]


@app.post("/cotacoes/{cotacao_id}/respostas")
def responder_cotacao(cotacao_id: int, resp: RespostaCotacaoIn, hoje: date | None = None,
                      db: Session = Depends(get_session)):
    cot = db.get(Cotacao, cotacao_id)
    if not cot:
        raise HTTPException(404, "Cotação não encontrada")
    compras.registrar_resposta(db, cot, resp.fornecedor_id, resp.valor_total, resp.prazo_entrega_dias, hoje or date.today())
    orquestrador.notificar_alertas(db)
    db.commit()
    return {"status": cot.status}


# ------------------------------------------------------------------ alertas e rotinas

@app.get("/alertas")
def listar_alertas(obra_id: int | None = None, abertos: bool = True, db: Session = Depends(get_session)):
    q = select(Alerta).order_by(Alerta.criado_em.desc(), Alerta.id.desc())
    if obra_id:
        q = q.where(Alerta.obra_id == obra_id)
    if abertos:
        q = q.where(Alerta.resolvido.is_(False))
    return [{"id": a.id, "obra_id": a.obra_id, "agente": a.agente, "tipo": a.tipo, "severidade": a.severidade,
             "mensagem": a.mensagem, "dados": a.dados, "criado_em": a.criado_em} for a in db.scalars(q)]


@app.post("/alertas/{alerta_id}/resolver")
def resolver_alerta(alerta_id: int, db: Session = Depends(get_session)):
    alerta = db.get(Alerta, alerta_id)
    if not alerta:
        raise HTTPException(404)
    alerta.resolvido = True
    db.commit()
    return {"ok": True}


@app.post("/rotinas/diaria")
def rodar_rotina_diaria(hoje: date | None = None, db: Session = Depends(get_session)):
    orquestrador.rotina_diaria(db, hoje or date.today())
    db.commit()
    return {"ok": True}


@app.post("/rotinas/relatorio-semanal")
def rodar_relatorio(hoje: date | None = None, enviar: bool = True, db: Session = Depends(get_session)):
    rels = relatorio.executar_semanal(db, hoje or date.today(), enviar=enviar)
    db.commit()
    return [{"obra_id": r.obra_id, "interno": r.texto_interno, "cliente": r.texto_cliente, "resumo": r.resumo} for r in rels]
