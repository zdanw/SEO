# Product Keywords Replace Reddit Keyword Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove site-level Reddit keyword library; store keywords per product; smart discover searches communities using a random product keyword only.

**Architecture:** New `reddit_product_keywords` table (FK cascade). Product create/update accept `keywords: string[]` with full replace. Discover loads product keywords, picks one at random for search, uses all for relevance. Delete `reddit_keywords` model/API/UI.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Vue 3 + Element Plus, pytest

**Spec:** `docs/superpowers/specs/2026-09-21-product-keywords-replace-reddit-keyword-library-design.md`

## Global Constraints

- Search terms: product keywords only (never name/category/talking_points)
- No priority field; random pick for search keyword
- Drop `reddit_keywords` entirely; no data migration
- SERP `keywords` table untouched
- Keyword cap: 20 per product after trim/dedupe

## File map

| File | Responsibility |
|------|----------------|
| `backend/alembic/versions/r012_product_keywords.py` | Create `reddit_product_keywords`, drop `reddit_keywords` |
| `backend/app/models/reddit.py` | Add `RedditProductKeyword`; remove `RedditKeyword`; relationship on product |
| `backend/app/schemas/reddit.py` | Product In/Update/Out `keywords`; remove RedditKeyword schemas |
| `backend/app/api/v1/reddit.py` | Replace keywords on product CRUD; remove keyword routes; drop used_count bump |
| `backend/app/services/reddit_discover.py` | Terms from keywords; random search pick; skip promo search if empty |
| `backend/app/services/site_service.py` | Stop deleting RedditKeyword |
| `backend/app/models/__init__.py` | Export swap |
| `frontend/src/api/reddit.ts` | Product `keywords`; remove RedditKeyword APIs |
| `frontend/src/views/BrandProducts.vue` | Keywords field on product form |
| `frontend/src/components/RedditOperations.vue` | Remove 关键词库 tab |
| `backend/tests/test_product_keywords.py` | Normalize/replace helpers + discover term behavior |
| `backend/tests/test_reddit_discover.py` | Update `product_search_terms` / promo random tests |

---

### Task 1: Discover helpers (keywords-only + random pick)

**Files:**
- Modify: `backend/app/services/reddit_discover.py`
- Modify: `backend/tests/test_reddit_discover.py`
- Create: `backend/tests/test_product_keywords.py` (pure helper tests if extracted)

**Interfaces:**
- Produces:
  - `normalize_product_keywords(raw: list[str] | None, *, limit: int = 20) -> list[str]`
  - `product_search_terms(*, keywords: list[str] | None = None) -> list[str]` (replaces name/category/talking_points signature)
  - `pick_promo_search_keyword(terms: list[str], *, seed: int | None = None) -> str` — empty → `""`; else `Random(seed).choice(terms)` when seed set, else `random.choice`
- Consumes: existing `run_smart_discover(..., product_terms=..., seed=...)`

- [ ] **Step 1: Write failing tests**

In `backend/tests/test_reddit_discover.py`, replace `test_product_search_terms_dedupes` with:

```python
def test_product_search_terms_from_keywords_only():
    terms = product_search_terms(keywords=[" Baby Monitors ", "EMF", "Baby Monitors", ""])
    assert terms == ["Baby Monitors", "EMF"]


def test_pick_promo_search_keyword_uses_seed():
    from app.services.reddit_discover import pick_promo_search_keyword
    terms = ["a", "b", "c"]
    assert pick_promo_search_keyword(terms, seed=7) == pick_promo_search_keyword(terms, seed=7)
    assert pick_promo_search_keyword([], seed=1) == ""
```

Update `test_run_smart_discover_promo_skips_unrelated_posts` so `assert keyword == "Baby Monitors"` becomes `assert keyword in {"Baby Monitors", "Monitors"}` (or fix product_terms to single known word if seed forces pick).

Add:

