"""Call-level operational metrics, pushed to Pushgateway.

Why this exists: the LiveKit SDK's own worker metrics (``lk_agents_worker_load``,
``lk_agents_active_job_count``, ...) are confirmed, by direct verification against the live
deployment, to never populate here — a cross-process multiprocess-aggregation issue in the
SDK, not something this module works around. Separately, call-handling code
(``app/worker.py``'s ``handle_call``, ``app/flow.py``'s ``save_record``) runs inside a fresh
per-call job subprocess from LiveKit's process pool, so there is no single long-lived process
to scrape a plain in-memory ``Counter`` from either — the same problem the SDK's broken
mechanism was meant to solve.

Pushing straight to Pushgateway sidesteps both problems, but pushing one metric *group* per
call (e.g. keyed by room name) is a well-known Prometheus anti-pattern: Pushgateway keeps every
pushed group forever unless explicitly deleted, so a unique key per call means its own
``/metrics`` grows without bound. (Prometheus's own docs warn against exactly this:
https://prometheus.io/docs/practices/pushing/#should-i-be-using-the-pushgateway .)

Instead, every push here uses ONE FIXED job name (``patient_intake_worker``) with no per-call
label — the same safe pattern ``scripts/backup.sh`` already uses for its own heartbeat — via
``pushadd_to_gateway`` (HTTP POST: replaces only the metric *names* in this push, leaving
other previously-pushed names alone) rather than ``push_to_gateway`` (HTTP PUT: would wipe
every other gauge under this job on each call). That trades true cross-call counting for a
bounded "when did we last see activity, and how did it go" signal. The genuinely cumulative
side of the picture — total patients registered — comes from Postgres instead
(``PatientStore.count()`` / the ``patient_records_total`` gauge in ``app/web.py``), which needs
no Pushgateway at all.
"""

from __future__ import annotations

import logging
import os
import time

from prometheus_client import CollectorRegistry, Gauge, pushadd_to_gateway

log = logging.getLogger("intake-metrics")

_PUSHGATEWAY_URL = os.environ.get("PUSHGATEWAY_URL", "")
_JOB_NAME = "patient_intake_worker"


def _push(**gauge_values: float) -> None:
    """Best-effort — a Pushgateway hiccup (or it simply not being configured, e.g. in local
    dev) must never affect call handling. Mirrors the same "warn, don't fail" posture as the
    backup heartbeat in scripts/backup.sh."""
    if not _PUSHGATEWAY_URL:
        return
    registry = CollectorRegistry()
    for name, value in gauge_values.items():
        Gauge(name, name.replace("_", " "), registry=registry).set(value)
    try:
        pushadd_to_gateway(_PUSHGATEWAY_URL, job=_JOB_NAME, registry=registry)
    except Exception:
        log.warning("failed to push call metrics to pushgateway", exc_info=True)


def call_started() -> None:
    """Call once when a call is dispatched to this worker (app/worker.py's handle_call)."""
    _push(call_last_started_timestamp_seconds=time.time())


def call_completed(*, success: bool) -> None:
    """Call once the intake is saved — or fails to save (app/flow.py's save_record)."""
    _push(
        call_last_completed_timestamp_seconds=time.time(),
        call_last_outcome=1.0 if success else 0.0,
    )
