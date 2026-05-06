# Adapter Contract

Adapters are transport boundaries, not runtime drivers.

## Responsibilities

- Normalize raw payloads with `normalize_inbound()`.
- Declare `AdapterCapabilities`.
- Send outbound messages using channel-specific APIs.
- Never call `Brain` directly.

## Pipeline

```
ChannelAdapter.normalize_inbound()
  -> InboundMessageGate
  -> CommandInterceptLayer
  -> BusySessionController
  -> EventQueue
  -> MainLoop
  -> Brain
```

Command and approval messages are intercepted before normal chat. Slash
commands must not enter the normal chat path unless a command explicitly
converts them to a `MessageEvent`.

## Capability Matrix

`AdapterCapabilities` records private/group send support, typing indicators,
rich media, voice, edit support, and reply references.

Startup validation checks the required routes for configured channel behavior.
Strict mode raises `StartupError`; non-strict mode logs and disables unsupported
routes. Unsupported operations return structured channel errors rather than
latent attribute failures.

QQ declares `can_send_private=true` and `can_send_group=true`.
