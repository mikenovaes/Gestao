"""Transcrição de áudio do mestre de obra.

O padrão usa o `faster-whisper` local (sem enviar áudio para terceiros) se estiver
instalado: `pip install faster-whisper`. Sem ele, o evento fica marcado como
pendente de transcrição e o texto pode ser enviado depois pela API.
"""
from __future__ import annotations

from functools import lru_cache


class TranscricaoIndisponivel(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _modelo():
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depende do ambiente
        raise TranscricaoIndisponivel("Instale faster-whisper para transcrever áudios.") from exc
    return WhisperModel("small", compute_type="int8")


def transcrever(caminho_audio: str) -> str:
    segmentos, _ = _modelo().transcribe(caminho_audio, language="pt")
    return " ".join(s.text.strip() for s in segmentos)
