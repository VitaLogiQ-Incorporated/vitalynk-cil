"""The synthetic source exercises every label path (CIL-303)."""

from __future__ import annotations

from cil.audit.events import EventLabel
from cil.audit.labeler import EventLabeler, load_labeling_config
from cil.audit.synthetic import SyntheticEventSource


def test_synthetic_stream_covers_all_seven_labels() -> None:
    labeler = EventLabeler(load_labeling_config())
    source = SyntheticEventSource()

    stream = source.events() + source.sla_breach_scores()
    labels = {labeler.label(e).label for e in stream}

    assert labels == set(EventLabel)  # all 7 labels reachable from synthetic data


def test_synthetic_is_deterministic() -> None:
    a = SyntheticEventSource().events()
    b = SyntheticEventSource().events()
    assert [e.event_id for e in a] == [e.event_id for e in b]
