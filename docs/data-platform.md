# Data Platform (EPIC-03)

The data platform is the part of the CIL that turns live continuity events into a
**high-fidelity, automatically-labeled dataset** — the foundation UC2's ML models
will train on. It is *un-retrofittable*: if capture is wrong, the dataset is
silently ruined and cannot be recreated, so the guarantees below are enforced in
code and covered by tests.

> Scope note: UC1 **captures and labels** only. Curation, training, and any ML/LLM
> are UC2. Nothing here makes a decision.

## The loop

```
ApplicationMonitor (endpoint state-change)  ─┐
NO_ACTION heartbeat (negative class)         ─┤
(future: scoring / decision / recovery)      ─┘
                  │  ContinuityEvent
                  ▼
              EventBus ──persist──► event spine (immutable)
                  │ fan-out
                  ▼
          LabelingPipeline
            ├─ capture ±15-min window  ──► training DB (separate, indefinite)
            ├─ label (7 rules, CCS-001) ──► label store
            ├─ backfill window pointer  ──► event spine
            └─ audit row                ──► audit log
```

A background **RetentionSweeper** purges aged *operational* data (24 months) while
the spine, audit log, and training DB are never pruned.

## Hard guarantees

| Guarantee | Where | How it's enforced |
|---|---|---|
| **Canonical time** — never lexical string compare | `timeutil`, `ts_us` column | every range/order query is on integer epoch-µs; naive datetimes raise |
| **Native resolution, no downsampling** | `WindowCaptureService` | windows copy the source range verbatim; count asserted == source in-range |
| **±15-min windows** (≥±5-min floor) | `window_before_s/after_s`, `window_min_radius_s` | sub-target radius is warned + audited, never silently shrunk |
| **Indefinite training retention** | `SQLiteTrainingStore` | separate DB file, **no delete path** exists |
| **Operational retention = 24 mo** | `RetentionSweeper` | `operational_retention_days=730`; pinned window ranges excluded |
| **Immutable spine + audit** | `EventStore`, `AuditStore` | `INSERT OR IGNORE`; sweeper never points at them |
| **Idempotent capture/label** | keyed on `event_id` / `window_id` | re-delivery duplicates nothing (training has no delete) |
| **Crash-safe windows** | two-phase capture | header at event time; full copy once the post-side elapses (`finalize_due`) |
| **App-level recovery** | labeler render-state caveat | RECOVERY withheld when required depth (render-state) isn't verifiable |

## The 7 labels (rule-based, CIL-303)

`FAILOVER · RECOVERY · ROLLBACK · ESCALATION · SLA_BREACH · OPTIMIZATION · NO_ACTION`

Precedence (highest first): `SLA_BREACH > ESCALATION > ROLLBACK > FAILOVER >
OPTIMIZATION > RECOVERY > NO_ACTION`. `SLA_BREACH` = CCS < 40 sustained 5s
(thresholds from `config/ccs_tiers.yaml`, CCS-001 — never hardcoded). SLA dwell
state is rebuilt from the score timeline on startup (`replay_sla`).

## HTTP surface (read-only)

| Endpoint | Returns |
|---|---|
| `GET /events?limit=` | recent continuity events (spine) |
| `GET /events/{id}` | one event (incl. its window pointer) |
| `GET /scores?limit=` | recent CQS/CCS samples |
| `GET /audit?limit=` | recent audit records |
| `GET /training/windows?limit=` | captured window headers |
| `GET /training/windows/{id}` | one window's self-describing export (header + native rows + label) |

Prometheus: `cil_events_total{kind}`, `cil_labels_total{label}`.

## Export seam (UC2 / cloud archive)

`OperationalExporter` emits events / scores / audit as JSONL; `TrainingExporter`
emits each window as one self-describing JSON record. Pure read-only — the edge
keeps a rolling full-res window; the indefinite archive lives in the cloud (the
edge/cloud split is data-platform/ops work).

## Config knobs (`Settings`)

`data_platform_enabled`, `training_db_path`, `window_before_s` / `window_after_s`
/ `window_min_radius_s`, `retention_enabled`, `operational_retention_days`,
`retention_sweep_interval_s`, `no_action_sample_interval_s`, `ccs_tiers_path`,
`labeling_config_path`.
