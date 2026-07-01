"""Policy evaluation service (EPIC-05 integration).

Builds a ``PolicyContext`` from the latest live scores (EPIC-04) and evaluates the CIP
library. Advisory only — it returns a recommendation; it does not decide or execute
(that is the decision FSM in EPIC-06).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cil.audit.events import ScoreKind
from cil.policy.context import PolicyContext

if TYPE_CHECKING:
    from cil.policy.engine import PolicyEngine
    from cil.policy.models import PolicyEvaluation
    from cil.storage.interface import ScoreStore


class PolicyEvaluator:
    """Evaluates the CIP library against the latest CCS/CQS in the score store."""

    def __init__(self, engine: PolicyEngine, score_store: ScoreStore) -> None:
        self._engine = engine
        self._store = score_store

    async def latest_context(self) -> PolicyContext | None:
        """Build a context from the most recent CCS (+ CQS) score, or None if none yet."""
        ccs = await self._store.read_scores(kind=ScoreKind.CCS, limit=1)
        if not ccs:
            return None
        latest = ccs[-1]  # read_scores returns oldest-first; limit=1 -> the newest
        cqs_rows = await self._store.read_scores(kind=ScoreKind.CQS, limit=1)
        return PolicyContext(
            ccs=latest.value,
            ccs_tier=latest.tier or "",
            cqs=cqs_rows[-1].value if cqs_rows else None,
            sla_breaching=latest.tier == "OUTAGE",
        )

    async def evaluate_latest(self) -> PolicyEvaluation | None:
        """Evaluate the CIP library against the latest scores (None if no scores yet)."""
        context = await self.latest_context()
        return None if context is None else self._engine.evaluate(context)
