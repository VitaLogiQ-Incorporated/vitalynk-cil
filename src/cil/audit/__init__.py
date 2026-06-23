"""Audit: audit/event logging + the automated event labeler.

Immutable event log (CIL-902) plus the rule-based labeler (CIL-303) that tags
each event as it occurs — FAILOVER, RECOVERY, ROLLBACK, ESCALATION, SLA_BREACH,
OPTIMIZATION, NO_ACTION — and links it to its telemetry window. Curation and
model training are out of UC1 scope.
"""
