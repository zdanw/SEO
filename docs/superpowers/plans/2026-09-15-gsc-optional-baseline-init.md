# GSC-Optional Baseline Init Implementation Plan

> **For agentic workers:** Execute task-by-task. Checkboxes track progress.

**Goal:** Make baseline site init work without GSC; treat GSC as a post-connect enhancer.

**Architecture:** Expand `OnboardingService` with soft-skippable base steps; add `enhance_after_gsc` triggered when a GSC property is selected.

**Tech Stack:** FastAPI, Celery, SQLAlchemy, Vue 3

## Global Constraints

- No fake CTR / missed-click numbers
- Soft-skip missing providers; do not fail whole init
- GSC must not be in base `INIT_STEPS`
- Single API prefix `/api`

---

## Task 1: Init step runners + soft-skip pipeline

**Files:**
- Create: `backend/app/domains/sites/init_steps.py`
- Modify: `backend/app/domains/sites/onboarding_service.py`
- Modify: `backend/app/schemas/v2/onboarding.py`

- [ ] Implement `StepSkipped` and step functions
- [ ] Rewrite `INIT_STEPS` without `gsc_sync`
- [ ] Soft-skip continue → `ready` unless `build_opportunities` hard-fails
- [ ] Enrich init-status for frontend

## Task 2: GSC enhance + OAuth routes

**Files:**
- Create: `backend/app/api/gsc_oauth.py`
- Modify: `backend/app/api/__init__.py`
- Modify: `backend/app/tasks/platform_tasks.py`
- Modify: `backend/app/domains/sites/onboarding_service.py` (`enhance_after_gsc`)

- [ ] Mount `/gsc/*` matching frontend
- [ ] On `sites/active` success → `enhance_gsc.delay`
- [ ] Enhance: backfill → ensure queries → observations → rebuild opps

## Task 3: Command Center + frontend copy

**Files:**
- Modify: `backend/app/api/command_center.py`
- Modify: `frontend/src/views/onboarding/Onboarding.vue`
- Modify: `frontend/src/views/command-center/CommandCenter.vue`
- Modify: `frontend/src/api/onboarding.ts`
- Modify: `frontend/src/api/commandCenter.ts`

- [ ] data_health issues: enhance unavailable, not system broken
- [ ] Onboarding: GSC optional, init without requiring connect
- [ ] Map init progress for UI

## Task 4: Verify

- [ ] Call init pipeline without GSC → ready
- [ ] Confirm no `gsc_sync` in progress keys
