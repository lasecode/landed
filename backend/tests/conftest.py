import os

os.environ.setdefault("APP_ENV", "test")
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite:///./test.db")
os.environ.pop("REDIS_URL", None)  # jobs run inline in tests
os.environ["ALLOW_MOCK_PROVIDER"] = "true"
os.environ["WHATSAPP_DRY_RUN"] = "true"

import json  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as dbmod  # noqa: E402
from app import models  # noqa: E402,F401
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    dbmod.Base.metadata.drop_all(dbmod.engine)
    dbmod.Base.metadata.create_all(dbmod.engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def signup(client, business="Adaeze Foods", phone="08030000001", name="Adaeze Okafor"):
    r = client.post("/api/auth/signup", json={"business_name": business, "owner_name": name, "phone": phone,
                                              "password": "password123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def owner(client):
    return signup(client)


def make_invoice(client, h, amount=45000, name="Tunde Bello", phone="08031112222", desc="3 bags of rice"):
    r = client.post("/api/invoices", headers=h, json={"customer_name": name, "customer_phone": phone,
                                                      "amount_naira": amount, "description": desc})
    assert r.status_code == 200, r.text
    return r.json()


def pay(client, h, account_number, amount, payer="TUNDE BELLO"):
    r = client.post("/api/dev/simulate-payment", headers=h,
                    json={"account_number": account_number, "amount_naira": amount, "payer_name": payer})
    assert r.status_code == 200, r.text
    assert r.json()["webhook_status"] == 200
    return r.json()["reference"]


def get_invoice(client, h, iid):
    r = client.get(f"/api/invoices/{iid}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def notifications():
    with dbmod.session_scope() as s:
        return [(n.template, n.recipient_phone, n.params) for n in s.query(models.Notification).order_by(models.Notification.id)]


__all__ = ["signup", "make_invoice", "pay", "get_invoice", "notifications", "json"]
