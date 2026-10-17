import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from gestao_obra.api import app
from gestao_obra.db import get_session

from .conftest import HOJE


@pytest.fixture
def client(engine, db):
    fabrica = sessionmaker(bind=engine, expire_on_commit=False)

    def _sessao():
        with fabrica() as s:
            yield s

    app.dependency_overrides[get_session] = _sessao
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_fluxo_completo_pela_api(client):
    obras = client.get("/obras").json()
    jardim = next(o for o in obras if o["nome"] == "Obra Jardim")

    r = client.post(f"/obras/{jardim['id']}/mensagens", json={
        "conteudo": "Alvenaria do bloco B chegou a 60%. 8 pessoas. Faltou areia.", "data": HOJE.isoformat()})
    assert r.status_code == 201 and r.json()["processado"]

    diario = client.get(f"/obras/{jardim['id']}/diario").json()
    assert diario[0]["etapa"] == "Alvenaria bloco B" and diario[0]["equipe"] == 8

    detalhe = client.get(f"/obras/{jardim['id']}", params={"hoje": HOJE.isoformat()}).json()
    alv = next(e for e in detalhe["etapas"] if e["nome"] == "Alvenaria bloco B")
    assert alv["percentual"] == 60

    r = client.post(f"/obras/{jardim['id']}/notas", json={
        "etapa_id": alv["id"], "numero": "123", "data": HOJE.isoformat(),
        "itens": [{"descricao": "Mão de obra", "quantidade": 1, "preco_unitario": 17300},
                  {"descricao": "Cimento", "quantidade": 40, "preco_unitario": 42.56}]})
    assert r.json()["motivo"].startswith("cimento 12% mais caro")

    client.post("/rotinas/diaria", params={"hoje": HOJE.isoformat()})
    cot = next(c for c in client.get("/cotacoes").json() if c["etapa"] == "Alvenaria bloco C")
    for resp, valor in zip(cot["respostas"], [18400, 16900, 19200]):
        client.post(f"/cotacoes/{cot['id']}/respostas", params={"hoje": HOJE.isoformat()},
                    json={"fornecedor_id": resp["fornecedor_id"], "valor_total": valor, "prazo_entrega_dias": 2})

    tipos = {a["tipo"] for a in client.get("/alertas").json()}
    assert {"impedimento", "estouro_custo", "decisao"} <= tipos

    rels = client.post("/rotinas/relatorio-semanal", params={"hoje": HOJE.isoformat(), "enviar": False}).json()
    assert any("decisões pendentes" in r["interno"] for r in rels)


def test_webhook_whatsapp(client):
    assert client.get("/webhook/whatsapp", params={
        "hub.mode": "subscribe", "hub.verify_token": "obra", "hub.challenge": "42"}).text == "42"
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "5511922220000", "type": "text", "text": {"body": "Alvenaria bloco B 65%, 7 pessoas."}},
        {"from": "5500000000000", "type": "text", "text": {"body": "número desconhecido"}},
    ]}}]}]}
    assert client.post("/webhook/whatsapp", json=payload).json() == {"processados": 1}
