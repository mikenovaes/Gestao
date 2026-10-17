from __future__ import annotations

import difflib
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Alerta, Etapa, Obra, Severidade


def normaliza(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return re.sub(r"\s+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def achar_etapa(obra: Obra, nome: str) -> Etapa | None:
    """Casa o nome falado pelo mestre com a etapa cadastrada ("alvenaria B" → "Alvenaria bloco B")."""
    alvo = normaliza(nome)
    por_nome = {normaliza(e.nome): e for e in obra.etapas}
    if alvo in por_nome:
        return por_nome[alvo]
    tokens = set(alvo.split())
    contidas = [e for n, e in por_nome.items() if tokens and tokens <= set(n.split())]
    if len(contidas) == 1:
        return contidas[0]
    parecido = difflib.get_close_matches(alvo, list(por_nome), n=1, cutoff=0.6)
    return por_nome[parecido[0]] if parecido else None


def registrar_alerta(
    db: Session, *, obra_id: int, agente: str, tipo: str, mensagem: str,
    etapa_id: int | None = None, severidade: Severidade = Severidade.atencao,
    dados: dict | None = None, chave: str | None = None,
) -> tuple[Alerta, bool]:
    """Cria ou atualiza o alerta aberto equivalente. Retorna (alerta, é_novo).

    Um atraso que passa de +4 para +6 dias atualiza o mesmo alerta em vez de
    gerar outro — o dono recebe o aviso uma vez e vê o número evoluir.
    """
    dados = dict(dados or {})
    if chave:
        dados["chave"] = chave
    abertos = db.scalars(select(Alerta).where(
        Alerta.obra_id == obra_id, Alerta.tipo == tipo, Alerta.etapa_id == etapa_id, Alerta.resolvido.is_(False),
    )).all()
    existente = next((a for a in abertos if a.dados.get("chave") == chave), None)
    if existente:
        existente.mensagem, existente.dados, existente.severidade = mensagem, dados, severidade
        return existente, False
    alerta = Alerta(obra_id=obra_id, etapa_id=etapa_id, agente=agente, tipo=tipo,
                    mensagem=mensagem, severidade=severidade, dados=dados)
    db.add(alerta)
    db.flush()
    return alerta, True


def resolver_alertas(db: Session, *, obra_id: int, tipo: str, etapa_id: int | None) -> None:
    for a in db.scalars(select(Alerta).where(
        Alerta.obra_id == obra_id, Alerta.tipo == tipo, Alerta.etapa_id == etapa_id, Alerta.resolvido.is_(False),
    )):
        a.resolvido = True


def brl(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"
