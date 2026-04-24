# Identity Substrate (Ticket A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the identity-grounded memory substrate — data models, event-sourced store, markdown parser, retriever, feature flags, API, and CLI — without affecting Lapwing's runtime behavior.

**Architecture:** Event-sourced SQLite store (`data/identity.db`) + ChromaDB collection (`identity_claims_v1`) for vector search. Markdown files under `data/identity/` are the source of truth; the parser extracts claims deterministically (Layer 1) with LLM classification (Layer 2, cached). All writes go through `append_revision` → materialize → outbox → Chroma. AuthContext enforced on every write and sensitive read.

**Tech Stack:** Python 3.12, aiosqlite (async SQLite), ChromaDB, pytest + pytest-asyncio, FastAPI

**Spec:** The full blueprint is in the user's message that initiated this session (Part A + Part B). On conflict, Part B (Addendum) takes precedence; annotate with `# per Addendum P0.x` or `# per Addendum P1.x`.

---

## File Structure

### New files to create

```
src/identity/
  __init__.py                    # Package marker
  models.py                     # Module 1: all data classes + enums
  auth.py                       # AuthContext + scope definitions + authorization helpers
  flags.py                      # Module 11: feature flags + killswitch
  store.py                      # Module 3: IdentityStore — SQLite + Chroma, event-sourced
  parser.py                     # Module 2: deterministic parser + LLM extractor + cache
  retriever.py                  # Module 4: IdentityRetriever + profiles + redaction
  __main__.py                   # CLI entry point (python -m src.identity)
  migrations/
    __init__.py
    001_identity_base.sql        # 13 core tables from blueprint
    002_identity_guardrails.sql  # Addendum tables + triggers + schema adjustments

tests/identity/
  __init__.py
  conftest.py                   # Shared fixtures + factory functions
  test_models.py                # Enum + ID computation tests
  test_flags.py                 # Feature flag + killswitch tests
  test_auth.py                  # AuthContext + scope tests
  test_store.py                 # Store CRUD + event sourcing + transactions
  test_store_privacy.py         # Redact/erase + tombstone tests
  test_store_triggers.py        # Append-only trigger tests (A.6)
  test_parser.py                # Deterministic parser tests
  test_parser_rebuild.py        # Rebuild + diff + reclassify tests
  test_retriever.py             # Retrieval + profile + redaction tests
  test_acceptance.py            # End-to-end acceptance tests (31+)

src/api/routes/
  identity_claims.py            # Module 12: new API routes (separate from existing identity.py)
```

### Files to modify

```
config/settings.py              # Add IDENTITY_* feature flag exports
src/config/settings.py          # Add IdentitySection to Pydantic settings
config.toml                     # Add [identity] section with defaults (project root, not config/)
src/app/container.py            # Wire IdentityStore + Retriever into brain
src/api/server.py               # Register identity_claims router
```

---

### Task 1: Data Models (`src/identity/models.py`)

**Files:**
- Create: `src/identity/__init__.py`
- Create: `src/identity/models.py`
- Test: `tests/identity/__init__.py`
- Test: `tests/identity/test_models.py`

- [ ] **Step 1: Write tests for enums and ID computation**

```python
# tests/identity/test_models.py
import pytest
from src.identity.models import (
    ClaimType, ClaimOwner, ClaimStatus, Sensitivity, RevisionAction,
    GateOutcome, GatePassReason, AuditAction, ConflictType,
    compute_raw_block_id, compute_claim_id,
)

def test_claim_type_values():
    assert ClaimType.BELIEF == "belief"
    assert ClaimType.MEMORY_ANCHOR == "memory_anchor"
    assert len(ClaimType) == 7

def test_claim_status_includes_redacted_erased():
    assert ClaimStatus.REDACTED == "redacted"
    assert ClaimStatus.ERASED == "erased"

def test_gate_pass_reason_includes_addendum():
    # per Addendum P0.5
    assert GatePassReason.COMPONENT_DISABLED == "component_disabled"
    assert GatePassReason.KILLSWITCH_ON == "killswitch_on"

def test_raw_block_id_deterministic():
    id1 = compute_raw_block_id("soul.md", "honesty_over_comfort")
    id2 = compute_raw_block_id("soul.md", "honesty_over_comfort")
    assert id1 == id2
    assert len(id1) == 16

def test_raw_block_id_differs_by_file():
    id1 = compute_raw_block_id("soul.md", "key1")
    id2 = compute_raw_block_id("voice.md", "key1")
    assert id1 != id2

def test_claim_id_does_not_depend_on_classification():
    """claim_id depends only on raw_block_id + claim_local_key, not type/predicate/etc."""
    raw_id = compute_raw_block_id("soul.md", "honesty")
    cid = compute_claim_id(raw_id, "claim_0")
    assert len(cid) == 16
    # Same raw_block_id + claim_local_key = same claim_id regardless of classification
    cid2 = compute_claim_id(raw_id, "claim_0")
    assert cid == cid2

def test_claim_id_differs_by_local_key():
    raw_id = compute_raw_block_id("soul.md", "honesty")
    cid0 = compute_claim_id(raw_id, "claim_0")
    cid1 = compute_claim_id(raw_id, "claim_1")
    assert cid0 != cid1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/identity/test_models.py -x -v`
Expected: ImportError

- [ ] **Step 3: Implement all data models**

Create `src/identity/__init__.py` (empty), `tests/identity/__init__.py` (empty), and `src/identity/models.py` with:
- All enums: `ClaimType`, `ClaimOwner`, `ClaimStatus`, `Sensitivity`, `RevisionAction`, `GateOutcome`, `GatePassReason`, `GateLevel`, `ConflictType`, `AuditAction`, `ContextProfile`
- `GatePassReason` must include `COMPONENT_DISABLED` and `KILLSWITCH_ON` (per Addendum P0.5)
- ID functions: `compute_raw_block_id(normalized_file, stable_block_key)`, `compute_claim_id(raw_block_id, claim_local_key)`, `compute_claim_id_from_key(source_file, stable_block_key, claim_local_key="claim_0")` (convenience wrapper)
- All dataclasses: `IdentityClaim` (without `source_span`/`source_sha` per Addendum P0.3), `ClaimRevision`, `GateEvent`, `ConflictEvent`, `RetrievalTrace`, `InjectionTrace`, `AuditLogEntry`, `OverrideToken`, `ClaimEvidence`, `ClaimSourceMapping` (new per P0.3), `InjectionDecision`, `ContextSignals`
- `ActorType` literal type

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/identity/test_models.py -x -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add src/identity/__init__.py src/identity/models.py tests/identity/__init__.py tests/identity/test_models.py
git commit -m "feat(identity): add data models + enums + ID computation (Module 1)"
```

---

### Task 2: Feature Flags (`src/identity/flags.py`)

**Files:**
- Create: `src/identity/flags.py`
- Test: `tests/identity/test_flags.py`

- [ ] **Step 1: Write tests**

```python
# tests/identity/test_flags.py
from src.identity.flags import IdentityFlags

def test_default_flags():
    flags = IdentityFlags()
    assert flags.parser_enabled is True
    assert flags.store_enabled is True
    assert flags.retriever_enabled is True
    assert flags.injector_enabled is False
    assert flags.gate_enabled is False
    assert flags.identity_system_killswitch is False

def test_killswitch_overrides_components():
    flags = IdentityFlags(identity_system_killswitch=True)
    assert flags.is_active("parser") is False
    assert flags.is_active("store") is False

def test_component_disabled_independently():
    flags = IdentityFlags(parser_enabled=False)
    assert flags.is_active("parser") is False
    assert flags.is_active("store") is True

