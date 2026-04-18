# Step 3 — Identity boundary memo

**DECISION BY CLAUDE CODE, PENDING REVIEW**

Date: 2026-04-18
Branch: `refactor/step3-state-serializer`
Scope: What files qualify as "identity" for `StateView.identity_docs`.

## Candidates

Four markdown files in the project are plausibly "identity":

| File | Location | Volatility | What it is |
| --- | --- | --- | --- |
| `soul.md` | `data/identity/` | Immutable by Lapwing; Kevin-edited | Core personhood — who she is, her relationships, her way of being |
| `constitution.md` | `data/identity/` | Immutable by Lapwing; Kevin-edited; guarded by ConstitutionGuard | Hard rules she cannot violate |
| `voice.md` | `prompts/lapwing_voice.md` | Kevin-edited via prompt reload | How she speaks — sentence shape, formatting bans |
| `rules.md` | `data/evolution/` | **Lapwing appends via diffs** | Evolved behavioural rules she's learned over time |
| `interests.md` | `data/evolution/` | **Lapwing appends via diffs** | Evolved interests / curiosity surface |

The first three are Kevin-authored and rarely change. The evolution pair
is Lapwing's own growth record — diff-appended each time she updates
herself.

## Semantic test: "if this changes, who is she?"

- `soul.md` flips → she is someone else. Identity.
- `constitution.md` flips → her boundaries change. Identity.
- `voice.md` flips → how she communicates changes, but her interior
  is the same person. Voice is the audible fingerprint of identity,
  not identity itself. *Gray area.*
- `rules.md` flips → she has learned something new; still the same
  her. Not identity.
- `interests.md` flips → her tastes shift; still the same her. Not
  identity.

`voice.md` sits right at the boundary. Keeping it in `IdentityDocs` is
justified by coupling: every system-prompt render has always included
it (via `inject_voice_reminder`), and separating it would double the
number of files callers must fetch without changing the prompt that
actually reaches the model.

## Decision

`IdentityDocs` = `{soul, constitution, voice}`.

`rules.md` and `interests.md` **do not** enter `StateView` in Step 3.

Rationale:

1. **Parity with pre-Step-3.** The current `PromptBuilder` loads exactly
   `soul + constitution + voice`. Adding `rules.md` / `interests.md`
   would be a prompt-content change smuggled into a refactor, and the
   M1 parity smoke would fail loudly and correctly. Step 3 is a
   structural rewrite, not a content change.

2. **Evolution layer is not implemented yet.** Although the repo has
   `data/evolution/rules.md` + `interests.md` scaffolding, the writer
   side (diff-accumulation pipeline) is not the prompt consumer's
   responsibility. When the reader side lands (Blueprint v2.0 §
   Evolution), it gets its own `evolution_state` StateView field.

3. **Kind separation matters.** Stable Kevin-authored identity and
   evolving self-authored rules have different provenance guards
   (ConstitutionGuard vs. no guard) and different staleness profiles.
   Mixing them under `identity_docs` would pretend they're the same
   kind of thing.

## Rollback path

If the model begins drifting on evolved rules (observable in the
Step 5 Commitment Reviewer or in Kevin's spot checks), extend
`StateView` with:

```python
@dataclass(frozen=True, slots=True)
class EvolutionState:
    rules: str
    interests: str
```

and add a sixth `StateView` field `evolution_state`. The serializer
renders it as a new layer between runtime state and memory snippets.
The change is purely additive and does not break any existing prompt
output. Builder reads the two `data/evolution/*.md` files; if either
is empty, the layer is skipped.

No schema migration, no data migration, no call-site changes elsewhere.

## Why this memo exists

The instruction allows me to decide judgment calls of this kind
unilaterally. I'm documenting it in memo form so (a) Kevin can see the
decision, (b) the rollback path is explicit, and (c) future Claude
sessions auditing Step 3 output know why `evolution/` content is
absent from the prompt.
