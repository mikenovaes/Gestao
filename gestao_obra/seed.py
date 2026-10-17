"""Dados de demonstração: duas obras, materiais, composições e 3 fornecedores."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Composicao, Etapa, Fornecedor, Material, Obra

MATERIAIS = [
    ("bloco cerâmico", "un", 1.95),
    ("cimento", "saco", 38.00),
    ("areia", "m³", 120.00),
    ("aço CA-50", "kg", 7.50),
    ("concreto usinado", "m³", 520.00),
]

# consumo por unidade do serviço (a perda já está embutida no consumo)
COMPOSICOES = {
    "alvenaria": [("bloco cerâmico", 12.5), ("cimento", 0.625), ("areia", 0.09375)],  # por m²
    "estrutura": [("concreto usinado", 1.0), ("aço CA-50", 90.0)],  # por m³
    "fundacao": [("concreto usinado", 1.0), ("aço CA-50", 60.0)],  # por m³
}


def popular(db: Session, hoje: date | None = None) -> None:
    if db.scalar(select(Obra.id)):
        return
    hoje = hoje or date.today()
    d = lambda n: hoje + timedelta(days=n)  # noqa: E731

    materiais = {nome: Material(nome=nome, unidade=un, preco_referencia=p) for nome, un, p in MATERIAIS}
    db.add_all(materiais.values())
    for servico, itens in COMPOSICOES.items():
        db.add_all(Composicao(servico=servico, material=materiais[m], consumo=c, perda=0) for m, c in itens)
    db.add_all([
        Fornecedor(nome="Fornecedor A", whatsapp="5511900000001"),
        Fornecedor(nome="Fornecedor B", whatsapp="5511900000002"),
        Fornecedor(nome="Fornecedor C", whatsapp="5511900000003"),
    ])

    db.add(Obra(
        nome="Obra Jardim", cliente_nome="Ana", cliente_whatsapp="5511911110000",
        mestre_whatsapp="5511922220000", engenheiro_whatsapp="5511933330000",
        etapas=[
            Etapa(nome="Fundação", servico="fundacao", ordem=1, quantidade=30, unidade="m³", orcamento=48000,
                  inicio_previsto=d(-60), fim_previsto=d(-40), inicio_real=d(-60), fim_real=d(-39), percentual=100),
            Etapa(nome="Estrutura", servico="estrutura", ordem=2, quantidade=55, unidade="m³", orcamento=95000,
                  inicio_previsto=d(-40), fim_previsto=d(-10), inicio_real=d(-38), fim_real=d(-9), percentual=100),
            Etapa(nome="Alvenaria bloco B", servico="alvenaria", ordem=3, quantidade=64, unidade="m²", orcamento=18000,
                  inicio_previsto=d(-10), fim_previsto=d(5), inicio_real=d(-9), percentual=55),
            Etapa(nome="Alvenaria bloco C", servico="alvenaria", ordem=4, quantidade=64, unidade="m²", orcamento=18000,
                  inicio_previsto=d(6), fim_previsto=d(20)),
            Etapa(nome="Acabamento", servico="acabamento", ordem=5, quantidade=1, unidade="vb", orcamento=60000,
                  inicio_previsto=d(21), fim_previsto=d(60)),
        ],
    ))
    db.add(Obra(
        nome="Obra Centro", cliente_nome="Carlos", cliente_whatsapp="5511944440000",
        mestre_whatsapp="5511955550000", engenheiro_whatsapp="5511933330000",
        etapas=[
            Etapa(nome="Fundação", servico="fundacao", ordem=1, quantidade=20, unidade="m³", orcamento=32000,
                  inicio_previsto=d(-30), fim_previsto=d(-15), inicio_real=d(-30), fim_real=d(-14), percentual=100),
            Etapa(nome="Estrutura", servico="estrutura", ordem=2, quantidade=40, unidade="m³", orcamento=70000,
                  inicio_previsto=d(-15), fim_previsto=d(5), inicio_real=d(-12), percentual=45),
            Etapa(nome="Alvenaria", servico="alvenaria", ordem=3, quantidade=120, unidade="m²", orcamento=32000,
                  inicio_previsto=d(6), fim_previsto=d(25)),
        ],
    ))
    db.flush()
