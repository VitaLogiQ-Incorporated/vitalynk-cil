"""Telemetry: adapters + normalization into the internal schema.

Sprint-1 home of the core contracts (CIL-201): the ``TelemetrySample`` Pydantic
model, the ``TelemetryAdapter`` protocol, the normalization layer, the WAN and
application monitors (CIL-202/203), and the mock simulator (CIL-204). The live
Ericsson binding (ops / EPIC-07) implements the same protocol and drops in later.
"""

from cil.telemetry.adapter import TelemetryAdapter
from cil.telemetry.app_monitor import ApplicationMonitor
from cil.telemetry.ericsson import EricssonNetCloudAdapter
from cil.telemetry.monitor import TelemetryCollector
from cil.telemetry.normalize import normalize
from cil.telemetry.probes import (
    DEFAULT_CLINICAL_ENDPOINTS,
    ApplicationProbe,
    ClinicalEndpoint,
    EndpointHealth,
    ProbeDepth,
)
from cil.telemetry.scenarios import Scenario
from cil.telemetry.schema import (
    DeviceMetrics,
    NetworkMetrics,
    RadioMetrics,
    TelemetrySample,
)
from cil.telemetry.simprobe import EndpointCondition, SimulatedClinicalProbe
from cil.telemetry.simulator import PathProfile, SimulatorAdapter

__all__ = [
    "DEFAULT_CLINICAL_ENDPOINTS",
    "ApplicationMonitor",
    "ApplicationProbe",
    "ClinicalEndpoint",
    "DeviceMetrics",
    "EndpointCondition",
    "EndpointHealth",
    "EricssonNetCloudAdapter",
    "NetworkMetrics",
    "PathProfile",
    "ProbeDepth",
    "RadioMetrics",
    "Scenario",
    "SimulatedClinicalProbe",
    "SimulatorAdapter",
    "TelemetryAdapter",
    "TelemetryCollector",
    "TelemetrySample",
    "normalize",
]
