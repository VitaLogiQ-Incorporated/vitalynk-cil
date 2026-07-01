"""Policy evaluation service (EPIC-05 integration).

Builds a ``PolicyContext`` from the latest live scores (EPIC-04) and evaluates the CIP
library. Advisory only — it returns a recommendation; it does not decide or execute
(that is the decision FSM in EPIC-06).

The SLA-breach signal is **dwell-gated** (CCS < ``outage_threshold`` sustained for
``sla_sustain_s``, per CCS-001) so it matches the scoring service / labeler and a single
transient sub-40 sample never looks like a breach.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cil.audit.events import ScoreKind
from cil.policy.context import PolicyContext
from cil.timeutil import to_us

if TYPE_CHECKING:
    from cil.audit.events import ScoreSample
    from cil.policy.engine import PolicyEngine
    from cil.policy.models import PolicyEvaluation
    from cil.storage.interface import ScoreStore

# How many recent CCS samples to inspect for the dwell (plenty for any real cadence).
_DWELL_WINDOW = 128


class PolicyEvaluator:
    """Evaluates the CIP library against the latest CCS/CQS in the score store."""

    def __init__(
        self,
        engine: PolicyEngine,
        score_store: ScoreStore,
        *,
        outage_threshold: float = 40.0,
        sla_sustain_s: float = 5.0,
    ) -> None:
        self._engine = engine
        self._store = score_store
        self._outage_threshold = outage_threshold
        self._sla_sustain_s = sla_sustain_s

    def _sla_dwell(self, ccs_samples: list[ScoreSample]) -> tuple[float, bool]:
        """Return (sustained_s, breaching) for the contiguous below-threshold run that
        ends at the newest sample. A single dip -> span 0 -> not breaching."""
        newest_us: int | None = None
        oldest_us: int | None = None
        for s in reversed(ccs_samples):  # newest-first
            if s.value < self._outage_threshold:
                if newest_us is None:
                    newest_us = to_us(s.timestamp)
                oldest_us = to_us(s.timestamp)
            else:
                break  # run broken -> the newest sample isn't in a below-threshold run
        if newest_us is None or oldest_us is None:
            return 0.0, False
        span_s = (newest_us - oldest_us) / 1_000_000
        return span_s, span_s >= self._sla_sustain_s

    async def latest_context(self) -> PolicyContext | None:
        """Build a context from the most recent CCS (+ CQS) score, or None if none yet."""
        ccs = await self._store.read_scores(kind=ScoreKind.CCS, limit=_DWELL_WINDOW)
        if not ccs:
            return None
        latest = ccs[-1]  # read_scores returns oldest-first; last -> the newest
        cqs_rows = await self._store.read_scores(kind=ScoreKind.CQS, limit=1)
        sustained_s, breaching = self._sla_dwell(ccs)
        return PolicyContext(
            ccs=latest.value,
            ccs_tier=latest.tier or "",
            cqs=cqs_rows[-1].value if cqs_rows else None,
            sla_breaching=breaching,
            sustained_s=sustained_s,
        )

    async def evaluate_latest(self) -> PolicyEvaluation | None:
        """Evaluate the CIP library against the latest scores (None if no scores yet)."""
        context = await self.latest_context()
        return None if context is None else self._engine.evaluate(context)