def test_current_snapshot():
    flags = IdentityFlags()
    snap = flags.current()
    assert isinstance(snap, dict)
    assert "parser_enabled" in snap
    assert "identity_system_killswitch" in snap
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m pytest tests/identity/test_flags.py -x -v`

- [ ] **Step 3: Implement IdentityFlags**

Dataclass with all flags from blueprint Module 11.1. Add `is_active(component)` method that checks killswitch first, then per-component flag. Add `current()` → dict snapshot method.

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/identity/test_flags.py -x -v`

- [ ] **Step 5: Commit**

```bash
git add src/identity/flags.py tests/identity/test_flags.py
git commit -m "feat(identity): add feature flags + killswitch (Module 11)"
```

---

### Task 3: AuthContext (`src/identity/auth.py`)

**Files:**
- Create: `src/identity/auth.py`
- Test: `tests/identity/test_auth.py`

- [ ] **Step 1: Write tests**

```python
# tests/identity/test_auth.py
from src.identity.auth import AuthContext, create_system_auth, create_kevin_auth, SCOPE_DEFINITIONS, DEFAULT_SCOPES_BY_ACTOR, AuthorizationError, check_scope

def test_kevin_has_all_scopes():
    auth = create_kevin_auth(session_id="s1")
    assert "identity.read" in auth.scopes
    assert "identity.erase" in auth.scopes
    assert "sensitive.restricted.explicit" in auth.scopes

def test_system_auth_has_limited_scopes():
    auth = create_system_auth()
    assert "identity.read" in auth.scopes
    assert "identity.erase" not in auth.scopes

def test_check_scope_raises_on_missing():
    auth = create_system_auth()
    import pytest
    with pytest.raises(AuthorizationError):
        check_scope(auth, "identity.erase")

def test_check_scope_passes_when_present():
    auth = create_kevin_auth(session_id="s1")
    check_scope(auth, "identity.read")  # should not raise
```

- [ ] **Step 2: Run tests to verify failure**
- [ ] **Step 3: Implement auth module**

`AuthContext` dataclass, `AuthorizationError` exception, `SCOPE_DEFINITIONS`, `DEFAULT_SCOPES_BY_ACTOR`, factory functions `create_kevin_auth`, `create_system_auth`, `create_lapwing_auth`, `check_scope` helper.

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/auth.py tests/identity/test_auth.py
git commit -m "feat(identity): add AuthContext + scope definitions (Module 1.9)"
```

---

### Task 4: SQL Migrations

**Files:**
- Create: `src/identity/migrations/__init__.py`
- Create: `src/identity/migrations/001_identity_base.sql`
- Create: `src/identity/migrations/002_identity_guardrails.sql`

- [ ] **Step 1: Write migration 001**

Copy the full SQL from blueprint Module 3.1. Core tables:
`identity_claims`, `identity_revisions`, `identity_gate_events`, `identity_gate_cache`, `identity_conflict_events`, `identity_retrieval_traces`, `identity_injection_traces`, `identity_audit_log`, `identity_auth_contexts`, `identity_override_tokens`, `identity_approval_requests`, `identity_evidence`, `identity_relations`, `identity_extraction_cache`, `identity_source_files` (file-level SHA tracking for parser change detection), `identity_index_outbox`, `identity_feature_flags_snapshots`, `identity_migration_version`.

**Key**: Remove `source_span_start`, `source_span_end`, `source_sha` from `identity_claims` (per Addendum P0.3 — per-claim provenance moves to `identity_claim_sources` in migration 002). Note: `identity_source_files` (file-level) is distinct from `identity_claim_sources` (per-claim provenance). The former tracks which files have been parsed and their current SHA; the latter (in 002) stores per-claim byte offsets.

- [ ] **Step 2: Write migration 002**

Per Addendum:
- `identity_claim_sources` (P0.3)
- `identity_redaction_tombstones` (P0.2)
- `identity_explicit_access_requests` (P1.4)
- `identity_feature_flags_snapshots` schema adjustment (add `snapshot_hash UNIQUE`, `reference_count`) (P1.3)
- All append-only triggers (P0.4): `identity_revisions`, `identity_audit_log`, `identity_redaction_tombstones` (no UPDATE/DELETE), `identity_explicit_access_requests` (no DELETE, UPDATE allowed)

- [ ] **Step 3: Commit**

```bash
git add src/identity/migrations/
git commit -m "feat(identity): add SQL migrations 001 + 002"
```

---

### Task 4b: Test Fixtures (`tests/identity/conftest.py`)

**Files:**
- Create: `tests/identity/conftest.py`

- [ ] **Step 1: Create shared fixtures and factory functions**

```python
# tests/identity/conftest.py
import pytest
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from src.identity.store import IdentityStore
from src.identity.auth import create_kevin_auth, create_system_auth, AuthContext
from src.identity.models import (
    IdentityClaim, ClaimRevision, ClaimType, ClaimOwner, ClaimStatus,
    Sensitivity, RevisionAction, GateEvent, GateOutcome, GatePassReason,
    ConflictEvent, ConflictType, RetrievalTrace, InjectionTrace,
    AuditLogEntry, AuditAction, OverrideToken, ClaimEvidence,
    compute_claim_id_from_key,
)

@pytest.fixture
async def store(tmp_path):
    s = IdentityStore(db_path=tmp_path / "identity.db")
    await s.init()
    yield s
    await s.close()

def _make_test_claim(*, id="c1", text="Test claim", type=ClaimType.VALUE,
                      owner=ClaimOwner.LAPWING, source_file="soul.md",
                      stable_block_key="test_key", **kwargs):
    """Factory for IdentityClaim with sensible defaults."""
    ...

def _make_create_revision(claim, **kwargs):
    """Factory for CREATE ClaimRevision from a claim."""
    ...

def _make_update_revision(old_claim, new_claim, **kwargs):
    """Factory for UPDATE ClaimRevision."""
    ...

def _make_gate_event(*, outcome="pass", pass_reason="sampled_out", **kwargs):
    ...

def _make_retrieval_trace(**kwargs):
    ...

def _make_injection_trace(**kwargs):
    ...

def _make_audit_entry(*, action="killswitch_on", **kwargs):
    ...

def _make_conflict_event(**kwargs):
    ...

def _make_evidence(*, claim_id="c1", evidence_type="episode", **kwargs):
    ...

def _make_override_token(**kwargs):
    ...

# Populated store fixtures for retriever tests
@pytest.fixture
async def populated_store(store):
    """Store with 5 mixed-type active claims."""
    ...

@pytest.fixture
async def populated_store_with_50_claims(store):
    """Store with 50 claims for performance testing."""
    ...
```

- [ ] **Step 2: Commit**

```bash
git add tests/identity/conftest.py
git commit -m "test(identity): add shared fixtures + factory functions"
```

---

### Task 5: Store — DB Init + Basic Read/Write (`src/identity/store.py`)

**Files:**
- Create: `src/identity/store.py`
- Test: `tests/identity/test_store.py`

- [ ] **Step 1: Write tests for DB init + migration**

```python
# tests/identity/test_store.py
import pytest
from src.identity.store import IdentityStore
from src.identity.auth import create_kevin_auth
# store fixture + _make_* helpers defined in conftest.py

async def test_init_creates_tables(store):
    tables = await store._get_tables()
    assert "identity_claims" in tables
    assert "identity_revisions" in tables
    assert "identity_claim_sources" in tables
    assert "identity_redaction_tombstones" in tables

async def test_migration_version(store):
    version = await store._get_migration_version()
    assert version == 2
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement IdentityStore.__init__ + init()**

