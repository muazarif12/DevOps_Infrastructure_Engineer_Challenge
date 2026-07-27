"""app/metrics.py: must never raise or attempt network I/O when unconfigured (local dev,
tests, or any deployment that hasn't set PUSHGATEWAY_URL), and must push under a single fixed
job name (not one group per call) when it is configured."""

from unittest.mock import patch

from app import metrics


def test_no_op_when_pushgateway_url_unset(monkeypatch):
    monkeypatch.setattr(metrics, "_PUSHGATEWAY_URL", "")
    with patch("app.metrics.pushadd_to_gateway") as mock_push:
        metrics.call_started()
        metrics.call_completed(success=True)
        metrics.call_completed(success=False)
    mock_push.assert_not_called()


def test_pushes_under_fixed_job_name_when_configured(monkeypatch):
    monkeypatch.setattr(metrics, "_PUSHGATEWAY_URL", "http://pushgateway.railway.internal:9091")
    with patch("app.metrics.pushadd_to_gateway") as mock_push:
        metrics.call_completed(success=True)

    assert mock_push.call_count == 1
    _args, kwargs = mock_push.call_args
    # Same fixed job name every call — the whole point is avoiding one group per call
    # (Pushgateway's own anti-pattern warning), unlike a per-room/per-call grouping key.
    assert kwargs["job"] == "patient_intake_worker"


def test_a_pushgateway_failure_never_raises(monkeypatch):
    monkeypatch.setattr(metrics, "_PUSHGATEWAY_URL", "http://unreachable.invalid:9091")
    with patch("app.metrics.pushadd_to_gateway", side_effect=OSError("connection refused")):
        metrics.call_started()  # must not raise — call handling can't depend on this
