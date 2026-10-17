import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///gestao_obra.db"))
    # Claude (opcional). Sem chave, os agentes usam o extrator heurístico.
    anthropic_model: str = field(default_factory=lambda: os.getenv("ANTHROPIC_MODEL", "claude-opus-5-5"))
    usar_llm: bool = field(default_factory=lambda: os.getenv("USAR_LLM", "auto") != "nao")
    # WhatsApp Cloud API (opcional). Sem token, mensagens vão para o log/console.
    whatsapp_token: str | None = field(default_factory=lambda: os.getenv("WHATSAPP_TOKEN"))
    whatsapp_phone_id: str | None = field(default_factory=lambda: os.getenv("WHATSAPP_PHONE_ID"))
    whatsapp_verify_token: str = field(default_factory=lambda: os.getenv("WHATSAPP_VERIFY_TOKEN", "obra"))
    dono_whatsapp: str | None = field(default_factory=lambda: os.getenv("DONO_WHATSAPP"))
    upload_dir: str = field(default_factory=lambda: os.getenv("UPLOAD_DIR", "uploads"))
    # Regras de negócio
    tolerancia_atraso_dias: int = field(default_factory=lambda: int(os.getenv("TOLERANCIA_ATRASO_DIAS", "3")))
    tolerancia_custo: float = field(default_factory=lambda: float(os.getenv("TOLERANCIA_CUSTO", "0.05")))
    antecedencia_compra_dias: int = field(default_factory=lambda: int(os.getenv("ANTECEDENCIA_COMPRA_DIAS", "15")))
    fornecedores_por_cotacao: int = field(default_factory=lambda: int(os.getenv("FORNECEDORES_POR_COTACAO", "3")))


settings = Settings()