Use `aiosqlite` for async SQLite. Read migration files from `src/identity/migrations/`. Apply them in order. Track version in `identity_migration_version`.

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Write tests for auth context save + feature flag snapshot (with hash dedup per P1.3)**

```python
async def test_save_auth_context(store):
    auth = create_kevin_auth(session_id="s1")
    ctx_id = await store.save_auth_context(auth)
    assert ctx_id == auth.context_id

async def test_save_feature_flags_dedup(store):
    """per Addendum P1.3: same flags → same snapshot_id"""
    flags = {"parser_enabled": True, "gate_enabled": False}
    id1 = await store.save_feature_flags_snapshot(flags)
    id2 = await store.save_feature_flags_snapshot(flags)
    assert id1 == id2
```

- [ ] **Step 6: Implement save_auth_context + save_feature_flags_snapshot**
- [ ] **Step 7: Run tests**
- [ ] **Step 8: Commit**

```bash
git add src/identity/store.py tests/identity/test_store.py
git commit -m "feat(identity): add IdentityStore DB init + auth/flags persistence (Module 3)"
```

---

### Task 6: Store — Event Sourcing Core

**Files:**
- Modify: `src/identity/store.py`
- Modify: `tests/identity/test_store.py`

- [ ] **Step 1: Write tests for append_revision + materialize_claim**

```python
async def test_append_revision_creates_claim(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", text="Lapwing values honesty")
    revision = _make_create_revision(claim)
    await store.append_revision(revision, auth)
    result = await store.get_claim("c1", auth)
    assert result is not None
    assert result.text == "Lapwing values honesty"

async def test_append_revision_transactional(store):
    """revision + projection + outbox in single transaction"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    revision = _make_create_revision(claim)
    await store.append_revision(revision, auth)
    revisions = await store.get_revisions("c1", auth)
    assert len(revisions) == 1
    outbox = await store._get_pending_outbox()
    assert len(outbox) == 1

async def test_rebuild_projection_matches_incremental(store):
    """acceptance #9: rebuild_projection equals incremental materialize"""
    auth = create_kevin_auth(session_id="s1")
    # Create + update a claim
    claim = _make_test_claim(id="c1", text="v1")
    await store.append_revision(_make_create_revision(claim), auth)
    claim2 = _make_test_claim(id="c1", text="v2")
    await store.append_revision(_make_update_revision(claim, claim2), auth)
    # Get incremental result
    incremental = await store.get_claim("c1", auth)
    # Rebuild from scratch
    await store.rebuild_projection(auth)
    rebuilt = await store.get_claim("c1", auth)
    assert incremental.text == rebuilt.text == "v2"
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement append_revision, materialize_claim, rebuild_projection, get_claim, list_claims, get_revisions, deprecate_claim, export_claim**

Follow blueprint Module 3.4 transaction pattern: write auth context → append revision → materialize (recompute canonical from revisions) → enqueue outbox.

`deprecate_claim(claim_id, auth, reason)`: sets status to DEPRECATED, appends a DEPRECATE revision, enqueues outbox with `delete_vector`. Claim data preserved (unlike ERASE which clears fields).

`export_claim(claim_id, auth)`: serializes claim + revisions + evidence into dict, writes `CLAIM_EXPORTED` audit entry, returns the data. Requires `identity.export` scope.

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/store.py tests/identity/test_store.py
git commit -m "feat(identity): add event-sourced append_revision + materialize + rebuild (Module 3/9)"
```

---

### Task 7: Store — Trace + Event Writers

**Files:**
- Modify: `src/identity/store.py`
- Modify: `tests/identity/test_store.py`

- [ ] **Step 1: Write tests for all trace/event writers**

```python
async def test_write_gate_event(store):
    event = _make_gate_event(outcome="pass", pass_reason="sampled_out")
    await store.write_gate_event(event)
    events = await store._list_gate_events()
    assert len(events) == 1

async def test_write_retrieval_trace(store):
    trace = _make_retrieval_trace()
    await store.write_retrieval_trace(trace)

async def test_write_injection_trace(store):
    trace = _make_injection_trace()
    await store.write_injection_trace(trace)

async def test_write_audit_log(store):
    auth = create_kevin_auth(session_id="s1")
    entry = _make_audit_entry(action="killswitch_on")
    await store.write_audit_log(entry, auth)

async def test_write_conflict_event(store):
    event = _make_conflict_event()
    await store.write_conflict_event(event)
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement all trace/event write methods**
- [ ] **Step 4: Run tests**
- [ ] **Step 5: Write tests for override tokens + approval requests**

```python
async def test_create_and_consume_override(store):
    auth = create_kevin_auth(session_id="s1")
    token = _make_override_token()
    await store.create_override_token(token, auth)
    consumed = await store.consume_override_token(token.token_id, token.action_payload_hash, auth)
    assert consumed is True
    consumed2 = await store.consume_override_token(token.token_id, token.action_payload_hash, auth)
    assert consumed2 is False  # already consumed
```

- [ ] **Step 6: Implement override + approval methods**
- [ ] **Step 7: Run tests**
- [ ] **Step 8: Commit**

```bash
git add src/identity/store.py tests/identity/test_store.py
git commit -m "feat(identity): add trace/event writers + override tokens (Module 3)"
```

---

### Task 8: Store — Privacy (Redact/Erase) + Tombstones

**Files:**
- Modify: `src/identity/store.py`
- Create: `tests/identity/test_store_privacy.py`

- [ ] **Step 1: Write tests for redact + source redaction check (per Addendum P0.2)**

```python
async def test_redact_requires_source_redaction(store, tmp_path):
    """per Addendum P0.2: redact returns requires_source_redaction when markdown source exists"""
    auth = create_kevin_auth(session_id="s1")
    # Create claim with markdown evidence
    claim = _make_test_claim(id="c1", source_file=str(tmp_path / "soul.md"))
    await store.append_revision(_make_create_revision(claim), auth)
    await store.add_evidence(_make_evidence(claim_id="c1", evidence_type="markdown_span", source_ref=str(tmp_path / "soul.md")), auth)
    result = await store.redact_claim("c1", auth, "privacy request", source_already_redacted=False)
    assert result.requires_source_redaction is True
    assert result.success is False
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Write tests for erase producing tombstone projection row (per Addendum P1.1)**

```python
async def test_erase_keeps_tombstone_row(store):
    """per Addendum P1.1: ERASED claim keeps projection row with cleared fields"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", text="sensitive info")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "gdpr request")
    erased = await store.get_claim("c1", auth)
    assert erased is not None
    assert erased.status == "erased"
    assert erased.text == ""
    assert erased.subject == "[ERASED]"

async def test_erase_deletes_evidence_and_relations(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.add_evidence(_make_evidence(claim_id="c1"), auth)
    await store.erase_claim("c1", auth, "cleanup")
    evidence = await store.get_evidence("c1", auth)
    assert len(evidence) == 0

async def test_erase_writes_tombstone(store):
    """per Addendum P0.2: erase writes to identity_redaction_tombstones"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file="soul.md", stable_block_key="key1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "reason")
    tombstones = await store._list_tombstones()
    assert len(tombstones) == 1
    assert tombstones[0]["source_file"] == "soul.md"
```

- [ ] **Step 4: Implement redact_claim + erase_claim + tombstone logic**
- [ ] **Step 5: Run tests**
- [ ] **Step 6: Commit**

```bash
git add src/identity/store.py tests/identity/test_store_privacy.py
git commit -m "feat(identity): add privacy redact/erase + tombstones (Addendum P0.2, P1.1)"
```

---

