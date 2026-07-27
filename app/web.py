"""REST service for reading and maintaining patient records.

Responses return the patient resource directly and lean on conventional HTTP status codes
(201 on create, 404 when missing, 422 for bad input via FastAPI's native validation), rather
than wrapping everything in a success/error envelope.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Query, status
from prometheus_client import Gauge
from prometheus_fastapi_instrumentator import Instrumentator

from app.obs import RequestIDMiddleware, configure_logging
from app.schema import NewPatient, PatientChanges, to_national_phone
from app.store import PatientStore, as_public_dict, create_schema

configure_logging()
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    create_schema()
    yield


app = FastAPI(title="Patient Intake Records", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIDMiddleware)

# /metrics: request count, latency, and in-progress requests by method/path/status. Scraped by
# Prometheus (see infra/observability/prometheus.yml) and alerted on (HighAPIErrorRate).
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# The one genuinely cumulative business metric in this project: how many patients are on
# file, straight from Postgres. Doesn't need a Pushgateway or the worker's (confirmed broken)
# multiprocess metrics — set_function() re-runs the query at scrape time, in-process, since
# this endpoint is already the one place in the app confirmed to produce real metric content.
_patients_total_gauge = Gauge(
    "patient_records_total", "Non-archived patient records currently in the database"
)


def _count_patients() -> float:
    with PatientStore.open() as store:
        return float(store.count())


_patients_total_gauge.set_function(_count_patients)


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


@app.get("/patients")
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


@app.get("/patients/{record_id}")
def read_patient(
    record_id: str,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(row)


@app.post("/patients", status_code=status.HTTP_201_CREATED)
def register_patient(
    body: NewPatient,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    return as_public_dict(store.add(body))


@app.patch("/patients/{record_id}")
def amend_patient(
    record_id: str,
    body: PatientChanges,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(store.apply_changes(row, body))


@app.delete("/patients/{record_id}")
def retire_patient(
    record_id: str,
    store: PatientStore = Depends(store_dependency),
) -> dict:
    row = store.get(record_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No patient with that id")
    return as_public_dict(store.archive(row))


