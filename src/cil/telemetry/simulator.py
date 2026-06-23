"""The telemetry simulator (CIL-204) — a mock ``TelemetryAdapter``.

Synthesises realistic telemetry and injects failure scenarios so the entire
brain (scoring -> policy -> decision -> recovery) can be built and tested without
hardware. It is also the permanent resilience-test harness.

Deterministic by design: the same seed + the same sequence of ``sample()`` calls
produces byte-identical output (timestamps are derived from a tick counter, not
the wall clock), so scenarios are exactly repeatable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from random import Random

from cil.logging import get_logger
from cil.telemetry.normalize import normalize
from cil.telemetry.scenarios import Scenario, shape_metrics
from cil.telemetry.schema import TelemetrySample

# Deterministic clock origin for generated timestamps.
_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class PathProfile:
    """A simulated WAN path (modem + carrier + SIM profile)."""

    path_id: str
    carrier: str
    profile: str = "default"


_DEFAULT_PATHS: tuple[PathProfile, ...] = (
    PathProfile("modem-a", "Verizon", "primary"),
    PathProfile("modem-b", "AT&T", "secondary"),
)


class SimulatorAdapter:
    """A deterministic mock telemetry source implementing ``TelemetryAdapter``."""

    def __init__(
        self,
        *,
        seed: int = 0,
        paths: Sequence[PathProfile] | None = None,
        sample_interval_s: float = 1.0,
        scenario: Scenario = Scenario.HEALTHY,
        scenario_duration: int = 30,
    ) -> None:
        self._rng = Random(seed)
        self._paths: tuple[PathProfile, ...] = tuple(paths) if paths else _DEFAULT_PATHS
        self._interval = sample_interval_s
        self._scenario = scenario
        self._duration = max(1, scenario_duration)
        self._tick = 0
        self._scenario_tick = 0
        self._log = get_logger("cil.telemetry.simulator")

    @property
    def scenario(self) -> Scenario:
        return self._scenario

    def set_scenario(self, scenario: Scenario, *, duration: int = 30) -> None:
        """Inject a scenario. It ramps over ``duration`` samples, then holds at
        peak until reset (call with ``Scenario.HEALTHY`` to recover)."""
        self._scenario = scenario
        self._duration = max(1, duration)
        self._scenario_tick = 0
        self._log.info("simulator.scenario_set", scenario=scenario.value, duration=self._duration)

    async def list_paths(self) -> list[str]:
        return [p.path_id for p in self._paths]

    async def sample(self) -> TelemetrySample:
        progress = min(self._scenario_tick / self._duration, 1.0)
        metrics = shape_metrics(self._scenario, progress, self._rng)
        path = self._active_path(progress)
        timestamp = _EPOCH + timedelta(seconds=self._tick * self._interval)

        raw: dict[str, object] = {
            "timestamp": timestamp.isoformat(),
            "path_id": path.path_id,
            "carrier": path.carrier,
            "profile": path.profile,
            "uptime_s": float(self._tick) * self._interval,
            **metrics,
        }
        sample = normalize(raw, logger=self._log)

        self._tick += 1
        self._scenario_tick += 1
        return sample

    def _active_path(self, progress: float) -> PathProfile:
        """For dual-modem failover, switch to the secondary path at the midpoint."""
        if (
            self._scenario == Scenario.DUAL_MODEM_FAILOVER
            and progress >= 0.5
            and len(self._paths) > 1
        ):
            return self._paths[1]
        return self._paths[0]