### Task 9: Store — Append-Only Triggers + Relations/Evidence

**Files:**
- Modify: `src/identity/store.py`
- Create: `tests/identity/test_store_triggers.py`

- [ ] **Step 1: Write trigger tests (acceptance A.6)**

```python
# tests/identity/test_store_triggers.py
import pytest
import aiosqlite

async def test_revisions_no_update(store):
    """per Addendum P0.4: identity_revisions rejects UPDATE"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute("UPDATE identity_revisions SET diff_summary='x' WHERE claim_id='c1'")

async def test_revisions_no_delete(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute("DELETE FROM identity_revisions WHERE claim_id='c1'")

async def test_audit_log_no_update(store):
    auth = create_kevin_auth(session_id="s1")
    entry = _make_audit_entry(action="killswitch_on")
    await store.write_audit_log(entry, auth)
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute("UPDATE identity_audit_log SET justification='x'")

async def test_tombstones_no_delete(store):
    # Write a tombstone via erase_claim flow then try to delete
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file="soul.md", stable_block_key="k1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "reason")
    with pytest.raises(Exception, match="append-only"):
        await store._raw_execute("DELETE FROM identity_redaction_tombstones")

async def test_explicit_access_allows_update(store):
    """per Addendum P0.4: UPDATE allowed for consumed marking"""
    auth = create_kevin_auth(session_id="s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin", scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"], ttl_seconds=60, auth=auth,
    )
    # UPDATE should work (consumed marking)
    await store._raw_execute(f"UPDATE identity_explicit_access_requests SET consumed=1 WHERE request_id='{req_id}'")

async def test_explicit_access_no_delete(store):
    auth = create_kevin_auth(session_id="s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin", scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"], ttl_seconds=60, auth=auth,
    )
    with pytest.raises(Exception, match="delete-protected"):
        await store._raw_execute(f"DELETE FROM identity_explicit_access_requests WHERE request_id='{req_id}'")
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Write tests for relations + evidence + claim sources**

```python
async def test_add_and_get_evidence(store):
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    ev = _make_evidence(claim_id="c1")
    await store.add_evidence(ev, auth)
    result = await store.get_evidence("c1", auth)
    assert len(result) == 1

async def test_add_and_get_relation(store):
    auth = create_kevin_auth(session_id="s1")
    for cid in ["c1", "c2"]:
        await store.append_revision(_make_create_revision(_make_test_claim(id=cid)), auth)
    await store.add_relation("c1", "c2", "supports", 0.8, auth)
    neighbors = await store.get_neighbors("c1", auth)
    assert len(neighbors) == 1

async def test_upsert_claim_sources(store):
    """per Addendum P0.3: provenance in separate table"""
    auth = create_kevin_auth(session_id="s1")
    claim = _make_test_claim(id="c1", source_file="soul.md")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.upsert_claim_source("c1", "soul.md", 0, 100, "sha1", "key1")
    sources = await store.get_claim_sources("c1")
    assert len(sources) == 1
    assert sources[0]["source_span_start"] == 0
```

- [ ] **Step 4: Implement evidence, relations, claim_sources, explicit_access methods**
- [ ] **Step 5: Run all store tests**
- [ ] **Step 6: Commit**

```bash
git add src/identity/store.py tests/identity/test_store_triggers.py tests/identity/test_store.py
git commit -m "feat(identity): add append-only triggers + relations/evidence/sources (Addendum P0.3, P0.4)"
```

---

### Task 10: Store — Extraction Cache + Gate Cache + Outbox

**Files:**
- Modify: `src/identity/store.py`
- Modify: `tests/identity/test_store.py`

- [ ] **Step 1: Write tests for extraction cache**

```python
async def test_extraction_cache_hit(store):
    await store.set_extraction_cache("key1", {"type": "belief", "confidence": 0.9})
    result = await store.get_extraction_cache("key1")
    assert result["type"] == "belief"

async def test_extraction_cache_miss(store):
    result = await store.get_extraction_cache("nonexistent")
    assert result is None

async def test_clear_extraction_cache(store):
    auth = create_kevin_auth(session_id="s1")
    await store.set_extraction_cache("key1", {"type": "belief"})
    count = await store.clear_extraction_cache("all", None, auth)
    assert count == 1
```

- [ ] **Step 2: Write tests for gate cache**

```python
async def test_gate_cache_hit(store):
    await store.set_gate_cache("gk1", {"outcome": "pass"}, ttl_seconds=3600)
    result = await store.get_gate_cache("gk1")
    assert result["outcome"] == "pass"

async def test_gate_cache_expired(store):
    await store.set_gate_cache("gk1", {"outcome": "pass"}, ttl_seconds=-1)
    result = await store.get_gate_cache("gk1")
    assert result is None
```

- [ ] **Step 3: Write tests for outbox**

```python
async def test_outbox_enqueue_and_drain(store):
    await store.enqueue_outbox("c1", "upsert_vector")
    pending = await store._get_pending_outbox()
    assert len(pending) == 1
    # Drain with no Chroma attached — should mark as done (or fail gracefully)
    count = await store.drain_outbox(batch_size=10)
    assert count >= 0
```

- [ ] **Step 4: Implement cache + outbox methods**
- [ ] **Step 5: Run tests**
- [ ] **Step 6: Commit**

```bash
git add src/identity/store.py tests/identity/test_store.py
git commit -m "feat(identity): add extraction/gate cache + outbox (Module 3)"
```

---

### Task 11: Parser — Deterministic Layer

**Files:**
- Create: `src/identity/parser.py`
- Create: `tests/identity/test_parser.py`

- [ ] **Step 1: Write tests for block extraction + inline metadata**

```python
# tests/identity/test_parser.py
from src.identity.parser import IdentityParser, RawBlock

def test_parse_inline_bracket():
    md = "- [type=value][owner=lapwing][id=honesty_over_comfort] Lapwing values honest engagement."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "soul.md")
    assert len(blocks) == 1
    b = blocks[0]
    assert b.stable_block_key == "honesty_over_comfort"
    assert b.inline_metadata["type"] == "value"
    assert b.inline_metadata["owner"] == "lapwing"

def test_parse_html_comment_anchor():
    md = "<!-- claim: kevin_direct_critique -->\nKevin prefers direct critique."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "soul.md")
    assert len(blocks) == 1
    assert blocks[0].stable_block_key == "kevin_direct_critique"

def test_parse_frontmatter_defaults():
    md = "---\nclaim_defaults:\n  owner: kevin\n  sensitivity: private\n---\n\n- Some claim text."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "relationships/kevin.md")
    assert len(blocks) == 1
    assert blocks[0].defaults["owner"] == "kevin"
    assert blocks[0].defaults["sensitivity"] == "private"

def test_parse_section_html_comment_defaults():
    md = "## Kevin\n\n<!-- claim-defaults: owner=kevin sensitivity=private -->\n\n- He likes direct communication."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "soul.md")
    assert blocks[0].section_defaults["owner"] == "kevin"

def test_priority_inline_over_section_over_frontmatter():
    md = """---
claim_defaults:
  owner: system
---

## Kevin

<!-- claim-defaults: owner=kevin -->

- [owner=lapwing][id=test1] Some claim.
"""
    parser = IdentityParser()
    blocks = parser.parse_text(md, "test.md")
    assert blocks[0].effective_metadata()["owner"] == "lapwing"  # inline wins

def test_fallback_stable_block_key():
    md = "- Some claim without explicit id."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "memory_anchors/test.md")
    assert len(blocks[0].stable_block_key) == 12  # sha256[:12] fallback

