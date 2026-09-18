"""Smoke: baseline init has no gsc_sync; imports ok."""
from app.domains.sites.init_steps import INIT_STEPS, STEP_RUNNERS
from app.api import api

assert "gsc_sync" not in INIT_STEPS
assert list(INIT_STEPS) == list(STEP_RUNNERS.keys())

paths = {getattr(r, "path", None) for r in api.routes}
assert "/gsc/status" in paths
assert "/gsc/sites/active" in paths
assert "/sites/{site_id}/init" in paths

print("INIT_STEPS=", INIT_STEPS)
print("gsc oauth routes ok")
print("PASS")
