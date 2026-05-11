"""Kernel resource adapters.

Adapters implement the Resource Protocol from src.lapwing_kernel.primitives.resource
and are registered with the kernel's ResourceRegistry. Each adapter wraps an
underlying side-effecting subsystem (browser, credential vault, ...) but
exposes only the Action / Observation contract.

HARD CONSTRAINT (blueprint §7.1 / §17.1 / §15.3 #11):
  No adapter directly imports or calls another adapter. Cross-resource
  coordination goes either through the Kernel action pipeline (re-issue an
  Action) or via an in-process handle store (e.g. CredentialLeaseStore from
  PR-07).

NOTE: distinct from src/adapters/ which is the LEGACY namespace for IO
adapters (QQ, Desktop). Kernel resource adapters live HERE under the
lapwing_kernel package per blueprint §2.1.

See docs/architecture/lapwing_v1_blueprint.md §6-§7.
"""