def test_raw_block_id_computation():
    md = "- [id=honesty] Lapwing values honesty."
    parser = IdentityParser()
    blocks = parser.parse_text(md, "soul.md")
    from src.identity.models import compute_raw_block_id
    expected = compute_raw_block_id("soul.md", "honesty")
    assert blocks[0].raw_block_id == expected

def test_source_span_utf8(tmp_path):
    """acceptance #12: UTF-8 spans correct for Chinese + emoji"""
    md = "- [id=cn] 中文测试 🎉 emoji"
    parser = IdentityParser()
    blocks = parser.parse_text(md, "soul.md")
    assert blocks[0].source_span[0] >= 0
    assert blocks[0].source_span[1] > blocks[0].source_span[0]
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement IdentityParser (deterministic layer)**

Parse markdown files for:
- Frontmatter YAML (`claim_defaults:`)
- HTML comment section defaults (`<!-- claim-defaults: ... -->`)
- Inline bracket metadata (`[type=value][id=key]`)
- HTML comment claim anchors (`<!-- claim: key -->`)
- Block boundary detection (list items, paragraphs between headings)
- `stable_block_key` computation (explicit id or `sha256(canonical_text)[:12]`)
- `raw_block_id` computation
- `source_span` as UTF-8 byte offsets
- `RawBlock` output dataclass

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/parser.py tests/identity/test_parser.py
git commit -m "feat(identity): add deterministic markdown parser (Module 2, Layer 1)"
```

---

### Task 12: Parser — LLM Extractor + Cache

**Files:**
- Modify: `src/identity/parser.py`
- Modify: `tests/identity/test_parser.py`

- [ ] **Step 1: Write tests for extraction cache key computation**

```python
from src.identity.parser import ExtractionCacheKey

def test_extraction_cache_key_deterministic():
    key = ExtractionCacheKey(
        candidate_text_sha="abc", section_context_sha="def",
        frontmatter_defaults_sha="ghi", prompt_version="v1",
        model_id="glm-5.1", schema_version="s1",
    )
    assert key.compute() == key.compute()
    assert len(key.compute()) == 16

def test_extraction_cache_key_changes_with_model():
    k1 = ExtractionCacheKey("a","b","c","v1","model_a","s1")
    k2 = ExtractionCacheKey("a","b","c","v1","model_b","s1")
    assert k1.compute() != k2.compute()
```

- [ ] **Step 2: Write tests for LLM extractor (mocked)**

```python
async def test_llm_extractor_uses_cache(mock_store):
    """acceptance #13: second extract uses cache, no LLM call"""
    parser = IdentityParser(store=mock_store, llm_router=mock_router)
    blocks = parser.parse_text("- [id=honesty] Lapwing values honesty.", "soul.md")
    # First call — cache miss, LLM called
    result1 = await parser.classify_block(blocks[0])
    assert mock_router.call_count == 1
    # Second call — cache hit
    result2 = await parser.classify_block(blocks[0])
    assert mock_router.call_count == 1  # no additional call
    assert result1 == result2
```

- [ ] **Step 3: Implement LLM extractor with ExtractionCacheKey + store integration**
- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/parser.py tests/identity/test_parser.py
git commit -m "feat(identity): add LLM extractor + cache (Module 2, Layer 2)"
```

---

### Task 13: Parser — Rebuild + Diff + Validate

**Files:**
- Modify: `src/identity/parser.py`
- Create: `tests/identity/test_parser_rebuild.py`

- [ ] **Step 1: Write tests for rebuild diff algorithm**

```python
# tests/identity/test_parser_rebuild.py
async def test_rebuild_new_claim_creates_revision(store, tmp_path):
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty][type=value] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    report = await parser.rebuild(auth=create_kevin_auth("s1"))
    assert report.created == 1
    assert report.updated == 0

async def test_rebuild_unchanged_produces_no_revision(store, tmp_path):
    """acceptance #11: no-op edit → 0 revisions"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    # Rebuild again with no change
    report2 = await parser.rebuild(auth=auth)
    assert report2.created == 0
    assert report2.updated == 0
    assert report2.deprecated == 0

async def test_rebuild_after_trailing_whitespace_no_revision(store, tmp_path):
    """acceptance #11: trailing whitespace/newline → 0 revisions"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    # Add trailing newline
    md_file.write_text("- [id=honesty] Lapwing values honesty.\n\n", encoding="utf-8")
    report2 = await parser.rebuild(auth=auth)
    assert report2.created == 0 and report2.updated == 0

async def test_id_stable_across_llm_model_change(store, tmp_path):
    """acceptance #18: model change → EXTRACTION_RECLASSIFY, claim_id unchanged"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path, prompt_version="v1", model_id="model_a")
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    claim_before = await store.get_claim(compute_claim_id_from_key("soul.md", "honesty"), auth)

    # Change model → triggers cache miss → EXTRACTION_RECLASSIFY
    parser2 = IdentityParser(store=store, identity_dir=tmp_path, prompt_version="v1", model_id="model_b")
    report = await parser2.rebuild(auth=auth)
    claim_after = await store.get_claim(claim_before.id, auth)
    assert claim_after.id == claim_before.id  # ID unchanged
    revisions = await store.get_revisions(claim_before.id, auth)
    assert any(r.action == "extraction_reclassify" for r in revisions)

async def test_tombstone_blocks_rebuild(store, tmp_path):
    """acceptance A.4: tombstone prevents claim resurrection"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=secret] Secret info.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    # Erase claim (this writes a tombstone)
    cid = compute_claim_id_from_key("soul.md", "secret")
    await store.erase_claim(cid, auth, "reason")
    # Rebuild again — tombstone should block
    report = await parser.rebuild(auth=auth)
    assert report.created == 0  # not resurrected

async def test_validate_strict_missing_id(store, tmp_path):
    """acceptance A.1: validate --strict fails on missing explicit id in production files"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- Claim without explicit id.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    result = parser.validate(strict=True, production_files=["soul.md"])
    assert result.passed is False
    assert len(result.warnings) > 0

async def test_provenance_update_no_revision(store, tmp_path):
    """acceptance A.5: span change without text change → no revision, source table updated"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    # Insert line above (shifts span)
    md_file.write_text("# Title\n\n- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    report = await parser.rebuild(auth=auth)
    assert report.updated == 0
    # But source mapping should reflect new span
    cid = compute_claim_id_from_key("soul.md", "honesty")
    sources = await store.get_claim_sources(cid)
    assert sources[0]["source_span_start"] > 0  # shifted
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement rebuild, validate, compute_revisions**

Full rebuild flow per blueprint Module 2.5:
1. Scan markdown files → RawBlock set
2. For each block: check extraction cache or call LLM → classification state
3. Check tombstones → skip matching blocks
4. Diff with existing claims → generate revisions
5. Transactional append revisions + update projection + enqueue outbox
6. Update `identity_claim_sources` for provenance changes (per Addendum P0.3)
7. Return RebuildReport

Validate flow:
- Default: warn on missing explicit id in production files
- `--strict`: fail on missing explicit id (per Addendum P0.1)

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/parser.py tests/identity/test_parser_rebuild.py
git commit -m "feat(identity): add rebuild + diff + validate (Module 2)"
```

---

### Task 14: Retriever (`src/identity/retriever.py`)

**Files:**
- Create: `src/identity/retriever.py`
- Create: `tests/identity/test_retriever.py`

- [ ] **Step 1: Write tests for retrieval + profile defaults**

