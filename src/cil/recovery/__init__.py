"""Recovery: post-action recovery validation (CIL-901).

Validation must reach *application-level liveness*, not just "link up" — a path
can be link-healthy while the clinical endpoint is frozen. "Recovered" means the
clinical app is genuinely reachable again.
"""
