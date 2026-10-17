"""Envio de mensagens. Sem credenciais, guarda as mensagens em memória (útil em dev e testes)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from ..config import settings

log = logging.getLogger(__name__)


@dataclass
class Mensagem:
    para: str
    texto: str
    midias: list[str] = field(default_factory=list)


class Notificador:
    def __init__(self) -> None:
        self.enviadas: list[Mensagem] = []

    def enviar(self, para: str | None, texto: str, midias: list[str] | None = None) -> None:
        if not para:
            log.warning("Destinatário vazio; mensagem descartada: %s", texto[:80])
            return
        msg = Mensagem(para, texto, midias or [])
        self.enviadas.append(msg)
        if settings.whatsapp_token and settings.whatsapp_phone_id:
            self._enviar_cloud_api(msg)
        else:
            log.info("[WhatsApp → %s]\n%s", para, texto)

    def _enviar_cloud_api(self, msg: Mensagem) -> None:
        url = f"https://graph.facebook.com/v21.0/{settings.whatsapp_phone_id}/messages"
        headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
        with httpx.Client(timeout=20) as client:
            client.post(url, headers=headers, json={
                "messaging_product": "whatsapp", "to": msg.para, "type": "text", "text": {"body": msg.texto},
            }).raise_for_status()
            for link in msg.midias:
                client.post(url, headers=headers, json={
                    "messaging_product": "whatsapp", "to": msg.para, "type": "image", "image": {"link": link},
                }).raise_for_status()


notificador = Notificador()


def baixar_midia(media_id: str, destino_dir: str) -> str:
    """Baixa áudio/foto recebido pela Cloud API e devolve o caminho local."""
    import os

    if not settings.whatsapp_token:
        return f"whatsapp-media:{media_id}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}
    with httpx.Client(timeout=60) as client:
        meta = client.get(f"https://graph.facebook.com/v21.0/{media_id}", headers=headers).raise_for_status().json()
        conteudo = client.get(meta["url"], headers=headers).raise_for_status().content
    extensao = {"audio/ogg": ".ogg", "image/jpeg": ".jpg", "image/png": ".png"}.get(meta.get("mime_type", ""), "")
    os.makedirs(destino_dir, exist_ok=True)
    caminho = os.path.join(destino_dir, f"{media_id}{extensao}")
    with open(caminho, "wb") as f:
        f.write(conteudo)
    return caminho