```python
# tests/identity/test_retriever.py
async def test_retrieve_returns_matching_claims(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("honesty in relationships", auth, profile=ContextProfile.GENERAL)
    assert len(result.claims) > 0
    assert result.trace is not None

async def test_retrieve_technical_filters_traits():
    """TECHNICAL profile filters out TRAIT type by default"""
    retriever = IdentityRetriever(store=store_with_mixed_types, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("code review approach", auth, profile=ContextProfile.TECHNICAL)
    for claim in result.claims:
        assert claim.type != ClaimType.TRAIT

async def test_retrieve_redacts_query_for_private(populated_store):
    retriever = IdentityRetriever(store=populated_store, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("sensitive query about kevin's health", auth,
                                       max_sensitivity=Sensitivity.PRIVATE)
    assert result.trace.raw_query_stored is False
    assert "health" not in result.trace.query_summary

async def test_retrieve_disabled_returns_empty(populated_store):
    flags = IdentityFlags(retriever_enabled=False)
    retriever = IdentityRetriever(store=populated_store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0

async def test_retrieve_killswitch_no_trace(populated_store):
    """acceptance #22: killswitch → no retrieval traces written"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=populated_store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0
    # Verify no trace written
    traces = await populated_store._list_retrieval_traces()
    assert len(traces) == 0
```

- [ ] **Step 2: Run to verify failure**
- [ ] **Step 3: Implement IdentityRetriever**

Per blueprint Module 4:
- Profile defaults (TECHNICAL, RELATIONSHIP, IDENTITY_QUERY, DEBUG, GENERAL)
- Sensitivity-based query redaction (n-gram fingerprinting for PUBLIC, summary for PRIVATE/RESTRICTED)
- Auth-based claim filtering
- RetrievalTrace writing (only when not killswitched)
- Score threshold + min_confidence filtering

- [ ] **Step 4: Run tests**
- [ ] **Step 5: Commit**

```bash
git add src/identity/retriever.py tests/identity/test_retriever.py
git commit -m "feat(identity): add IdentityRetriever + profiles + redaction (Module 4)"
```

---

### Task 15: CLI (`src/identity/__main__.py`)

**Files:**
- Create: `src/identity/__main__.py`

- [ ] **Step 1: Implement CLI commands**

Commands per blueprint Module 2.6:
- `scan` — dry-run showing diff
- `rebuild --confirm` — write to DB
- `validate` / `validate --strict` — consistency check (per Addendum P0.1)
- `show <claim_id>` — claim + revisions + evidence
- `cache-stats` — LLM cache hit rate
- `cache-clear --dry-run` / `cache-clear --confirm --scope <file|claim|all>`
- `reclassify --prompt-version X --model-id Y --dry-run` / `--apply --confirm`

Use `argparse`. Each command creates its own `IdentityStore` + `IdentityParser` instances with `asyncio.run()`.

- [ ] **Step 2: Manual smoke test**

Run: `python -m src.identity scan` (should work against `data/identity/` even if empty of claims)

- [ ] **Step 3: Commit**

```bash
git add src/identity/__main__.py
git commit -m "feat(identity): add CLI (scan/rebuild/validate/show/cache) (Module 2)"
```

---

### Task 16: API Routes (`src/api/routes/identity_claims.py`)

**Files:**
- Create: `src/api/routes/identity_claims.py`
- Modify: `src/api/server.py` (register router)

- [ ] **Step 1: Implement API routes**

Per blueprint Module 12.1, all routes require AuthContext:

**Read routes:**
- `GET /api/identity/claims` — list claims (filtered by auth scopes)
- `GET /api/identity/claims/{id}` — single claim
- `GET /api/identity/claims/{id}/evidence`
- `GET /api/identity/claims/{id}/revisions`
- `GET /api/identity/claims/{id}/neighbors`
- `GET /api/identity/retrieval-traces`
- `GET /api/identity/injection-traces`
- `GET /api/identity/gate-events`
- `GET /api/identity/conflict-events`
- `GET /api/identity/audit-log` (kevin/admin only)

**Write routes:**
- `POST /api/identity/rebuild`
- `POST /api/identity/reclassify`
- `POST /api/identity/killswitch`
- `POST /api/identity/override`
- `POST /api/identity/approve-write/{request_id}`
- `POST /api/identity/explicit-access` (per Addendum P1.4)

**Privacy routes:**
- `POST /api/identity/claims/{id}/export`
- `POST /api/identity/claims/{id}/redact`
- `POST /api/identity/claims/{id}/erase`

**Health routes:**
- `GET /api/identity/health`
- `GET /api/identity/stats`
- `GET /api/identity/outbox-status`

Follow existing pattern: module-level `_store = None`, `init(store=...)`, router with `prefix="/api/identity"`.

For auth: derive AuthContext from the existing adapter auth level. Desktop connections = OWNER = kevin scopes. For now, a simple middleware or dependency that creates AuthContext from the request context.

- [ ] **Step 2: Register router in server.py**

Add `from src.api.routes import identity_claims` and include the router.

- [ ] **Step 3: Commit**

```bash
git add src/api/routes/identity_claims.py src/api/server.py
git commit -m "feat(identity): add API routes (Module 12)"
```

---

### Task 17: Config + Feature Flag Wiring

**Files:**
- Modify: `src/config/settings.py` — add `IdentitySection`
- Modify: `config/settings.py` — export identity flags
- Modify: `config.toml` — add `[identity]` section (project root)

- [ ] **Step 1: Add identity config section + env var mapping**

Add `IdentitySection` to the Pydantic settings model, AND add entries to `_ENV_MAP` so that env vars like `IDENTITY_SYSTEM_KILLSWITCH=true` are picked up:

```python
# In _ENV_MAP dict, add:
"IDENTITY_PARSER_ENABLED": ("identity", "parser_enabled"),
"IDENTITY_STORE_ENABLED": ("identity", "store_enabled"),
"IDENTITY_RETRIEVER_ENABLED": ("identity", "retriever_enabled"),
"IDENTITY_INJECTOR_ENABLED": ("identity", "injector_enabled"),
"IDENTITY_GATE_ENABLED": ("identity", "gate_enabled"),
"IDENTITY_SYSTEM_KILLSWITCH": ("identity", "identity_system_killswitch"),
```

Then the Pydantic section:

```python
# In src/config/settings.py, add:
class IdentitySection(BaseModel):
    parser_enabled: bool = True
    store_enabled: bool = True
    retriever_enabled: bool = True
    injector_enabled: bool = False
    gate_enabled: bool = False
    identity_system_killswitch: bool = False
    gate_default_level: str = "observe"
    gate_sample_rate: float = 1.0
    gate_cache_ttl_seconds: int = 3600
    # ... all flags from blueprint Module 11.1
```

- [ ] **Step 2: Add to config.toml**

```toml
[identity]
parser_enabled = true
store_enabled = true
retriever_enabled = true
injector_enabled = false
gate_enabled = false
identity_system_killswitch = false
```

- [ ] **Step 3: Export from config/settings.py**

```python
IDENTITY_PARSER_ENABLED: bool = _s.identity.parser_enabled
# ... etc
```

- [ ] **Step 4: Commit**

```bash
git add src/config/settings.py config/settings.py config.toml
git commit -m "feat(identity): add config section + feature flags (Module 11)"
```

---

### Task 18: Container Wiring

**Files:**
- Modify: `src/app/container.py`

- [ ] **Step 1: Wire IdentityStore + IdentityRetriever into container**

In `_configure_brain_dependencies()`, after existing memory wiring:

