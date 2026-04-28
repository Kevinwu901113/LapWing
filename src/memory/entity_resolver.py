"""EntityResolver — alias lookup + actor-role-aware pronoun resolution.

Phase 1 §1.5 of the wiki blueprint. The resolver answers:

    "When a message says 'Kevinwu' or '我', which canonical wiki entity
    are we actually talking about?"

Two parts:

1. ``aliases.yaml`` is the static registry — explicit canonical name +
   aliases. Owned by Kevin, modifiable at runtime via ``add_alias``
   (which also appends a changelog entry).
2. Pronoun resolution is *not* in the YAML. ``我`` is not a fixed
   alias for Kevin — its meaning depends on who's speaking and the
   message direction. The owner saying ``我`` means Kevin; Lapwing
   saying ``我`` (outbound) means Lapwing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger("lapwing.memory.entity_resolver")


ActorRole = Literal["owner", "lapwing", "agent", "trusted", "guest", "system"]
MessageDirection = Literal["inbound", "outbound"]

# Pronouns whose referent depends on speaker role / direction.
# Values are *not* canonical entity ids — they're symbolic targets.
_OWNER_PRONOUNS: dict[str, str] = {
    "我": "self",
    "我的": "self",
    "你": "other",
    "你的": "other",
    "I": "self",
    "me": "self",
    "my": "self",
    "you": "other",
    "your": "other",
}


class EntityResolver:
    """Resolve mentions to canonical wiki entity ids."""

    def __init__(self, aliases_path: str | Path) -> None:
        self._aliases_path = Path(aliases_path)
        self._aliases: dict[str, dict[str, Any]] = {}
        self._reverse: dict[str, str] = {}
        self.reload()

    # ── Loading ─────────────────────────────────────────────────────

    def reload(self) -> None:
        if not self._aliases_path.exists():
            self._aliases = {}
            self._reverse = {}
            return
        text = self._aliases_path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            logger.warning("[entity_resolver] yaml error: %s", exc)
            data = {}
        if not isinstance(data, dict):
            data = {}
        self._aliases = data
        self._build_reverse_index()

    def _build_reverse_index(self) -> None:
        self._reverse = {}
        for entity_id, payload in self._aliases.items():
            if not isinstance(payload, dict):
                continue
            canonical = payload.get("canonical")
            if isinstance(canonical, str) and canonical:
                self._reverse[canonical] = entity_id
                self._reverse[canonical.lower()] = entity_id
            for alias in payload.get("aliases", []) or []:
                if isinstance(alias, str) and alias:
                    self._reverse[alias] = entity_id
                    self._reverse[alias.lower()] = entity_id

    @property
    def reverse_index(self) -> dict[str, str]:
        return dict(self._reverse)

    # ── Public API ──────────────────────────────────────────────────

    def get_canonical(self, entity_id: str) -> str | None:
        payload = self._aliases.get(entity_id)
        if not isinstance(payload, dict):
            return None
        canonical = payload.get("canonical")
        return canonical if isinstance(canonical, str) else None

    def resolve(
        self,
        mention: str,
        *,
        actor_id: str | None = None,
        actor_role: ActorRole | None = None,
        channel: str | None = None,
        message_direction: MessageDirection | None = None,
    ) -> str | None:
        """Return the canonical entity id for ``mention``, or None."""
        if not mention:
            return None
        text = mention.strip()
        if not text:
            return None

        # 1. exact alias hit (case-sensitive then case-insensitive)
        if text in self._reverse:
            return self._reverse[text]
        lower = text.lower()
        if lower in self._reverse:
            return self._reverse[lower]

        # 2. pronoun resolution
        if text in _OWNER_PRONOUNS or lower in _OWNER_PRONOUNS:
            target = _OWNER_PRONOUNS.get(text) or _OWNER_PRONOUNS.get(lower)
            return self._resolve_pronoun(
                target,
                actor_role=actor_role,
                message_direction=message_direction,
            )

        return None

    def _resolve_pronoun(
        self,
        target: str | None,
        *,
        actor_role: ActorRole | None,
        message_direction: MessageDirection | None,
    ) -> str | None:
        if target not in ("self", "other"):
            return None

        # Lapwing speaking (outbound or actor_role=lapwing)
        is_lapwing_speaker = (
            actor_role == "lapwing" or message_direction == "outbound"
        )

        if actor_role == "owner" and not is_lapwing_speaker:
            return "entity.kevin" if target == "self" else "entity.lapwing"
        if is_lapwing_speaker:
            return "entity.lapwing" if target == "self" else "entity.kevin"

        # Other roles (agent / trusted / guest / system): too ambiguous to
        # bind pronouns. Leave to upstream NER if needed.
        return None

    # ── Mutation ────────────────────────────────────────────────────

    def add_alias(self, entity_id: str, alias: str, *, reason: str = "") -> None:
        """Append an alias and persist. Logs a changelog entry next to
        the aliases file (``_meta/changelog.md``)."""
        if not alias.strip():
            return
        entry = self._aliases.setdefault(entity_id, {"canonical": alias, "aliases": []})
        if not isinstance(entry, dict):
            entry = {"canonical": alias, "aliases": []}
            self._aliases[entity_id] = entry
        existing = entry.setdefault("aliases", [])
        if alias in existing:
            return
        existing.append(alias)
        self._persist()
        self._build_reverse_index()
        self._record_changelog(
            f"add alias `{alias}` to `{entity_id}`"
            + (f" — {reason}" if reason else "")
        )

    def _persist(self) -> None:
        self._aliases_path.parent.mkdir(parents=True, exist_ok=True)
        self._aliases_path.write_text(
            yaml.safe_dump(self._aliases, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def _record_changelog(self, line: str) -> None:
        log_path = self._aliases_path.parent / "changelog.md"
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"- {ts} — {line}\n")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[entity_resolver] changelog write failed: %s", exc)
