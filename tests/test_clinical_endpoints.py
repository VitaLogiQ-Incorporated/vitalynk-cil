"""Clinical-endpoint inventory (CCS-APP-001): strict config + config-driven loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from cil.telemetry.probes import ClinicalEndpoint, ProbeDepth, load_clinical_endpoints


def test_endpoint_rejects_unknown_key() -> None:
    # a typo (`requird_depth`) must fail loudly, not silently downgrade the OR endpoint
    with pytest.raises(ValidationError):
        ClinicalEndpoint(
            name="or-systems",
            system="OR",
            target="https://or.local/health",
            requird_depth="render_state",  # type: ignore[call-arg]
        )


def test_load_falls_back_when_file_absent(tmp_path: Path) -> None:
    eps = load_clinical_endpoints(str(tmp_path / "nope.yaml"))
    assert {e.name for e in eps} == {"epic-ehr", "cerner", "pacs", "ris", "or-systems"}


def test_load_from_config_file(tmp_path: Path) -> None:
    p = tmp_path / "eps.yaml"
    p.write_text(
        "endpoints:\n"
        "  - {name: epic, system: Epic, target: 'https://x', required_depth: render_state}\n"
    )
    eps = load_clinical_endpoints(str(p))
    assert len(eps) == 1
    assert eps[0].required_depth is ProbeDepth.RENDER_STATE


def test_repo_config_matches_expected_fleet() -> None:
    # the shipped config loads and preserves the render-state OR endpoint
    eps = load_clinical_endpoints("config/clinical_endpoints.yaml")
    by_name = {e.name: e for e in eps}
    assert by_name["or-systems"].required_depth is ProbeDepth.RENDER_STATE