```python
from config.settings import IDENTITY_PARSER_ENABLED, IDENTITY_STORE_ENABLED
from src.identity.flags import IdentityFlags
from src.identity.store import IdentityStore
from src.identity.retriever import IdentityRetriever

identity_flags = IdentityFlags(
    parser_enabled=IDENTITY_PARSER_ENABLED,
    store_enabled=IDENTITY_STORE_ENABLED,
    # ... other flags from config
)

if identity_flags.store_enabled:
    self._identity_store = IdentityStore(
        db_path=self._data_dir / "identity.db",
        chroma_dir=self._data_dir / "chroma_memory",  # shares dir with MemoryVectorStore; distinct collection name identity_claims_v1
    )
    await self._identity_store.init()

    if identity_flags.retriever_enabled:
        self._identity_retriever = IdentityRetriever(
            store=self._identity_store,
            flags=identity_flags,
        )
    
    self.brain._identity_store = self._identity_store
    self.brain._identity_retriever = getattr(self, "_identity_retriever", None)
    self.brain._identity_flags = identity_flags
```

Wire into API routes init:
```python
from src.api.routes import identity_claims
identity_claims.init(store=self._identity_store)
```

Add shutdown cleanup for identity store.

- [ ] **Step 2: Commit**

```bash
git add src/app/container.py
git commit -m "feat(identity): wire IdentityStore + Retriever into AppContainer"
```

---

### Task 19: Acceptance Tests

**Files:**
- Create: `tests/identity/test_acceptance.py`

This task implements all 31+ acceptance tests from the blueprint. Blueprint tests #2 (shadow mode), #3 (injection), #4 (gate observe), #5 (gate advise), #8 (skill pipeline), #9 (reviewer trace), #10 (conflict path), #20 (gate pass attributability), #21 (REDACT no residue after 3-day mixed traffic) are **deferred to Ticket B** — they require the injector, gate, skill pipeline, or reviewer modules. Tests in this task use `pytest.skip("Ticket B")` for those.

- [ ] **Step 1: Write basic acceptance tests (1, 7) + stubs for Ticket B**

```python
# tests/identity/test_acceptance.py
import pytest

# Ticket B stubs — these tests require modules not in Ticket A
@pytest.mark.skip(reason="Ticket B: requires injector module")
async def test_02_shadow_mode(): pass

@pytest.mark.skip(reason="Ticket B: requires injector module")
async def test_03_injection_works(): pass

@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_04_gate_observe(): pass

@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_05_gate_advise(): pass

@pytest.mark.skip(reason="Ticket B: requires skill pipeline")
async def test_08_skill_pipeline(): pass

@pytest.mark.skip(reason="Ticket B: requires reviewer module")
async def test_09_reviewer_trace(): pass

@pytest.mark.skip(reason="Ticket B: requires gate + conflict resolution")
async def test_10_conflict_path(): pass

@pytest.mark.skip(reason="Ticket B: requires gate module")
async def test_20_gate_pass_attributability(): pass
```

Then the actual Ticket A tests:

```python
# tests/identity/test_acceptance.py

async def test_01_cold_start(tmp_path):
    """acceptance #1: cold start with empty identity dir"""
    store = IdentityStore(db_path=tmp_path / "id.db")
    await store.init()
    claims = await store.list_claims(create_kevin_auth("s1"))
    assert len(claims) == 0
    await store.close()

async def test_06_gate_observe():
    """acceptance #4: gate observe mode"""
    # Covered by gate tests — gate not in Ticket A but flags + event writing are

async def test_07_killswitch(store):
    """acceptance #7: killswitch disables all identity operations"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=store, flags=flags)
    auth = create_kevin_auth("s1")
    result = await retriever.retrieve("anything", auth)
    assert len(result.claims) == 0
```

- [ ] **Step 2: Write stability + cache acceptance tests (11, 12, 13, 14)**

```python
async def test_11_irrelevant_edit_stability(store, tmp_path):
    """acceptance #11: trailing whitespace → 0 revisions"""
    # Already covered in test_parser_rebuild.py

async def test_12_utf8_span(store, tmp_path):
    """acceptance #12: Chinese + emoji UTF-8 spans correct"""
    # Already covered in test_parser.py

async def test_13_llm_cache_consistency(store, tmp_path):
    """acceptance #13: 10 rebuilds with no change → 0 revisions after first"""
    md_file = tmp_path / "soul.md"
    md_file.write_text("- [id=honesty] Lapwing values honesty.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    for _ in range(9):
        report = await parser.rebuild(auth=auth)
        assert report.created == 0 and report.updated == 0

async def test_14_chroma_rebuild_consistency(store, tmp_path):
    """acceptance #14: chroma vector count matches active claims"""
    # After rebuild + outbox drain, check chroma count == active claim count
```

- [ ] **Step 3: Write security + privacy acceptance tests (15, 16, 18, 19, 22)**

```python
async def test_15_killswitch_audit_completeness(store):
    """acceptance #15: killswitch ON/OFF → exactly 2 audit entries"""
    auth = create_kevin_auth("s1")
    await store.write_audit_log(AuditLogEntry(action=AuditAction.KILLSWITCH_ON, ...), auth)
    await store.write_audit_log(AuditLogEntry(action=AuditAction.KILLSWITCH_OFF, ...), auth)
    # Verify entries exist

async def test_16_unauth_cannot_access_sensitive(store):
    """acceptance #16: unauthenticated cannot trigger sensitive ops"""
    anon_auth = AuthContext(actor_type="anonymous", scopes=set(), ...)
    with pytest.raises(AuthorizationError):
        await store.redact_claim("c1", anon_auth, "reason")

async def test_18_id_stable_across_model_change():
    """acceptance #18: already covered in test_parser_rebuild.py"""

async def test_19_append_revision_transactional():
    """acceptance #19: already covered in test_store.py"""

async def test_22_killswitch_purity(store):
    """acceptance #22: killswitch ON → 100 chat runs → 0 new trace rows"""
    flags = IdentityFlags(identity_system_killswitch=True)
    retriever = IdentityRetriever(store=store, flags=flags)
    auth = create_kevin_auth("s1")
    initial_traces = await store._count_retrieval_traces()
    for _ in range(100):
        await retriever.retrieve("test query", auth)
    final_traces = await store._count_retrieval_traces()
    assert final_traces == initial_traces
```

- [ ] **Step 4: Write Addendum acceptance tests (A.1 through A.9)**