```python
def test_run_smart_discover_promo_skips_when_no_product_terms():
    calls = []
    def feed(subreddit, keyword, limit):
        calls.append((subreddit, keyword))
        return []
    result = run_smart_discover(
        persona_communities=[],
        promo_communities=[],
        already_commented=set(),
        search_fn=feed,
        generate_fn=lambda *_: None,
        allow_promo=True,
        remaining_slots=3,
        targets=[("Buyingforbaby", "promo")],
        product_terms=[],
        now=datetime.now(timezone.utc),
    )
    assert calls == []
    assert result["queued"] == 0
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
cd backend && python -m pytest tests/test_reddit_discover.py::test_product_search_terms_from_keywords_only tests/test_reddit_discover.py::test_pick_promo_search_keyword_uses_seed tests/test_reddit_discover.py::test_run_smart_discover_promo_skips_when_no_product_terms -v
```

- [ ] **Step 3: Implement helpers + wire `run_smart_discover` / `smart_discover_for_account`**

```python
def normalize_product_keywords(raw: list[str] | None, *, limit: int = 20) -> list[str]:
    out, seen = [], set()
    for item in raw or []:
        term = " ".join(str(item or "").split()).strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
        if len(out) >= limit:
            break
    return out

def product_search_terms(*, keywords: list[str] | None = None) -> list[str]:
    return normalize_product_keywords(keywords)

def pick_promo_search_keyword(terms: list[str], *, seed: int | None = None) -> str:
    clean = [t for t in terms if t]
    if not clean:
        return ""
    rng = random.Random(seed) if seed is not None else random
    return rng.choice(clean)
```

In `run_smart_discover`: `promo_keyword = pick_promo_search_keyword(terms, seed=seed)`; when `intent == "promo"` and not `promo_keyword`, `continue` (skip search).

In `smart_discover_for_account`: load keywords from `product.keywords` relationship (or query), set `product_terms = product_search_terms(keywords=[k.keyword for k in ...])`. Remove name/category/talking_points path.

- [ ] **Step 4: Run tests — expect PASS**

```bash
cd backend && python -m pytest tests/test_reddit_discover.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reddit_discover.py backend/tests/test_reddit_discover.py
git commit -m "feat(reddit): use product keywords only for discover search"
```

---

### Task 2: Model + migration

**Files:**
- Create: `backend/alembic/versions/r012_product_keywords.py` (`down_revision = "r011_task_runs_ops"`)
- Modify: `backend/app/models/reddit.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/services/site_service.py` (remove RedditKeyword delete)

**Interfaces:**
- Produces: `class RedditProductKeyword` with `product_id`, `keyword`, `created_at`; `RedditProduct.keyword_rows` relationship; property or helper list of strings for API

- [ ] **Step 1: Add model**

```python
class RedditProductKeyword(Base):
    __tablename__ = "reddit_product_keywords"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(Integer, ForeignKey("reddit_products.id", ondelete="CASCADE"), nullable=False, index=True)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
```

On `RedditProduct`:

```python
keyword_rows: Mapped[list["RedditProductKeyword"]] = relationship(
    "RedditProductKeyword", back_populates="product", cascade="all, delete-orphan"
)
```

Delete `RedditKeyword` class. Update `__init__.py` exports. Remove `RedditKeyword` from `site_service.py`.

- [ ] **Step 2: Migration**

Create table with unique index on `(product_id, lower(keyword))` via `sa.text("lower(keyword)")` or app-level unique + plain index on `(product_id, keyword)`. Prefer:

```python
op.create_table(
    "reddit_product_keywords",
    sa.Column("id", sa.Integer(), primary_key=True),
    sa.Column("product_id", sa.Integer(), sa.ForeignKey("reddit_products.id", ondelete="CASCADE"), nullable=False),
    sa.Column("keyword", sa.String(200), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)
op.create_index("ix_reddit_product_keywords_product_id", "reddit_product_keywords", ["product_id"])
op.create_index("uq_reddit_product_keywords_product_keyword", "reddit_product_keywords", ["product_id", "keyword"], unique=True)
op.drop_index("ix_reddit_keywords_category", table_name="reddit_keywords")
op.drop_index("ix_reddit_keywords_keyword", table for reddit_keywords)
op.drop_index("ix_reddit_keywords_site_id", ...)
op.drop_table("reddit_keywords")
```

