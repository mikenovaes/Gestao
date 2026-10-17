"""Extração estruturada do diário de obra com Claude, com fallback heurístico.

Sem ANTHROPIC_API_KEY (ou com USAR_LLM=nao) o sistema continua funcionando com
um extrator por regras — pior para áudios longos e confusos, mas previsível.
"""
from __future__ import annotations

import json
import logging
import os
import re

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings

log = logging.getLogger(__name__)


class AtividadeExtraida(BaseModel):
    model_config = ConfigDict(extra="forbid")

    etapa: str = Field(description="Nome da etapa exatamente como aparece na lista de etapas da obra")
    feito: str = Field(description="O que foi executado no dia, em uma frase objetiva")
    percentual_concluido: float | None = Field(description="Avanço total da etapa (0-100) se o mestre informou, senão null")


class DiarioExtraido(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atividades: list[AtividadeExtraida]
    equipe: int | None = Field(description="Quantidade de pessoas trabalhando no dia, ou null")
    travou: list[str] = Field(description="Impedimentos do dia (falta de material, chuva, equipamento...)")
    materiais_faltando: list[str] = Field(description="Materiais citados como em falta, nome simples no singular")


SISTEMA = """Você organiza o diário de obra de uma construtora brasileira.
Recebe a transcrição de um áudio ou mensagem do mestre de obras no fim do dia e
a lista de etapas da obra. Extraia somente o que foi dito: não invente avanço,
equipe ou impedimentos. Associe cada atividade a uma etapa da lista; se nenhuma
servir, use o nome mais próximo citado pelo mestre."""


def _cliente():
    if not settings.usar_llm or not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        return None
    import anthropic

    return anthropic.Anthropic()


def extrair_diario(texto: str, etapas: list[str]) -> DiarioExtraido:
    cliente = _cliente()
    if cliente is not None:
        try:
            return _extrair_com_claude(cliente, texto, etapas)
        except Exception:  # rede, recusa, schema: o diário não pode parar
            log.exception("Falha na extração com Claude; usando heurística")
    return extrair_diario_heuristico(texto, etapas)


def _extrair_com_claude(cliente, texto: str, etapas: list[str]) -> DiarioExtraido:
    import anthropic

    resposta = cliente.beta.messages.create(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=SISTEMA,
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": anthropic.transform_schema(DiarioExtraido)},
        },
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{
            "role": "user",
            "content": "Etapas da obra:\n" + "\n".join(f"- {e}" for e in etapas) + f"\n\nRelato do mestre:\n{texto}",
        }],
    )
    if resposta.stop_reason == "refusal":
        raise RuntimeError(f"Extração recusada: {resposta.stop_details}")
    bruto = next(b.text for b in resposta.content if b.type == "text")
    return DiarioExtraido.model_validate(json.loads(bruto))


# ---------------------------------------------------------------- heurística

_NUM = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
        "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "quinze": 15, "vinte": 20}
_TRAVOU = re.compile(r"\b(faltou|falta|faltando|parou|parado|travou|chuva|choveu|sem)\b[^.;\n]*", re.I)
_FALTOU_MATERIAL = re.compile(r"\b(?:faltou|falta|faltando|acabou|sem)\s+(?:o |a |os |as )?([a-zà-ú]+)", re.I)
_EQUIPE = re.compile(r"(\d+|" + "|".join(_NUM) + r")\s+(?:pessoas|homens|funcion[aá]rios|pedreiros|serventes|colaboradores)", re.I)
_PERCENT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")


def _normaliza(s: str) -> str:
    import unicodedata

    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def extrair_diario_heuristico(texto: str, etapas: list[str]) -> DiarioExtraido:
    frases = [f.strip() for f in re.split(r"[.!?\n]+", texto) if f.strip()]
    atividades: list[AtividadeExtraida] = []
    for etapa in etapas:
        palavras = set(re.findall(r"\w+", _normaliza(etapa))) - {"de", "da", "do"}
        for frase in frases:
            tokens = set(re.findall(r"\w+", _normaliza(frase)))
            if palavras <= tokens and not _TRAVOU.match(frase):
                pct = _PERCENT.search(frase)
                atividades.append(AtividadeExtraida(
                    etapa=etapa, feito=frase,
                    percentual_concluido=float(pct.group(1).replace(",", ".")) if pct else None,
                ))
                break

    equipe = None
    if m := _EQUIPE.search(texto):
        bruto = m.group(1).lower()
        equipe = int(bruto) if bruto.isdigit() else _NUM.get(bruto)

    travou = [m.group(0).strip() for m in _TRAVOU.finditer(texto)]
    faltando = sorted({m.group(1).lower() for m in _FALTOU_MATERIAL.finditer(texto)})
    return DiarioExtraido(atividades=atividades, equipe=equipe, travou=travou, materiais_faltando=faltando)