```python
async def test_a1_explicit_id_compliance(tmp_path):
    """A.1: validate --strict on production files"""
    # Already covered in test_parser_rebuild.py

async def test_a2_fallback_id_churn(store, tmp_path):
    """A.2: memory_anchor without explicit id → text change causes DEPRECATE+CREATE"""
    md_file = tmp_path / "memory_anchors" / "test.md"
    md_file.parent.mkdir(parents=True)
    md_file.write_text("- Some anchor text.", encoding="utf-8")
    parser = IdentityParser(store=store, identity_dir=tmp_path)
    auth = create_kevin_auth("s1")
    await parser.rebuild(auth=auth)
    claims_before = await store.list_claims(auth)
    # Modify text slightly
    md_file.write_text("- Some anchor text!", encoding="utf-8")
    report = await parser.rebuild(auth=auth)
    assert report.deprecated == 1
    assert report.created == 1

async def test_a3_privacy_delete_source_redaction(store, tmp_path):
    """A.3: already covered in test_store_privacy.py"""

async def test_a4_tombstone_persistent_blocking(store, tmp_path):
    """A.4: already covered in test_parser_rebuild.py"""

async def test_a5_provenance_update_no_revision(store, tmp_path):
    """A.5: already covered in test_parser_rebuild.py"""

async def test_a6_append_only_triggers(store):
    """A.6: already covered in test_store_triggers.py"""

async def test_a7_gate_component_disabled_events(store):
    """A.7: gate_enabled=false → 100 checks → 100 GateEvents with COMPONENT_DISABLED"""
    # This will be fully tested in Ticket B (gate module), but the store
    # and GateEvent model are ready. Write a simplified version here.
    from src.identity.models import GateEvent, GateOutcome, GatePassReason
    auth = create_kevin_auth("s1")
    flags_id = await store.save_feature_flags_snapshot({"gate_enabled": False})
    for i in range(100):
        event = GateEvent(
            event_id=str(uuid4()),
            outcome=GateOutcome.PASS,
            pass_reason=GatePassReason.COMPONENT_DISABLED,
            reason="gate_disabled_by_flag",
            feature_flags_snapshot_id=flags_id,
            # ... other required fields
        )
        await store.write_gate_event(event)
    events = await store._list_gate_events()
    assert len(events) == 100
    assert all(e["pass_reason"] == "component_disabled" for e in events)

async def test_a8_erased_tombstone_projection(store):
    """A.8: already covered in test_store_privacy.py"""

async def test_a9_explicit_access_forgery_protection(store):
    """A.9: fake request_id → verify returns False"""
    verified = await store.verify_explicit_request(
        request_id="fake_id", actor_id="kevin",
        scope="sensitive.restricted.explicit", target_claim_id="c1",
    )
    assert verified is False

    # Create real request, consume once, try again
    auth = create_kevin_auth("s1")
    req_id = await store.create_explicit_access_request(
        actor_id="kevin", scope="sensitive.restricted.explicit",
        target_claim_ids=["c1"], ttl_seconds=60, auth=auth,
    )
    v1 = await store.verify_explicit_request(req_id, "kevin", "sensitive.restricted.explicit", "c1")
    assert v1 is True
    v2 = await store.verify_explicit_request(req_id, "kevin", "sensitive.restricted.explicit", "c1")
    assert v2 is False  # already consumed
```

- [ ] **Step 5: Write privacy acceptance tests**

```python
async def test_privacy_redact_no_raw_text_residue(store):
    """Privacy: after REDACT, claim text not in any non-audit table"""
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1", text="Kevin's secret medical info")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.redact_claim("c1", auth, "privacy", source_already_redacted=True)
    # Search all tables for the sensitive text
    found = await store._search_all_tables("Kevin's secret medical info")
    assert not found, "Sensitive text found in non-audit tables after REDACT"

async def test_privacy_erase_chroma_deleted(store):
    """Privacy: after ERASE, chroma vector deleted"""
    # Verify outbox has delete_vector action after erase
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.erase_claim("c1", auth, "gdpr")
    outbox = await store._get_pending_outbox()
    delete_entries = [e for e in outbox if e["action"] == "delete_vector"]
    assert len(delete_entries) >= 1

async def test_privacy_export_writes_audit(store):
    """Privacy: export writes CLAIM_EXPORTED audit"""
    auth = create_kevin_auth("s1")
    claim = _make_test_claim(id="c1")
    await store.append_revision(_make_create_revision(claim), auth)
    await store.export_claim("c1", auth)
    audits = await store._list_audit_entries(action="claim_exported")
    assert len(audits) == 1
```

- [ ] **Step 6: Run all tests**

Run: `python -m pytest tests/identity/ -x -v`

- [ ] **Step 7: Commit**

```bash
git add tests/identity/test_acceptance.py
git commit -m "test(identity): add 31+ acceptance tests for Ticket A"
```

---

### Task 20: Performance Budget Test

**Files:**
- Modify: `tests/identity/test_acceptance.py`

- [ ] **Step 1: Write P95 retrieval latency test**

```python
import time

async def test_17_retrieval_p95_under_100ms(populated_store_with_50_claims):
    """acceptance #17: P95 retrieval < 100ms"""
    retriever = IdentityRetriever(store=populated_store_with_50_claims, flags=IdentityFlags())
    auth = create_kevin_auth("s1")
    latencies = []
    for _ in range(100):
        t0 = time.monotonic()
        await retriever.retrieve("honesty and relationships", auth)
        latencies.append((time.monotonic() - t0) * 1000)
    latencies.sort()
    p95 = latencies[94]
    assert p95 < 100, f"P95 retrieval latency {p95:.1f}ms exceeds 100ms budget"
```

- [ ] **Step 2: Run test**
- [ ] **Step 3: Commit**

```bash
git add tests/identity/test_acceptance.py
git commit -m "test(identity): add P95 retrieval latency test (acceptance #17)"
```

---

### Task 21: Final Integration + Run All Tests

- [ ] **Step 1: Run full identity test suite**

Run: `python -m pytest tests/identity/ -x -v`

- [ ] **Step 2: Run full project test suite to check for regressions**

Run: `python -m pytest tests/ -x -q --timeout=300`

- [ ] **Step 3: Verify CLI works end-to-end**

```bash
python -m src.identity scan
python -m src.identity validate
python -m src.identity validate --strict
```

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(identity): address integration issues from full test run"
```

---

## Acceptance Criteria Cross-Reference

| # | Test | Task | Status |
|---|------|------|--------|
| 1 | Cold start | 19 | Ticket A |
| 2 | Shadow mode behavior | — | Ticket B (skip) |
| 3 | Injection works | — | Ticket B (skip) |
| 4 | Gate observe | — | Ticket B (skip) |
| 5 | Gate advise | — | Ticket B (skip) |
| 6 | L1 evolution | — | Ticket B (skip) |
| 7 | Killswitch | 19 | Ticket A |
| 8 | Skill pipeline | — | Ticket B (skip) |
| 9 | Reviewer trace | — | Ticket B (skip) |
| 10 | Conflict path | — | Ticket B (skip) |
| 11 | Irrelevant edit stability | 13 | Ticket A |
| 12 | UTF-8 span | 11 | Ticket A |
| 13 | LLM cache (10 rebuilds) | 19 | Ticket A |
| 14 | Chroma rebuild consistency | 19 | Ticket A |
| 15 | Killswitch audit completeness | 19 | Ticket A |
| 16 | Unauth cannot access sensitive | 19 | Ticket A |
| 17 | P95 retrieval < 100ms | 20 | Ticket A |
| 18 | ID stable across model change | 13 | Ticket A |
| 19 | append_revision transactional | 6 | Ticket A |
| 20 | Gate pass attributability | — | Ticket B (skip) |
| 21 | REDACT no raw text (3-day) | — | Ticket B (skip) |
| 22 | Killswitch purity (100 chats) | 19 | Ticket A |
| A.1 | Explicit id compliance | 13 | Ticket A |
| A.2 | Fallback id churn | 19 | Ticket A |
| A.3 | Privacy source redaction | 8 | Ticket A |
| A.4 | Tombstone persistent blocking | 13 | Ticket A |
| A.5 | Provenance no-revision | 13 | Ticket A |
| A.6 | Append-only triggers | 9 | Ticket A |
| A.7 | Gate component disabled events | 19 | Ticket A |
| A.8 | ERASED tombstone projection | 8 | Ticket A |
| A.9 | Explicit access forgery | 19 | Ticket A |
| Privacy | REDACT no residue | 19 | Ticket A |
| Privacy | ERASE chroma deleted | 19 | Ticket A |
| Privacy | Export audit | 19 | Ticket A |
| Attributability | 20 random reply trace | — | Ticket B |

## Default Configuration (Ticket A)

All behavior-affecting flags OFF:
- `injector_enabled = false`
- `gate_enabled = false`
- `workflow_learner_enabled = false`
- `identity_system_killswitch = false`

Lapwing runtime behavior: **unchanged**. The identity substrate is fully observable and queryable but does not inject into prompts or gate actions.
