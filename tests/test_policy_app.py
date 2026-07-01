"""EPIC-05 e2e: the /policy routes expose the CIP library and advise off live scores."""

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
        scoring_enabled=True,
        scoring_interval_s=0.02,
        no_action_sample_interval_s=0.05,
        window_before_s=1,
        window_after_s=1,
        window_min_radius_s=0,
        retention_enabled=False,
        retention_sweep_interval_s=999,
        telemetry_db_path=str(tmp_path / "op.db"),
        training_db_path=str(tmp_path / "training.db"),
    )


def test_policy_endpoints_live(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as c:
        policies = c.get("/policy/policies").json()
        assert len(policies) == 5
        assert policies[0]["id"] == "CIP-ESCALATE"  # highest priority first
        assert {p["action"] for p in policies} == {
            "escalate",
            "failover",
            "shift",
            "optimize",
            "stay",
        }

        # evaluate against live scores — the healthy simulator sits in Protected -> stay
        rec = None
        for _ in range(250):
            r = c.get("/policy/evaluate")
            if r.status_code == 200:
                rec = r.json()
                break
            time.sleep(0.02)
        assert rec is not None, "policy evaluate never saw a score"
        assert rec["recommended_action"] == "stay"
        assert rec["winning_policy"] == "CIP-STAY"
        assert rec["matches"], "expected an explainable match trail"


def test_policy_endpoints_empty_when_disabled() -> None:
    settings = Settings(
        telemetry_enabled=False,
        app_monitoring_enabled=False,
        data_platform_enabled=False,
        policy_enabled=False,
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/policy/policies").json() == []
        assert c.get("/policy/evaluate").status_code == 404
