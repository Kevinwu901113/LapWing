# Testing Guidelines

Tests are contracts, not snapshots.

Prefer assertions about stable behavior:

- invalid tool arguments return structured validation errors
- validation failures do not execute tools
- error codes come from the controlled enum
- EventQueue remains the only MainLoop/Brain input
- IntentRouter sees only normal chat
- disabled, broken, quarantined, and environment-mismatch capabilities are not injected
- stable capability promotion requires eval evidence and policy approval
- memory and identity writes remain draft/proposal/pending before publication

Avoid assertions about volatile details:

- exact model names
- catalog lengths
- timestamps
- raw serialized prompts
- unstable ordering unless deterministic ordering is the contract
- full local file paths or raw error text

When local operator overrides in `config.toml` are intentionally dirty, default
flag tests should either instantiate config models directly or run with env
overrides that express the default contract. Do not reset local operator config
just to satisfy tests.
