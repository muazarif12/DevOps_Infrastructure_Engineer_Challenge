"""REST service for reading and maintaining patient records.

Responses return the patient resource directly and lean on conventional HTTP status codes
(201 on create, 404 when missing, 422 for bad input via FastAPI's native validation), rather
than wrapping everything in a success/error envelope.
"""

from __future__ import annotations

import logging
import os
import secrets
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, status
from prometheus_fastapi_instrumentator import Instrumentator

from app.obs import RequestIDMiddleware, configure_logging
from app.schema import NewPatient, PatientChanges, to_national_phone
from app.store import PatientStore, as_public_dict, create_schema

configure_logging()
log = logging.getLogger("api")

_API_KEY = os.environ.get("API_KEY")


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Every /patients* route requires this (see `patients_router` below) — previously anyone
    who could reach the port could dump every patient record with one unauthenticated
    GET /patients. /health, /readyz, and /metrics stay open: they carry no PHI and container
    healthchecks / Prometheus need to reach them without a credential.

    Fails closed if API_KEY isn't configured at all, rather than silently accepting every
    request — docker-compose.yml already makes API_KEY required (`${API_KEY:?...}`), this is
    defense in depth for any other way the app gets started."""
    if not _API_KEY:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, detail="server misconfigured: API_KEY not set"
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, _API_KEY):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing or invalid API key")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    yield


app = FastAPI(title="Patient Intake Records", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)

# /metrics: request count, latency, and in-progress requests by method/path/status. Scraped by
# Prometheus (see infra/observability/prometheus.yml) and alerted on (HighAPIErrorRate).
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


def store_dependency() -> Iterator[PatientStore]:
    store = PatientStore.open()
    try:
        yield store
    finally:
        store.close()


@app.get("/health")
def health() -> dict:
    """Liveness only: is the process up and serving requests at all. Deliberately does not
    touch the database — a slow/unreachable DB should show up as a /readyz failure, not take
    down the liveness probe and cause an unnecessary restart-loop of an otherwise-fine process."""
    return {"status": "up"}


@app.get("/readyz")
def ready(store: PatientStore = Depends(store_dependency)) -> dict:
    """Readiness: can this instance actually serve traffic right now. Runs a real round-trip
    to the database, unlike /health — a dead/unreachable DB fails this, which is what should
    pull the instance out of a load balancer's rotation."""
    try:
        store.ping()
    except Exception as exc:
        log.exception("readiness check failed")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"database unreachable: {exc}"
        ) from exc
    return {"status": "ready"}


# Every route on this router requires require_api_key — declared once here rather than
# repeated per-endpoint, so a new /patients* route can't accidentally ship unauthenticated.
patients_router = APIRouter(dependencies=[Depends(require_api_key)])


@patients_router.get("/patients")
def browse_patients(
    family_name: str | None = Query(default=None),
    birth_date: date | None = Query(default=None),
    phone: str | None = Query(default=None),
    store: PatientStore = Depends(store_dependency),
) -> list[dict]:
    national_phone = None
    if phone:
        try:
            national_phone = to_national_phone(phone)
        except ValueError:
            # An unparseable phone filter simply matches nothing.
            return []
    rows = store.search(
        family_name=family_name,
        birth_date=birth_date,
        national_phone=national_phone,
    )
    return [as_public_dict(row) for row in rows]


@patients_router.get("/patients/{record_id}")
def read_patient(
    record_id: str,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(row)


@patients_router.post("/patients", status_code=status.HTTP_201_CREATED)
def register_patient(
    body: NewPatient,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    return as_public_dict(store.add(body))


@patients_router.patch("/patients/{record_id}")
def amend_patient(
    record_id: str,
    body: PatientChanges,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(store.apply_changes(row, body))


@patients_router.delete("/patients/{record_id}")
def retire_patient(
    record_id: str,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(store.archive(row))


app.include_router(patients_router)
