# Gestão de Obra no Automático

Sistema para construtoras baseado em **uma base única de informação + 5 agentes**,
cada um cuidando de um gargalo da obra. Não é um "software mágico": é tudo que
chega da obra (áudio, foto, nota fiscal, planilha, e-mail) caindo num lugar só,
e agentes que leem essa base e avisam cedo.

```
 o que chega            base única                 agentes                                  você
 ───────────            ──────────                 ───────                                  ────
 áudio do mestre ─┐                         ┌─► A1 diário      (transcreve, organiza por etapa)
 foto da laje    ─┤                         ├─► A2 cronograma  (planejado × real, aviso na 1ª semana)
 nota fiscal     ─┼─► Evento → SQLite/PG ───┼─► A3 compras     (material da próxima etapa, 3 cotações) ─► WhatsApp
 planilha        ─┤                         ├─► A4 custo       (NF × orçamento, estouro de 5% + motivo)
 e-mail          ─┘                         └─► A5 relatório   (segunda 7h: dono e cliente)
```

| Antes | Depois |
|---|---|
| apagar incêndio | diário automático |
| planilha atrasada | atraso avisado cedo |
| atraso descoberto no fim do mês | compra no tempo |
| nota sem conferir | custo vigiado |
| obra esperando material | relatório na segunda |

## Os agentes

| # | Agente | Entrada | O que faz | Saída |
|---|---|---|---|---|
| 1 | **Diário** (`agentes/diario.py`) | áudio/texto/foto do mestre no WhatsApp | transcreve e extrai *o que foi feito, equipe e o que travou* por etapa (Claude com saída estruturada; fallback por regras) e atualiza o avanço | `DiarioRegistro`, `Foto`, alerta de impedimento ("faltou areia") |
| 2 | **Cronograma** (`agentes/cronograma.py`) | diário + datas planejadas | projeta o fim pelo ritmo real (`avanço ÷ dias trabalhados`) e compara com o planejado | alerta "Alvenaria bloco B está 6 dias atrasada" — atualiza o mesmo alerta, sem spam |
| 3 | **Compras** (`agentes/compras.py`) | cronograma + composições | etapa começando em ≤ 15 dias → calcula o material (quantidade × consumo) → pede cotação a 3 fornecedores → escolhe o menor preço **que entrega a tempo** | `Cotacao` + decisão pendente "aprovar compra com Fornecedor B por R$ 16.900" |
| 4 | **Custo** (`agentes/custo.py`) | cada nota fiscal | lança contra o orçamento da etapa; estourou 5% → avisa na hora com o motivo (preço acima da referência ou consumo acima da composição) | alerta "Alvenaria estourou +6%. Motivo: cimento 12% mais caro" |
| 5 | **Relatório** (`agentes/relatorio.py`) | tudo acima | segunda 7h: resumo por obra (avanço, atrasos, gasto da semana, decisões pendentes) para o dono; versão sem custos e com as fotos da semana para o cliente | mensagens no WhatsApp + `Relatorio` salvo |

Os agentes **não conversam entre si**: todos leem e escrevem na base
(`models.py`). Isso permite implantar na ordem recomendada — **um agente por mês,
testado em uma obra antes de levar para todas**:

1. mês 1 — diário (alimenta todos os outros)
2. mês 2 — cronograma
3. mês 3 — compras
4. mês 4 — custo
5. mês 5 — relatório

## Rodando

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m gestao_obra.cli seed      # obras de exemplo (Obra Jardim, Obra Centro)
.venv/bin/python -m gestao_obra.cli servir    # http://localhost:8000 (painel) e /docs (API)
.venv/bin/python -m pytest                    # testes
```

Simulando o mestre mandando o relato do dia:

```bash
curl -X POST localhost:8000/obras/1/mensagens -H 'content-type: application/json' \
  -d '{"conteudo": "Alvenaria do bloco B chegou a 58%. Equipe de 8 pessoas. Travou: faltou areia."}'
```

### Rotinas agendadas (cron)

```cron
CRON_TZ=America/Sao_Paulo
0 6 * * *  cd /srv/gestao && .venv/bin/python -m gestao_obra.cli diaria    # cronograma, compras, custo
0 7 * * 1  cd /srv/gestao && .venv/bin/python -m gestao_obra.cli semanal   # relatório de segunda
```

## Configuração (variáveis de ambiente)

| Variável | Padrão | Uso |
|---|---|---|
| `DATABASE_URL` | `sqlite:///gestao_obra.db` | use PostgreSQL em produção |
| `ANTHROPIC_API_KEY` | — | ativa a extração do diário com Claude (`claude-opus-5-5`, saída em JSON Schema, fallback automático de modelo em caso de recusa). Sem chave, usa o extrator por regras |
| `ANTHROPIC_MODEL` / `USAR_LLM=nao` | `claude-opus-5-5` | trocar o modelo / desligar o LLM |
| `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN` | — | WhatsApp Cloud API. Sem token, as mensagens só vão para o log |
| `DONO_WHATSAPP` | — | destino do relatório de segunda e dos alertas críticos |
| `TOLERANCIA_ATRASO_DIAS` | `3` | a partir de quantos dias o atraso vira alerta |
| `TOLERANCIA_CUSTO` | `0.05` | estouro de orçamento que dispara alerta (5%) |
| `ANTECEDENCIA_COMPRA_DIAS` | `15` | quantos dias antes da etapa o agente de compras cota |
| `FORNECEDORES_POR_COTACAO` | `3` | |

Áudios são transcritos localmente com `faster-whisper`, se instalado
(`pip install faster-whisper`); sem ele o evento fica registrado com erro e o
texto pode ser reenviado pela API.

## API principal

| Método | Rota | |
|---|---|---|
| `GET/POST` | `/webhook/whatsapp` | verificação e recebimento da Cloud API (a obra é identificada pelo telefone do mestre) |
| `POST` | `/obras/{id}/mensagens` | entrada manual de texto/áudio/foto |
| `GET` | `/obras`, `/obras/{id}`, `/obras/{id}/diario` | painel: avanço, previsto × real, fim projetado, gasto × orçado |
| `POST` | `/obras/{id}/notas` | lança nota fiscal (dispara o agente de custo) |
| `GET` / `POST` | `/cotacoes`, `/cotacoes/{id}/respostas` | cotações e respostas dos fornecedores |
| `GET` / `POST` | `/alertas`, `/alertas/{id}/resolver` | alertas e decisões pendentes |
| `POST` | `/rotinas/diaria`, `/rotinas/relatorio-semanal` | rodar as rotinas sob demanda |

## Próximos passos sugeridos

- Leitura automática de NF-e (XML da SEFAZ) e de e-mails de fornecedores, para alimentar compras/custo sem digitação.
- Resposta de cotação pelo próprio WhatsApp do fornecedor (extração de valor/prazo com Claude).
- Caminho crítico com dependências entre etapas (hoje o atraso da obra é o maior atraso entre as etapas abertas).
- Autenticação e perfis (dono, engenheiro, mestre, cliente) antes de expor a API publicamente.
