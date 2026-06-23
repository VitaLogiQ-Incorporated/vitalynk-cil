"""Telemetry demo: drive the simulator through scenarios into a SQLite store.

Run with: `uv run python scripts/demo_telemetry.py` (or `make demo`).
Shows the Sprint-1 loop end-to-end — simulator -> normalize -> store -> read back —
with no server or hardware. Uses an in-memory SQLite DB so it leaves nothing behind.
"""

from __future__ import annotations

import asyncio

from cil.storage.sqlite import SQLiteTelemetryStore
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.probes import DEFAULT_CLINICAL_ENDPOINTS
from cil.telemetry.scenarios import Scenario
from cil.telemetry.schema import TelemetrySample
from cil.telemetry.simprobe import EndpointCondition, SimulatedClinicalProbe
from cil.telemetry.simulator import SimulatorAdapter


def fmt(s: TelemetrySample) -> str:
    n = s.network
    lat = f"{n.latency_ms:5.0f}ms" if n.latency_ms is not None else "    —  "
    sinr = f"{s.radio.sinr:5.1f}dB" if s.radio.sinr is not None else "   —  "
    loss = f"{n.packet_loss_pct:5.1f}%" if n.packet_loss_pct is not None else "   — "
    return (
        f"  {s.timestamp.strftime('%H:%M:%S'):8}"
        f" path={s.path_id:8} carrier={s.carrier:8}"
        f" reach={n.reachable!s:5} lat={lat} loss={loss} sinr={sinr}"
    )


async def main() -> None:
    store = SQLiteTelemetryStore(":memory:")
    await store.setup()
    adapter = SimulatorAdapter(seed=7)
    collector = TelemetryCollector(adapter, store, interval_s=0.0)

    print("== HEALTHY ==")
    for _ in range(3):
        print(fmt(await collector.run_once()))

    print("\n== BLACKOUT (injected) ==")
    adapter.set_scenario(Scenario.BLACKOUT, duration=4)
    for _ in range(3):
        print(fmt(await collector.run_once()))

    print("\n== DUAL-MODEM FAILOVER (degrade -> switch -> recover) ==")
    adapter.set_scenario(Scenario.DUAL_MODEM_FAILOVER, duration=8)
    for _ in range(9):
        print(fmt(await collector.run_once()))

    total = await store.count()
    recent = await store.read_samples(limit=3)
    print(f"\nPersisted samples: {total}")
    print(f"Last 3 read back from SQLite: {[s.path_id for s in recent]}")
    await store.close()

    await clinical_demo()


async def clinical_demo() -> None:
    """Application monitoring: the frozen-OR-screen differentiator."""
    probe = SimulatedClinicalProbe(seed=1)
    monitor = ApplicationMonitor(probe, DEFAULT_CLINICAL_ENDPOINTS, interval_s=0.0)

    def show(tag: str) -> None:
        print(f"\n== CLINICAL: {tag} ==")
        for h in monitor.latest.values():
            note = f"  ({h.detail})" if h.detail else ""
            print(
                f"  {h.endpoint:12} reachable={h.reachable!s:5} live={h.live!s:5} "
                f"healthy={h.healthy!s:5}{note}"
            )

    await monitor.run_once()
    show("all healthy")

    # The frozen screen: reachable at the IP layer, but the app is not responding.
    probe.set_condition("or-systems", EndpointCondition.FROZEN)
    await monitor.run_once()
    show("OR endpoint FROZEN (reachable but not live)")


if __name__ == "__main__":
    asyncio.run(main())
