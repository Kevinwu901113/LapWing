# Context Governance

## Reversible Records

Long-term context writes use reversible proposal records, not final summaries.
This applies to memory, identity, relationship, commitment, preference,
project fact, research result, and curator output records.

Shared fields live in `ReversibleContextRecord`:

- stable id and record type
- content plus source handles
- creator, confidence, sensitivity, and status
- why it matters and user intent evidence
- emotional or relational context when relevant
- decision boundary and reversibility handle
- approval state, expiry/review timestamps, and supersession links

Safe audit payloads exclude raw content.

## Memory Wiki Gate

Memory Wiki published writes remain gated. Summaries and curator outputs should
first create draft, pending, or proposal records. Identity, relationship,
preference, long-term commitment, and boundary records require the stronger
approval gate. `memory_wiki_write_enabled` remains false by default unless an
operator explicitly overrides it.

## Frozen And Dynamic Context

Frozen snapshots are for stable long-term context only: constitution, published
profile/wiki facts, and stable identity or project facts.

Do not freeze trajectory, working set, task state, fresh correction, approval
state, pending steering, active commitments, or capability retrieval summaries.
Those belong in dynamic StateView sections.

## Identity Injection

Identity facts are not injected merely because they exist. Relevance and policy
must allow the fact for the current task, channel, and profile. Sensitive
identity or relationship memory requires explicit relevance.
