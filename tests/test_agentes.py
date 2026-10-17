from datetime import timedelta

from sqlalchemy import select

from gestao_obra import orquestrador
from gestao_obra.agentes import compras, cronograma, custo, relatorio
from gestao_obra.agentes.base import achar_etapa
from gestao_obra.models import Alerta, Cotacao, DiarioRegistro, Obra, TipoEvento
from gestao_obra.servicos.llm import extrair_diario_heuristico
from gestao_obra.servicos.whatsapp import notificador

from .conftest import HOJE


def obra(db, nome="Obra Jardim") -> Obra:
    return db.scalar(select(Obra).where(Obra.nome == nome))


def etapa(o: Obra, nome: str):
    return next(e for e in o.etapas if e.nome == nome)


def alertas(db, tipo):
    return db.scalars(select(Alerta).where(Alerta.tipo == tipo, Alerta.resolvido.is_(False))).all()


# ------------------------------------------------------------------ agente 1: diário

RELATO = "Hoje fizemos a alvenaria do bloco B, chegamos a 70%. Equipe de 8 pessoas. Travou porque faltou areia."


def test_extrator_heuristico_entende_relato_do_mestre():
    ext = extrair_diario_heuristico(RELATO, ["Alvenaria bloco B", "Alvenaria bloco C", "Acabamento"])
    assert [a.etapa for a in ext.atividades] == ["Alvenaria bloco B"]
    assert ext.atividades[0].percentual_concluido == 70
    assert ext.equipe == 8
    assert ext.materiais_faltando == ["areia"]


def test_achar_etapa_pelo_jeito_que_o_mestre_fala():
    o = Obra(nome="x", etapas=[])
    from gestao_obra.models import Etapa
    o.etapas = [Etapa(nome="Alvenaria bloco B", servico="alvenaria", inicio_previsto=HOJE, fim_previsto=HOJE),
                Etapa(nome="Alvenaria bloco C", servico="alvenaria", inicio_previsto=HOJE, fim_previsto=HOJE)]
    assert achar_etapa(o, "alvenaria B").nome == "Alvenaria bloco B"
    assert achar_etapa(o, "alvenaria bloco c").nome == "Alvenaria bloco C"
    assert achar_etapa(o, "pintura") is None


def test_mensagem_do_whatsapp_vira_diario_e_alerta(db):
    o = obra(db)
    orquestrador.receber(db, o, TipoEvento.texto, dia=HOJE, conteudo=RELATO)
    reg = db.scalar(select(DiarioRegistro))
    assert reg.etapa.nome == "Alvenaria bloco B" and reg.equipe == 8 and "areia" in reg.travou
    assert etapa(o, "Alvenaria bloco B").percentual == 70
    [falta] = alertas(db, "impedimento")
    assert "areia" in falta.mensagem and falta.notificado
    assert any("areia" in m.texto for m in notificador.enviadas)


# ------------------------------------------------------------------ agente 2: cronograma

def test_cronograma_avisa_atraso_cedo_e_atualiza_o_mesmo_alerta(db):
    o = obra(db)
    alv = etapa(o, "Alvenaria bloco B")  # começou há 9 dias, termina em 5
    alv.percentual = 40  # ritmo 4,4%/dia → faltam 14 dias → +9 dias
    cronograma.executar(db, o, HOJE)
    [a] = alertas(db, "atraso")
    assert a.dados["atraso_dias"] == 9 and "Alvenaria bloco B" in a.mensagem

    alv.percentual = 50
    cronograma.executar(db, o, HOJE)
    [a2] = alertas(db, "atraso")
    assert a2.id == a.id and a2.dados["atraso_dias"] == 4

    alv.percentual = 100
    alv.fim_real = HOJE
    cronograma.executar(db, o, HOJE)
    assert alertas(db, "atraso") == []


def test_etapa_que_nao_comecou_atrasa_um_dia_por_dia(db):
    o = obra(db, "Obra Centro")
    alv = etapa(o, "Alvenaria")
    s = cronograma.analisar_etapa(alv, alv.inicio_previsto + timedelta(days=6))
    assert s.atraso_dias == 6


# ------------------------------------------------------------------ agente 3: compras

def test_compras_calcula_material_e_pede_3_cotacoes(db):
    o = obra(db)
    [cot] = compras.executar(db, o, HOJE)
    assert cot.etapa.nome == "Alvenaria bloco C"
    itens = {i.material.nome: i.quantidade for i in cot.itens}
    assert itens == {"bloco cerâmico": 800, "cimento": 40, "areia": 6}
    assert len(cot.respostas) == 3
    assert compras.executar(db, o, HOJE) == []  # não cota duas vezes


