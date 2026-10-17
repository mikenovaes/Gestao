import os
import tempfile
from datetime import date

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/teste.db")
os.environ["USAR_LLM"] = "nao"
os.environ.pop("WHATSAPP_TOKEN", None)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from gestao_obra.db import Base
from gestao_obra import models  # noqa: F401
from gestao_obra.seed import popular
from gestao_obra.servicos.whatsapp import notificador

HOJE = date(2026, 10, 19)  # uma segunda-feira


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    notificador.enviadas.clear()
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        popular(session, HOJE)
        session.commit()
        yield session
