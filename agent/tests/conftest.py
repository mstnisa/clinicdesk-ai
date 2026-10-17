import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["ADMIN_TOKEN"] = "test-token"
os.environ["RATE_LIMIT_PER_MINUTE"] = "1000"
os.environ.pop("N8N_WEBHOOK_URL", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN = {"Authorization": "Bearer test-token"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def chat(client):
    def _chat(sender, text):
        r = client.post("/api/chat", json={"sender_id": sender, "message": text})
        assert r.status_code == 200, r.text
        return r.json()
    return _chat