def test_compras_escolhe_melhor_preco_no_prazo(db):
    o = obra(db)
    [cot] = compras.executar(db, o, HOJE)
    a, b, c = (r.fornecedor_id for r in cot.respostas)
    compras.registrar_resposta(db, cot, a, 18400, 2, HOJE)
    compras.registrar_resposta(db, cot, c, 19200, 2, HOJE)
    assert cot.status == "aguardando"
    compras.registrar_resposta(db, cot, b, 16900, 3, HOJE)
    assert cot.status == "escolhida"
    assert next(r for r in cot.respostas if r.escolhida).fornecedor.nome == "Fornecedor B"
    [decisao] = alertas(db, "decisao")
    assert "Fornecedor B" in decisao.mensagem and "16.900,00" in decisao.mensagem


def test_compras_descarta_fornecedor_que_nao_entrega_a_tempo(db):
    o = obra(db)
    [cot] = compras.executar(db, o, HOJE)
    a, b, c = (r.fornecedor_id for r in cot.respostas)
    compras.registrar_resposta(db, cot, a, 18400, 2, HOJE)
    compras.registrar_resposta(db, cot, b, 16900, 30, HOJE)  # mais barato, mas chega depois do início
    compras.registrar_resposta(db, cot, c, 19200, 2, HOJE)
    assert next(r for r in cot.respostas if r.escolhida).fornecedor.nome == "Fornecedor A"


# ------------------------------------------------------------------ agente 4: custo

def test_custo_avisa_estouro_com_motivo(db):
    o = obra(db)
    alv = etapa(o, "Alvenaria bloco B")  # orçamento R$ 18.000
    _, s = custo.lancar_nota(db, o, alv, numero="1", data=HOJE, itens=[
        custo.ItemNota("Mão de obra alvenaria", 1, 15000)])
    assert s.motivo is None and alertas(db, "estouro_custo") == []

    _, s = custo.lancar_nota(db, o, alv, numero="2", data=HOJE, itens=[
        custo.ItemNota("Mão de obra alvenaria - medição 2", 1, 2300),
        custo.ItemNota("Cimento CP-II 50kg", 40, 42.56)])  # 12% acima de R$ 38
    assert round(s.desvio, 3) == 0.056
    assert s.motivo.startswith("cimento 12% mais caro")
    [a] = alertas(db, "estouro_custo")
    assert "+6%" in a.mensagem and "cimento 12% mais caro" in a.mensagem


# ------------------------------------------------------------------ agente 5: relatório

def test_relatorio_semanal_dono_e_cliente(db):
    o = obra(db)
    orquestrador.receber(db, o, TipoEvento.foto, dia=HOJE - timedelta(days=2), arquivo="laje.jpg", conteudo="alvenaria B")
    custo.lancar_nota(db, o, etapa(o, "Alvenaria bloco B"), numero="9", data=HOJE - timedelta(days=3),
                      itens=[custo.ItemNota("Cimento", 10, 38)])
    compras.executar(db, o, HOJE)
    notificador.enviadas.clear()

    rels = relatorio.executar_semanal(db, HOJE)
    jardim = next(r for r in rels if r.obra_id == o.id)
    assert jardim.resumo["gasto_semana"] == 380
    assert jardim.resumo["fotos"] == ["laje.jpg"]
    assert "Obra Jardim" in jardim.texto_interno and "R$ 380,00" in jardim.texto_interno
    assert "R$" not in jardim.texto_cliente and "Ana" in jardim.texto_cliente

    para_cliente = next(m for m in notificador.enviadas if m.para == o.cliente_whatsapp)
    assert para_cliente.midias == ["laje.jpg"]


def test_rotina_diaria_roda_todos_os_agentes(db):
    orquestrador.rotina_diaria(db, HOJE)
    assert len(db.scalars(select(Cotacao)).all()) == 2  # alvenaria C (Jardim) e alvenaria (Centro)


def test_extracao_com_claude_monta_requisicao_e_valida_resposta():
    from types import SimpleNamespace

    from gestao_obra.servicos.llm import _extrair_com_claude

    chamada = {}

    class FakeMessages:
        def create(self, **kw):
            chamada.update(kw)
            texto = ('{"atividades":[{"etapa":"Alvenaria bloco B","feito":"subiu 2 fiadas","percentual_concluido":null}],'
                     '"equipe":8,"travou":["faltou areia"],"materiais_faltando":["areia"]}')
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=texto)])

    cliente = SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages()))
    ext = _extrair_com_claude(cliente, RELATO, ["Alvenaria bloco B"])
    assert ext.equipe == 8 and ext.materiais_faltando == ["areia"]
    assert chamada["model"] == "claude-opus-5-5"
    assert chamada["output_config"]["format"]["type"] == "json_schema"
    assert chamada["fallbacks"] == "default"
