# VitaLynk — Carrier Intelligence Layer (CIL), UC1

> A VitaLogiQ product.

The **Carrier Intelligence Layer** is the deterministic "brain" of **VitaLynk
Adaptive Fabric™** — an on-device clinical network-continuity system. It watches
the network, scores health (CQS/CCS), evaluates policy, and **decides** an action
(`stay · shift · failover · optimize · escalate`). The Ericsson E400 **executes**
the decision; the CIL never moves packets or switches SIMs itself.

> **The one rule:** the CIL decides; Ericsson executes. No ML and no LLM sit on
> the UC1 decision path. See [CLAUDE.md](../.claude/CLAUDE.md) for the full
> grounding (kept in the parent workspace dir, alongside `supporting material/`).

## Architecture

A **modular monolith** — one deployable container, clean internal boundaries:

| Module | Responsibility |
|---|---|
| `cil.telemetry` | adapters (Ericsson + simulator) + normalization into the internal schema |
| `cil.storage`   | storage interface + SQLite impl (operational + training stores) |
| `cil.scoring`   | CQS (carrier quality) and CCS (clinical continuity) engines |
| `cil.policy`    | declarative YAML policy framework + CIP policy library |
| `cil.decision`  | decision FSM + orchestration (emits actions, does **not** execute) |
| `cil.recovery`  | recovery validation (application-level liveness) |
| `cil.audit`     | audit/event logging + automated event labeling |
| `cil.api`       | FastAPI app, `/health`, `/metrics`, config wiring |

**Stack:** Python 3.11+ · FastAPI · Pydantic v2 · SQLite (behind an interface) ·
structlog (JSON) · Prometheus.

## Quickstart

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync                 # create the venv + install deps (incl. dev)
uv run cil              # start the service on :8000
# or: uv run python -m cil
```

When it starts, two loops run: the **WAN telemetry** ingest loop (samples the
simulator → normalize → store) and the **clinical application** monitor (probes
endpoint liveness). Both persist to SQLite (`data/telemetry.db`).

Verify (live):

```bash
curl localhost:8000/health             # {"status":"ok",...}
curl localhost:8000/telemetry/count    # {"count": N}  (grows over time)
curl localhost:8000/telemetry/latest   # the most recent normalized sample
curl localhost:8000/clinical/endpoints # the monitored clinical systems
curl localhost:8000/clinical/health    # per-endpoint reachable / live / healthy
curl localhost:8000/metrics            # Prometheus (cil_telemetry_* + cil_app_*)
open  localhost:8000/docs              # OpenAPI
```

## Verifying the build

Everything runs through `uv` (or the `make` shortcuts):

| Command | What it checks |
|---|---|
| `make check` | the full gate: lint + format + type-check + tests |
| `make lint` | `ruff check` + `ruff format --check` |
| `make typecheck` | `mypy` (strict) |
| `make test` | `pytest` |
| `make demo` | runs the simulator → store loop end-to-end (no server) |
| `make run` | starts the service on :8000 |
| `make docker-build` / `make docker-run` | build / run the container |

Raw equivalents (no `make`): `uv run ruff check .`, `uv run mypy`, `uv run pytest`,
`uv run python scripts/demo_telemetry.py`.

## Configuration

Settings load from `config/default.yaml`, overridable by `CIL_*` environment
variables (see [`.env.example`](.env.example)). Precedence:
`env > .env > YAML file`. Point at a different file with `CIL_CONFIG_FILE`.
Scoring/policy thresholds are **not** here — they live in their own runtime
config artifacts, never hardcoded.

## Container

Build and run locally:

```bash
docker build -t vitalynk-cil:dev .
docker run --rm -p 8000:8000 vitalynk-cil:dev
```

### Published images (CI/CD)

CI builds the image on every change. On pushes to `main` and on `vX.Y.Z` release
tags it also **publishes** the image to **GHCR** (GitHub Container Registry), so
the exact tested build is available to pull — no rebuild needed:

```bash
docker pull ghcr.io/<owner>/vitalynk-cil:latest      # newest on main
docker pull ghcr.io/<owner>/vitalynk-cil:sha-<commit># exact, traceable build
docker pull ghcr.io/<owner>/vitalynk-cil:1.2.3       # a release tag
docker run --rm -p 8000:8000 ghcr.io/<owner>/vitalynk-cil:latest
```

(Replace `<owner>` with the GitHub org/user once the repo is pushed.) Publishing
uses the built-in `GITHUB_TOKEN` — no secrets to configure. The pipeline produces
the artifact; deploying it onto the Ericsson E400 / staging is ops-owned.

## Status

**Sprint 1 — Foundation & Telemetry.** Done:

- **CIL-101** — repo skeleton: package layout, config, JSON logging, `/health` +
  `/metrics`, tests, Docker, CI/CD (GHCR publish).
- **CIL-201** — telemetry schema (`TelemetrySample`) + `TelemetryAdapter` interface
  + normalization (missing-field detection) + the Ericsson adapter (fetch →
  normalize) with a `NetCloudClient` transport seam. Only the NetCloud HTTP client
  is left to ops/EPIC-07.
- **CIL-204** — telemetry simulator with injectable failure scenarios (latency
  spike, loss ramp, jitter, registration drop, blackout, dual-modem failover).
- **CIL-202** — WAN-monitoring ingest loop wired into the app
  (simulator → normalize → store), with `/telemetry/*` endpoints + Prometheus metrics.
- **CIL-203** — Application monitoring: clinical endpoint liveness with the
  reachable-vs-**live** distinction (the frozen-OR-screen differentiator),
  injectable probe conditions, state-change events, `/clinical/*` endpoints +
  `cil_app_*` metrics. Probe depth is modeled (link/IP/app-response/render-state);
  render-state is flagged as **pending clinical input** (open question).
**Sprint 2 — Data Platform (EPIC-03).** Done — see [docs/data-platform.md](docs/data-platform.md):

- **CIL-301** — operational store + **retention sweeper** (24-month purge of
  telemetry/app-health/scores; immutable event spine + audit never pruned; pinned
  training windows protected). Canonical integer `ts_us` time key throughout.
- **CIL-302** — **±15-min native-resolution window capture** into a *separate,
  delete-free* training DB: two-phase + crash-safe, idempotent, byte-identical to
  source (no downsampling), short windows flagged not silently completed.
- **CIL-303** — **automated event labeling** (7 rules, CCS-001-driven, no ML) via
  an event bus + labeling pipeline; the ApplicationMonitor publishes endpoint
  state-changes and a NO_ACTION heartbeat supplies the negative class. Read-only
  `/events`, `/scores`, `/audit`, `/training/windows` endpoints + JSONL export seam
  + `cil_events_total` / `cil_labels_total` metrics.

Next: scoring — **CQS/CCS** (Sprint 3), which consumes WAN telemetry + clinical
liveness and becomes the data platform's score producer. The live Ericsson
telemetry binding (EPIC-07) plugs into the existing `TelemetryAdapter` seam; the
real clinical probe into the `ApplicationProbe` seam.
