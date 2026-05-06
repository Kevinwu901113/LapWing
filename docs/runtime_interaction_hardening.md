# Runtime Interaction Hardening

## Inbound Contract

All external input must follow this path:

```
ChannelAdapter.normalize_inbound()
  -> InboundMessageGate
  -> CommandInterceptLayer
  -> BusySessionController
  -> EventQueue
  -> MainLoop
  -> Brain
```

`EventQueue` is the only `MainLoop` and `Brain` input path. Adapters may
normalize and send messages, but must not call `Brain.think*` directly.

`IntentRouter` only receives normal chat after approval, slash-command,
interrupt, queue, and steering handling. It remains two-tier:
`zero_tools` or `standard`.

## Tool Errors

Tool argument validation runs before executors. Validation failure returns a
structured `ToolExecutionResult` with:

- `status=validation_error`
- `error_code=tool.schema_validation_failed`
- `error_class=validation`
- `retryable=true`
- sanitized `safe_details`

Validation failures never execute the target tool. Dispatcher denials map to
permission, precondition, or dependency errors instead of opaque blocked
payloads.

## Busy Input

Busy input modes are `normal`, `interrupt`, `queue`, and `steer`, with
`approval` and `command` intercepted before normal routing.

Queue defaults: 20 pending inputs per chat, 30 minute TTL, and dedupe by source
message id or same normalized text within 3 seconds.

Interrupt records cancellation intent and uses existing safe MainLoop
preemption. It does not hard-kill an unsafe tool outside existing cancellation
support.

Steering is stored as a structured `SteeringEvent`. It never mutates the system
prompt and is never injected mid-tool-turn. It appears only in
`StateView.pending_steering_events` inside the dynamic runtime-state block, then
is acknowledged or expires.

## Adapter Capabilities

Adapters declare `AdapterCapabilities`. Startup validation catches missing
private/group send support before runtime. Strict mode raises `StartupError`;
non-strict mode logs warnings and disables unsupported routes. QQ declares
private and group send support explicitly.

## Feature Flags

`runtime_interaction_hardening.enabled` defaults to true because the implemented
contracts preserve existing behavior. `adapter_strict_mode` defaults false.
