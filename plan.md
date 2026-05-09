# Lapwing Subjecthood Failure Batch 1 Plan

## Summary
Implement only the P0 dispatcher/tool infra repair and P1 self-projection foundation. The first execution step will publish three repo-grounded reports into the PR description/work log before the relevant edits land: four-path dispatcher root cause, outbound callsite inventory, and UserVisibleOutboundLog storage decision. No P2-P5 scope is included.

## Current Findings Locked In
- Four-path dispatcher audit:
  - Path A is mostly intact: `delegate_to_agent` passes services into `TaskSupervisor.start_agent_task()` and `_spawn_runtime()` preserves them.
  - Path B is mostly intact: `AgentRegistry` refreshes cached legacy agents with `services_override`, but tests must prove the refreshed bundle contains the real tool dispatcher, not only a present object.
  - Path C is a confirmed propagation hole: `TaskRuntime.create_agent_context()` currently returns `ToolExecutionContext(..., services={})`.
  - Path D is missing: actual tool dispatch does not perform a runtime liveness assertion before tool execution.
- Naming mismatch is likely central: current `services["dispatcher"]` is the pub/sub `Dispatcher`, while actual tool calls require the `ToolDispatcher`. The repair will explicitly separate `dispatcher` as event pub/sub from `tool_dispatcher` as the tool-call organ.
- UserVisibleOutboundLog should reuse `TrajectoryStore` as the projection source, not add a new table, because it already stores chat id, timestamp, source-ish entry type, focus id, metadata JSON, and replayable assistant-visible entries.

## Key Changes
- Add explicit infra liveness checks at the actual tool-call boundary. `tool_dispatcher` and `tool_registry` failures become `tool_infra_unavailable`; policy/profile denials remain `tool_forbidden`.
- Add a minimal `InfraCircuitBreaker` for `dispatcher/tool_dispatcher` and `tool_registry`: open on infra failure, 60s/120s/300s cooldown, one half-open probe, close after 3 consecutive runtime liveness successes. Delegation and volatile proactive external-info work fail fast while open.
- Stop dispatcher-missing retry cascades. The first infra failure in a foreground turn emits one honest `framework_fallback` message through the gate, stops the tool loop, and waits for the next user input.
- Introduce `ExpressionGate` with the requested source classes. `direct_reply` is the single authoritative foreground send path, has no suppression, and fails open to raw channel send with best-effort audit/logging.
- Route non-direct user-visible output through `ExpressionGate`: framework/system sends, background completion/failure, proactive sends, reminders, confirmations, and any remaining TELL_USER-equivalent path.
- Hard reject internal-only sources, including sub-agent direct outbound attempts, raw `AGENT_NEEDS_INPUT`/`AGENTNEEDSINPUT`, raw infra errors, debug/internal state. Rejections write mutation-log audit records.
- Standardize delivered outbound logging in `TrajectoryStore` with `text`, `source`, `delivered`, `text_hash`, and metadata. Context replay will include delivered user-visible outbound entries after persona/state injection and before the current user message.
- Add self-projection context injection: latest 20 entries, 8k char cap, strong retention for entries after the previous foreground user message, older entry truncation to metadata plus first 300 chars, guarded by `self_projection.outbound_context_injection_enabled`.
- Add deterministic intent/topic lineage for new tasks: nullable legacy-compatible `intent_key`, `topic_key`, and `generation`; new volatile external-info tasks must populate them. Weather topic keys use simple string normalization only, for example `weather:guangzhou-university-city`.
- Add stopped-topic markers per `(chat_id, topic_key)`. Same-topic completions/failures with `generation <= stopped_at_generation` are suppressed; a later generation is allowed.
- Internalize `needs_input`: infra/config/permission payloads become `tool_infra_unavailable` and breaker/fallback; true missing business input can become a natural `direct_reply`, never a raw enum/token.

## Flags
Add production-default-on, independently reversible flags:
- `expression_gate.enabled`
- `expression_gate.direct_reply_through_gate`
- `expression_gate.fail_open_direct_reply`
- `self_projection.outbound_context_injection_enabled`
- `intent_cancellation.enabled`
- `infra_breaker.enabled`

Each flag must restore its own legacy behavior without requiring another flag to change.

## Tests And Acceptance
- Add focused tests for: service propagation across A/B/C, tool-call liveness at D, taxonomy split, breaker open/half-open/close, fail-fast no cascade, direct-reply fail-open, internal-only hard reject, duplicate infra dedupe key, cancelled/stale suppression, and raw token blacklist.
- Add the deterministic 8-step incident replay with dispatcher fault injection. Expected: at most one `framework_fallback`, zero researcher/coder cascade, no “still checking” claim after failure, no backend failure flood, no raw `AGENTNEEDSINPUT`, late same-topic completion suppressed, and the final question can reference prior outbound text.
- Add lineage test: 3 sibling same-generation weather tasks suppressed after stop; new generation not suppressed.
- Add one end-to-end flag-off test per new flag.
- Run focused regression suites around concurrent background work, main loop, tool dispatch, agent dispatcher behavior, proactive trajectory, and brain history replay.
- Run the full suite with the known default-off capability env matrix and `PYTHONPATH=.`.
- Final acceptance requires a real 30-minute supervisor soak with real service/LLM/channel, including exact startup command, duration, outbound counts by source, active/cancelled task counts, suppressed count, breaker transitions, and gate fail-open count.

## Assumptions
- `dispatcher` in the blueprint maps to the tool-call organ, but the codebase currently has a pub/sub object with the same name; implementation will preserve pub/sub as `dispatcher` and add/use `tool_dispatcher` for tool-call liveness.
- `TrajectoryStore` is the chosen UserVisibleOutboundLog backend unless implementation uncovers an unfixable delivered/update/metadata gap.
- For `direct_reply` fail-open, delivery wins if the gate is broken. Tests will require `delivered=true` when stores are available, and require best-effort fail-open audit when mutation logging is available.
- No StatusAnswerContext, commitment ledger, full BodyHealth, full proactive policy rewrite, sub-agent protocol rewrite, or LLM intent canonicalization will be implemented in this batch.
