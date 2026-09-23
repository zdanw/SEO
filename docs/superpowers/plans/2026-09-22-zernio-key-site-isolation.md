# Zernio Key 按站点隔离 Implementation Plan

> **For agentic workers:** Execute task-by-task with TDD. Skip git commits unless the user asks.

**Goal:** Scope Zernio API keys to `site_id` so list/create/update/delete/sync only see the current site; migrate existing keys to the oldest site.

**Architecture:** Add `site_id` on `ZernioApiKey`; thread `site_id` through `zernio_keys` helpers and Reddit API; cascade delete on site removal.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Vue, pytest

## Global Constraints

- Existing keys → oldest `client_sites` row (min `created_at`, then min `id`)
- Unique `(site_id, api_key)` not global `api_key`
- No cross-site key share/copy UI

---

### Task 1: Service helpers scoped by site_id

**Files:**
- Modify: `backend/app/services/zernio_keys.py`
- Modify: `backend/tests/test_zernio_keys.py`
- Modify: `backend/app/models/zernio_key.py`

- [ ] Write failing tests: `list_enabled_keys` / `list_all_keys` / `is_zernio_ready` filter by site; key on site 2 invisible to site 1
- [ ] Add `site_id` to model; update helpers to require `site_id`
- [ ] Update existing tests to pass `site_id=1` on keys and helper calls
- [ ] Verify pytest passes

### Task 2: OAuth sync + credential resolve

**Files:**
- Modify: `backend/app/services/reddit_oauth.py`
- Modify: `backend/app/services/reddit_client.py`
- Modify: `backend/tests/test_zernio_keys.py`

- [ ] `iter_sync_clients` / `deactivate_orphan` / sync use site-scoped keys
- [ ] `get_enabled_key(db, key_id, site_id=...)` rejects cross-site key when site given
- [ ] Tests green

### Task 3: API + migration + site delete

**Files:**
- Modify: `backend/app/api/v1/reddit.py`
- Create: `backend/alembic/versions/r015_zernio_key_site_id.py`
- Modify: `backend/app/services/site_service.py`

- [ ] All zernio-key endpoints filter/set `ctx.site.id`
- [ ] Alembic: add column, backfill oldest site, NOT NULL, drop global unique, add `(site_id, api_key)` unique + index
- [ ] `delete_client_site` deletes `ZernioApiKey` for site

### Task 4: Frontend copy + handbook

**Files:**
- Modify: `frontend/src/views/SocialAccounts.vue`
- Modify: `使用手册.md`

- [ ] Clarify Key is per current site
- [ ] Handbook: Key 按站隔离；旧 Key 归默认/最早站
