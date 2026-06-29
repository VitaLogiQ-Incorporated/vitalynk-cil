"""Clinical Continuity Score (CIL-402) — blend, worst-case, tiers, config-driven."""

from __future__ import annotations

from datetime import UTC, datetime

from cil.audit.events import ScoreKind
from cil.scoring.ccs import CCSConfig, CCSEngine, CCSTiers, load_ccs_tiers
from cil.telemetry.probes import EndpointHealth, ProbeDepth

BASE = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def health(
    name: str = "epic-ehr",
    *,
    reachable: bool = True,
    live: bool = True,
    healthy: bool = True,
) -> EndpointHealth:
    return EndpointHealth(
        timestamp=BASE,
        endpoint=name,
        system=name,
        reachable=reachable,
        live=live,
        healthy=healthy,
        depth_achieved=ProbeDepth.APP_RESPONSE if healthy else None,
        required_depth=ProbeDepth.APP_RESPONSE,
    )


def test_all_healthy_good_carrier_is_protected() -> None:
    eng = CCSEngine()
    value = eng.compute([health("a"), health("b"), health("c")], cqs=100.0)
    assert value == 100.0
    assert eng.classify(value) == "Protected"


def test_unreachable_critical_endpoint_craters_via_worst_case() -> None:
    # 2 healthy + 1 fully down: mean=66.7, worst=0 -> clinical ~33 (worst_weight 0.5)
    eng = CCSEngine()
    healths = [health("a"), health("b"), health("c", reachable=False, live=False, healthy=False)]
    value = eng.compute(healths, cqs=100.0)
    # clinical = 0.5*66.67 + 0.5*0 = 33.3 ; ccs = 0.8*33.3 + 0.2*100 = 46.7
    assert 45.0 <= value <= 48.0
    assert eng.classify(value) == "Breach Risk"


def test_frozen_endpoint_is_penalised_between_healthy_and_down() -> None:
    eng = CCSEngine()
    frozen = eng.compute([health("a", live=False, healthy=False)], cqs=100.0)  # frozen=30
    down = eng.compute([health("a", reachable=False, live=False, healthy=False)], cqs=100.0)
    healthy = eng.compute([health("a")], cqs=100.0)
    assert down < frozen < healthy


def test_tier_classification_boundaries() -> None:
    eng = CCSEngine()
    assert eng.classify(95) == "Protected"
    assert eng.classify(80) == "Stable"
    assert eng.classify(65) == "Degraded"
    assert eng.classify(45) == "Breach Risk"
    assert eng.classify(20) == "OUTAGE"
    assert eng.classify(39.5) == "OUTAGE"  # gap between 39 and 40 resolves down


def test_carrier_quality_modulates_score() -> None:
    eng = CCSEngine()
    hi = eng.compute([health("a")], cqs=100.0)
    lo = eng.compute([health("a")], cqs=0.0)
    assert hi > lo  # same clinical, worse carrier -> lower CCS


def test_no_clinical_signal_falls_back_to_carrier() -> None:
    eng = CCSEngine()
    assert eng.compute([], cqs=72.0) == 72.0


def test_config_override_changes_blend() -> None:
    # carrier-only weighting -> CCS == CQS regardless of clinical
    cfg = CCSConfig(clinical_weight=0.0, carrier_weight=1.0)
    eng = CCSEngine(cfg)
    assert eng.compute([health("a", reachable=False, live=False, healthy=False)], cqs=88.0) == 88.0


def test_score_wraps_as_ccs_scoresample_with_tier() -> None:
    out = CCSEngine().score([health("a")], cqs=100.0, ts=BASE)
    assert out.kind is ScoreKind.CCS
    assert out.scope == "site"
    assert out.value == 100.0
    assert out.tier == "Protected"


def test_tiers_load_from_ccs001_config() -> None:
    tiers = load_ccs_tiers("config/ccs_tiers.yaml")
    assert tiers.outage_threshold == 40
    assert tiers.sla_sustain_s == 5
    assert {t.name for t in tiers.tiers} == {
        "Protected",
        "Stable",
        "Degraded",
        "Breach Risk",
        "OUTAGE",
    }


def test_outage_tier_below_threshold() -> None:
    eng = CCSEngine(tiers=CCSTiers())
    # everything down -> 0 -> OUTAGE
    value = eng.compute([health("a", reachable=False, live=False, healthy=False)], cqs=0.0)
    assert value == 0.0
    assert eng.classify(value) == "OUTAGE"
