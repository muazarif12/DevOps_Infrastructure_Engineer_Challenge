"""API tests exercising the full create/read/filter/update/archive flow."""

import pytest
from fastapi.testclient import TestClient

from app.web import app

NEW_PATIENT = dict(
    given_name="Grace",
    family_name="Hopper",
    birth_date="12/09/1906",
    sex="female",
    phone="(212) 736-5000",
    street="1 Navy Yard",
    city="Arlington",
    state="va",
    postal_code="22202",
)


@pytest.fixture()
def client():
    # The `with` block runs the lifespan, which creates the schema.
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "up"}


def test_readyz(client):
    assert client.get("/readyz").json() == {"status": "ready"}


def _patient_records_total(metrics_text: str) -> float:
    for line in metrics_text.splitlines():
        if line.startswith("patient_records_total "):
            return float(line.split()[1])
    raise AssertionError("patient_records_total not found in /metrics output")


def test_metrics_reflects_real_patient_count(client):
    # patient_records_total is computed fresh on each scrape (Gauge.set_function), straight
    # from Postgres/SQLite — not a Pushgateway push, not the worker's (confirmed broken)
    # multiprocess metrics. Archived rows must not count. Other tests in this file share the
    # same DB, so assert on the *delta* rather than an absolute count.
    before = _patient_records_total(client.get("/metrics").text)

    created = client.post("/patients", json=NEW_PATIENT).json()
    assert _patient_records_total(client.get("/metrics").text) == before + 1

    client.delete(f"/patients/{created['record_id']}")
    assert _patient_records_total(client.get("/metrics").text) == before


def test_create_normalizes_and_returns_201(client):
    resp = client.post("/patients", json=NEW_PATIENT)
    assert resp.status_code == 201
    body = resp.json()
    assert body["phone"] == "2127365000"
    assert body["state"] == "VA"
    assert body["sex"] == "Female"
    assert body["record_id"]
    assert body["archived"] is False


def test_create_rejects_bad_phone_with_422(client):
    bad = dict(NEW_PATIENT, phone="12")
    assert client.post("/patients", json=bad).status_code == 422


def test_get_and_404(client):
    created = client.post("/patients", json=NEW_PATIENT).json()
    got = client.get(f"/patients/{created['record_id']}")
    assert got.status_code == 200
    assert got.json()["family_name"] == "Hopper"
    assert client.get("/patients/does-not-exist").status_code == 404


def test_filter_by_phone_and_family_name(client):
    client.post("/patients", json=NEW_PATIENT)
    # Phone filter accepts human formatting and matches the normalized store value.
    by_phone = client.get("/patients", params={"phone": "212-736-5000"}).json()
    assert any(p["family_name"] == "Hopper" for p in by_phone)
    by_name = client.get("/patients", params={"family_name": "Hopper"}).json()
    assert len(by_name) >= 1


def test_patch_only_changes_supplied_fields(client):
    created = client.post("/patients", json=NEW_PATIENT).json()
    rid = created["record_id"]
    patched = client.patch(f"/patients/{rid}", json={"city": "Reston"}).json()
    # The one supplied field changed; everything else is preserved (no null-out).
    assert patched["city"] == "Reston"
    assert patched["given_name"] == "Grace"
    assert patched["phone"] == "2127365000"
    assert patched["postal_code"] == "22202"


def test_archive_removes_from_reads(client):
    created = client.post("/patients", json=NEW_PATIENT).json()
    rid = created["record_id"]
    assert client.delete(f"/patients/{rid}").json()["archived"] is True
    assert client.get(f"/patients/{rid}").status_code == 404
    remaining = client.get("/patients", params={"family_name": "Hopper"}).json()
    assert all(p["record_id"] != rid for p in remaining)
