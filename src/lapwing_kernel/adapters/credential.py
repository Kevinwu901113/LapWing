"""CredentialAdapter — Resource adapter for credential side-effects.

Boundary with CredentialVault (blueprint §6 / §7):
  - CredentialVault = fact store (encrypted at-rest secret storage)
  - CredentialAdapter = Resource (side-effecting use of secrets)

Boundary with other adapters (blueprint §7.1, GPT final-pass):
  - No adapter directly imports another adapter.
  - Cross-resource coordination goes through:
      (a) the Kernel action pipeline (re-issue Action(...))
      (b) the in-process CredentialLeaseStore (handle, NOT secret in args)

LLM-visible surface (blueprint §7.3 / §7.6):
  list_count      → content="count=N"               (no service names)
  exists(service) → content="exists" | "missing"
  use(service)    → Observation.artifacts=[lease_ref]; NO plaintext anywhere
                    LLM-facing. Consumer (BrowserAdapter._login_with_lease)
                    pulls secret from CredentialLeaseStore in process.
  create          → blocked_by_policy (Kevin uses CLI to manage vault)

PolicyDecider (blueprint §4.4) gates credential.use to INTERRUPT first-use,
ALLOW after CredentialUseState.has_been_used(service) returns True. The
adapter does NOT re-check policy — by the time execute() runs, ALLOW has
already been decided.

See docs/architecture/lapwing_v1_blueprint.md §7.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, ClassVar

from src.lapwing_kernel.primitives.action import Action
from src.lapwing_kernel.primitives.observation import Observation
from src.lapwing_kernel.redactor import SecretRedactor

from .credential_lease_store import CredentialLeaseStore
from .credential_use_state import CredentialUseState

logger = logging.getLogger(__name__)


def _new_id() -> str:
    return str(uuid.uuid4())


class CredentialAdapter:
    """Resource adapter. name='credential'. Conforms to Resource Protocol."""

    name: ClassVar[str] = "credential"
    SUPPORTED_VERBS: ClassVar[frozenset[str]] = frozenset(
        {"list_count", "exists", "use", "create"}
    )

    def __init__(
        self,
        *,
        vault: Any,
        use_state: CredentialUseState,
        redactor: SecretRedactor | None = None,
    ):
        self._vault = vault
        self._use_state = use_state
        self._redactor = redactor or SecretRedactor()

    def supports(self, verb: str) -> bool:
        return verb in self.SUPPORTED_VERBS

    async def execute(self, action: Action) -> Observation:
        verb = action.verb

        if verb == "list_count":
            services = self._vault.list_services()
            n = len(services)
            return Observation.ok(
                action.id,
                "credential",
                summary=f"{n} credential services configured",
                content=f"count={n}",
                provenance={"count": n},
            )

        if verb == "exists":
            service = action.args.get("service", "")
            present = self._vault.get(service) is not None
            return Observation.ok(
                action.id,
                "credential",
                summary=f"credential for {service}: {'exists' if present else 'missing'}",
                content="exists" if present else "missing",
                provenance={"service": service, "present": present},
            )

        if verb == "use":
            service = action.args.get("service")
            purpose = action.args.get("purpose", "browser_login")
            if not service:
                return Observation.failure(
                    action.id,
                    "credential",
                    status="failed",
                    error="missing_service",
                )
            cred = self._vault.get(service)
            if cred is None:
                return Observation(
                    id=_new_id(),
                    action_id=action.id,
                    resource="credential",
                    status="missing",
                    summary=f"no credential for {service}",
                    provenance={"service": service},
                )
            # PolicyDecider has already ALLOW'd if we got here. Issue lease.
            lease = await CredentialLeaseStore.instance().create(
                service=service,
                secret=cred,
                purpose=purpose,
            )
            # Mark approved on first successful use so subsequent uses skip
            # the INTERRUPT branch in PolicyDecider.
            self._use_state.mark_used(service)
            return Observation.ok(
                action.id,
                "credential",
                summary=f"credential lease issued for {service}",
                # IMPORTANT: content must NOT contain the plaintext.
                content="credential available",
                artifacts=[
                    {
                        "type": "credential_lease_ref",
                        "ref": lease.id,
                        "service": service,
                        "purpose": purpose,
                        "expires_at": lease.expires_at.isoformat(),
                    }
                ],
                provenance={"service": service, "purpose": purpose},
            )

        if verb == "create":
            # LLM must NEVER autonomously create credentials. Kevin uses
            # `python -m src.cli.credential add ...` (or equivalent) to
            # populate the vault.
            return Observation.failure(
                action.id,
                "credential",
                status="blocked_by_policy",
                error="credential.create is owner-only via CLI",
                summary="credential.create is owner-only via CLI",
            )

        return Observation.failure(
            action.id,
            "credential",
            status="failed",
            error=f"unsupported_verb:{verb}",
        )
