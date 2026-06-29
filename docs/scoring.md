# Scoring — CQS & CCS (EPIC-04)

The scoring engine is the CIL's **judgment layer**: it turns the raw telemetry and
clinical-liveness data (Epics 2 & 3) into two explainable **0–100 health scores**.
Deterministic and config-driven — **no ML** (that is UC2).

## The two scores

| Score | What it answers | Built from | Authoritative? |
|---|---|---|---|
| **CQS** — Carrier Quality Score (CIL-401) | "How good is the network/carrier?" | radio + network telemetry | input only |
| **CCS** — Clinical Continuity Score (CIL-402) | "Can the hospital still reach its clinical systems?" | clinical liveness + CQS | **yes — the metric the brain decides on** |

### CQS (carrier quality)
Each telemetry metric is linearly mapped to a 0–100 sub-score between a configured
`good` value (→100) and `bad` value (→0), clamped, then combined as a **weighted
mean over the metrics actually present**. **Reachability gates everything** — an
unreachable path scores `unreachable_score` (0) regardless of radio stats.
Config: [`config/cqs.yaml`](../config/cqs.yaml).

### CCS (clinical continuity) — authoritative
Each clinical endpoint contributes a per-state score (`healthy` 100 · `degraded` 60
· `frozen` 30 · `unreachable` 0). The clinical component is a **worst-case blend**
(`worst_weight`) of the mean and the *worst* endpoint, so one dead critical system
(e.g. the EHR) can't be masked by a healthy average. CCS then blends clinical
(dominant) with carrier quality:

```
clinical = (1-worst_weight)·mean(endpoint_scores) + worst_weight·min(endpoint_scores)
CCS      = (clinical_weight·clinical + carrier_weight·CQS) / (clinical_weight+carrier_weight)
```

Config: [`config/ccs.yaml`](../config/ccs.yaml) (blend) + [`config/ccs_tiers.yaml`](../config/ccs_tiers.yaml) (CCS-001 tiers).

## CCS-001 tier matrix (Tier 1, approved — config-driven, never hardcoded)

| Tier | CCS range | Meaning |
|---|---|---|
| Protected | 90–100 | Healthy |
| Stable | 75–89 | Normal |
| Degraded | 60–74 | Watch |
| Breach Risk | 40–59 | At risk |
| **OUTAGE** | **< 40 sustained 5s** | **SLA_BREACH** |

## The scoring loop

Each tick (`scoring_interval_s`, default 1s) the `ScoringService`:

1. reads the latest telemetry + clinical health,
2. computes **CQS** (per path) and **CCS** (site),
3. **persists both** as `ScoreSample` rows → visible at **`/scores`**,
4. emits a persist-only **`SCORE_SAMPLE`** event so the CIL-303 labeler sees the CCS
   timeline, and
5. runs an **SLA edge-detector**: when CCS stays below the CCS-001 outage threshold
   for the sustained window, it emits **one** anchoring `SLA_STATE` event — which the
   pipeline captures a ±window for and the labeler tags **`SLA_BREACH`**.

```
telemetry + clinical ─► CQS, CCS ─► /scores
                           │
                           ├─ SCORE_SAMPLE event ─► labeler SLA timeline
                           └─ (CCS < 40 sustained 5s) ─► SLA_STATE event ─► window + SLA_BREACH label
```

Observed end-to-end: healthy → `Protected (CCS 100)`; clinical outage →
`OUTAGE (CCS 20)`; after 5 s sustained → exactly one `SLA_BREACH` (labeled + windowed).

## HTTP / metrics

- `GET /scores` — recent CQS/CCS samples (each CCS carries its tier).
- The SLA breach also surfaces at `/events` (kind `sla_state`), `/audit`
  (`SLA_BREACH`), and `/training/windows`.

## Config knobs (`Settings`)

`scoring_enabled`, `scoring_interval_s`, `cqs_config_path`, `ccs_config_path`,
`ccs_tiers_path` (CCS-001 — shared with the labeler so breach detection is
consistent).

## Scope

In: deterministic CQS + CCS, tiers, SLA edge-detection, publishing. **Out:** ML
(UC2); acting on the scores — that's the Policy (Epic 5) and Decision (Epic 6)
engines, which consume what this produces.
