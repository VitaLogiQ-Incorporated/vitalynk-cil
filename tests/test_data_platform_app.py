"""EPIC-03 acceptance: the running app produces a labeled, captured dataset.

Drives the real FastAPI app (telemetry + app monitor + data platform) over a
TestClient with tiny intervals, then asserts the end-to-end loop: events land on
the spine, each anchoring event captures a ±window, labels + audit + metrics flow,
and the windows export as self-describing records — all queryable over HTTP.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from cil.api.app import create_app
from cil.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        telemetry_enabled=True,
        telemetry_interval_s=0.02,
        app_monitoring_enabled=True,
        app_monitoring_interval_s=0.02,
        data_platform_enabled=True,
        no_action_sample_interval_s=0.02,
        window_before_s=2,
        window_after_s=2,
        window_min_radius_s=0,
        retention_enabled=False,
        retention_sweep_interval_s=999,
        telemetry_db_path=str(tmp_path / "op.db"),
        training_db_path=str(tmp_path / "training.db"),
    )


def test_data_platform_end_to_end(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as c:
        events: list[dict] = []
        for _ in range(250):  # poll up to ~5s for the background loops to produce
            events = c.get("/events", params={"limit": 100}).json()
            if events:
                break
            time.sleep(0.02)
        assert events, "no continuity events produced by the running app"

        # The NO_ACTION heartbeat guarantees the training-set negative class.
        kinds = {e["kind"] for e in events}
        assert "no_action_sample" in kinds

        # Every anchoring event captured a ±window, and it exports self-describing.
        windows = c.get("/training/windows", params={"limit": 100}).json()
        assert windows
        wid = windows[0]["window_id"]
        export = c.get(f"/training/windows/{wid}")
        assert export.status_code == 200
        assert export.json()["window"]["window_id"] == wid

        # The anchored event carries its window pointer (backfilled).
        na = next(e for e in events if e["kind"] == "no_action_sample")
        assert na["telemetry_window_id"] is not None
        detail = c.get(f"/events/{na['event_id']}").json()
        assert detail["event_id"] == na["event_id"]

        # Audit + Prometheus counters reflect the activity.
        assert c.get("/audit", params={"limit": 10}).json()
        assert "cil_events_total" in c.get("/metrics").text

        # Unknown ids 404 cleanly.
        assert c.get("/events/does-not-exist").status_code == 404
        assert c.get("/training/windows/w_nope").status_code == 404


def test_data_platform_endpoints_empty_when_disabled() -> None:
    settings = Settings(
        telemetry_enabled=False, app_monitoring_enabled=False, data_platform_enabled=False
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/events").json() == []
        assert c.get("/scores").json() == []
        assert c.get("/audit").json() == []
        assert c.get("/training/windows").json() == []
