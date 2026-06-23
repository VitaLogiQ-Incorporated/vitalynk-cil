"""Decision: the decision finite-state machine + decision orchestration.

An explicit FSM selects an action from ``stay | shift | failover | optimize |
escalate`` and *emits* it as a decision (CIL-601/602). The CIL decides; Ericsson
executes — this module never performs the switching/failover itself.
"""