(Match exact index names from `r005_restore_zernio_reddit_ops.py`.)

- [ ] **Step 3: Commit**

```bash
git add backend/alembic/versions/r012_product_keywords.py backend/app/models/reddit.py backend/app/models/__init__.py backend/app/services/site_service.py
git commit -m "feat(reddit): add product keywords table, drop reddit_keywords"
```

---

### Task 3: Product API replace keywords; remove keyword routes

**Files:**
- Modify: `backend/app/schemas/reddit.py`
- Modify: `backend/app/api/v1/reddit.py`
- Create: `backend/tests/test_product_keywords.py` (API-level if TestClient available; else unit-test `_set_product_keywords`)

**Interfaces:**
- Produces: `_set_product_keywords(db, product, keywords: list[str]) -> None` full replace using `normalize_product_keywords`
- Schema: `keywords: list[str]` on In/Update/Out (Update optional; omit = no change; empty list clears)

- [ ] **Step 1: Failing unit test for replace helper** (extract helper next to community setter pattern)

```python
def test_normalize_product_keywords_caps_at_20():
    from app.services.reddit_discover import normalize_product_keywords
    raw = [f"kw{i}" for i in range(25)]
    assert len(normalize_product_keywords(raw)) == 20
```

- [ ] **Step 2: Schema + `_product_out` include keywords; create/update call `_set_product_keywords`; remove `/keywords` routes and used_count block in post generate; remove RedditKeyword imports**

- [ ] **Step 3: Run**

```bash
cd backend && python -m pytest tests/test_product_keywords.py tests/test_reddit_brand_product.py tests/test_product_community_bind.py -v
```

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(reddit): product CRUD keywords; remove reddit keyword API"
```

---

### Task 4: Frontend BrandProducts + remove 关键词库 tab

**Files:**
- Modify: `frontend/src/api/reddit.ts`
- Modify: `frontend/src/views/BrandProducts.vue`
- Modify: `frontend/src/components/RedditOperations.vue`

- [ ] **Step 1: API types** — `RedditBrandProduct.keywords?: string[]`; create/update payloads include `keywords`; delete `RedditKeyword` interface and `list/create/update/deleteRedditKeyword*` functions

- [ ] **Step 2: BrandProducts** — form field `productKeywordsText`, comma split like talking_points; load/save `keywords`; optional tag suffix `· N词`

- [ ] **Step 3: RedditOperations** — remove keywords tab pane, dialog, state, loadKeywords, useKeyword; remove imports; if `innerTab === 'keywords'` default elsewhere; smart discover: if product selected and `keywords.length === 0`, ElMessage.warning before call

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(ui): bind keywords on products; remove Reddit keyword library tab"
```

---

### Task 5: Verify + docs touch (optional)

- [ ] **Step 1:** `cd backend && python -m pytest tests/test_reddit_discover.py tests/test_product_keywords.py tests/test_reddit_brand_product.py -v`
- [ ] **Step 2:** If `使用手册.md` documents 关键词库 under 社交分发, update one short paragraph to point at 品牌/产品库 keywords (only if section exists)
- [ ] **Step 3:** Final commit if docs changed

---

## Spec coverage

| Spec item | Task |
|-----------|------|
| Table `reddit_product_keywords` | 2 |
| Drop `reddit_keywords` | 2–3 |
| Product `keywords[]` API | 3 |
| Random search keyword | 1 |
| All terms for relevance | 1 (existing prefer_product_relevant) |
| Empty → no promo search | 1 |
| BrandProducts UI | 4 |
| Remove 关键词库 tab | 4 |
| SERP untouched | (no changes) |
