# Subjecthood Batch 1 Reports

## Dispatcher Four-Path Root Cause

- Path A, delegate/start boundary: `src/tools/agent_tools.py` passes the current services bundle into `TaskSupervisor.start_agent_task()`, and `TaskSupervisor._spawn_runtime()` copies it into child services. Current behavior was mostly intact; the fix preserves this and relies on the same bundle carrying `tool_dispatcher`.
- Path B, registry cache hit: `AgentRegistry.get_or_create_instance(..., services_override=...)` refreshes cached legacy agents with the latest services bundle and factory-created agents receive that bundle. This was not the primary root cause, but the required service set now includes `tool_dispatcher` so stale bundles fail before runtime.
- Path C, ToolExecutionContext construction: `TaskRuntime.create_agent_context()` previously returned `services={}`. This was a confirmed propagation hole; it now includes `tool_registry` and `tool_dispatcher`.
- Path D, runtime liveness boundary: tool dispatch previously did not assert dispatcher/tool-registry liveness at the actual call boundary. `ToolDispatcher.dispatch()` now checks `tool_dispatcher` and `tool_registry` before policy/tool execution and returns `tool_infra_unavailable` for infra loss.

## Outbound Callsite Inventory

- Foreground final/interim replies: `MainLoop._wrap_user_reply_send_fn()` now sends `direct_reply` through `ExpressionGate`.
- Framework/system messages: `send_system_message()` now routes through `ExpressionGate` when enabled and preserves legacy behavior behind the flag.
- Background completion/failure: `MainLoop._deliver_agent_status_event()` labels completion/failure sources and passes task lineage metadata for suppression.
- Agent needs input: the same delivery point hard-rejects raw `agent_needs_input` through the gate audit path instead of rendering it.
- Proactive `send_message`: `src/tools/personal_tools.py` now routes delivered proactive text through `ExpressionGate` and respects the infra breaker before sending.
- Reminder/scheduler sends: existing `DurableScheduler` calls continue through `send_system_message()`, so they inherit the gate.
- Legacy `tell_user` bypass: there is no live model-facing `tell_user` tool registration; `MutationType.TELL_USER` remains an audit event and all current framework equivalents go through the gate.

## UserVisibleOutboundLog Landing

`TrajectoryStore` is reused as the outbound log projection. It already has chat id, timestamp, entry type/source, focus id, and metadata JSON. Gate-written entries now standardize `text`, `source`, `delivered`, `text_hash`, and metadata. No new table is added.
