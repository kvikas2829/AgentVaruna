import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="varuna-test-")
os.environ["VARUNA_DATA_DIR"] = _tmp
os.environ["VARUNA_SEED"] = "0"
os.environ.pop("ANTHROPIC_API_KEY", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from varuna import db as dbm  # noqa: E402
from varuna.bootstrap import bootstrap  # noqa: E402
from varuna.localgw import LocalTransport  # noqa: E402
from varuna.main import app  # noqa: E402
from varuna.models import Agent  # noqa: E402
from varuna_sdk import VarunaClient  # noqa: E402


@pytest.fixture()
def fresh_db(tmp_path):
    """A clean database with the two demo agents."""
    dbm.init_engine(f"sqlite:///{tmp_path / 't.db'}")
    with dbm.session_scope() as db:
        bootstrap(db)
    yield


@pytest.fixture()
def client(fresh_db):
    with TestClient(app) as c:
        tok = c.post("/v1/auth/login", json={"username": "admin", "password": "varuna"}).json()["token"]
        c.headers["authorization"] = f"Bearer {tok}"
        yield c


@pytest.fixture()
def travel(fresh_db):
    return "agt_travel"


@pytest.fixture()
def legal(fresh_db):
    return "agt_legal"


def sdk(agent_id: str, label: str = "test", source: str = "demo") -> VarunaClient:
    return VarunaClient(LocalTransport(agent_id, source=source), label=label, sleep=lambda s: None)


def agent_key(agent_id: str) -> str:
    with dbm.session_scope() as db:
        return db.get(Agent, agent_id).api_key
