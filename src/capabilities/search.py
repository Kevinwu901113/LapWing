"""Pure-function search, filter, sort, and deduplication helpers.

Operates on lists of CapabilityManifest. Stateless — no I/O, no database.
"""

from __future__ import annotations

from .schema import CapabilityManifest, CapabilityMaturity, CapabilityScope, CapabilityStatus, CapabilityType

SCOPE_PRECEDENCE: list[CapabilityScope] = [
    CapabilityScope.SESSION,
    CapabilityScope.WORKSPACE,
    CapabilityScope.USER,
    CapabilityScope.GLOBAL,
]

_TRUST_ORDER: dict[str, int] = {
    "guest": 0,
    "developer": 1,
    "trusted": 2,
    "admin": 3,
}


def _scope_rank(scope: CapabilityScope) -> int:
    try:
        return SCOPE_PRECEDENCE.index(scope)
    except ValueError:
        return len(SCOPE_PRECEDENCE)


def filter_active(manifests: list[CapabilityManifest]) -> list[CapabilityManifest]:
    return [m for m in manifests if m.status == CapabilityStatus.ACTIVE]


def filter_by_tags(
    manifests: list[CapabilityManifest],
    tags: list[str],
    *,
    match_all: bool = False,
) -> list[CapabilityManifest]:
    if not tags:
        return list(manifests)
    tag_set = {t.lower() for t in tags}
    result = []
    for m in manifests:
        m_tags = {t.lower() for t in m.tags}
        if match_all:
            if tag_set <= m_tags:
                result.append(m)
        else:
            if tag_set & m_tags:
                result.append(m)
    return result


def filter_by_type(
    manifests: list[CapabilityManifest],
    cap_types: list[CapabilityType],
) -> list[CapabilityManifest]:
    if not cap_types:
        return list(manifests)
    allowed = set(cap_types)
    return [m for m in manifests if m.type in allowed]


def filter_by_scope(
    manifests: list[CapabilityManifest],
    scopes: list[CapabilityScope],
) -> list[CapabilityManifest]:
    if not scopes:
        return list(manifests)
    allowed = set(scopes)
    return [m for m in manifests if m.scope in allowed]


def filter_stable(manifests: list[CapabilityManifest]) -> list[CapabilityManifest]:
    return [m for m in manifests if m.maturity == CapabilityMaturity.STABLE and m.status == CapabilityStatus.ACTIVE]


def filter_trust_level(
    manifests: list[CapabilityManifest],
    max_trust: str,
) -> list[CapabilityManifest]:
    max_level = _TRUST_ORDER.get(max_trust.lower(), 0)
    return [m for m in manifests if _TRUST_ORDER.get(m.trust_required.lower(), 0) <= max_level]


def text_search(
    manifests: list[CapabilityManifest],
    query: str,
    *,
    fields: tuple[str, ...] = ("name", "description"),
) -> list[CapabilityManifest]:
    if not query.strip():
        return list(manifests)
    q = query.lower()
    return [m for m in manifests if any(q in getattr(m, f, "").lower() for f in fields)]


def deduplicate_by_precedence(
    manifests: list[CapabilityManifest],
) -> list[CapabilityManifest]:
    by_id: dict[str, list[CapabilityManifest]] = {}
    for m in manifests:
        by_id.setdefault(m.id, []).append(m)
    result = []
    for cap_id, entries in by_id.items():
        entries.sort(key=lambda m: _scope_rank(m.scope))
        result.append(entries[0])
    return result


def resolve_by_scope(
    manifests: list[CapabilityManifest],
    scope: CapabilityScope,
) -> list[CapabilityManifest]:
    return [m for m in manifests if m.scope == scope]


def sort_by_name(manifests: list[CapabilityManifest]) -> list[CapabilityManifest]:
    return sorted(manifests, key=lambda m: m.name.lower())


def sort_by_maturity(manifests: list[CapabilityManifest]) -> list[CapabilityManifest]:
    _ORDER = {
        CapabilityMaturity.STABLE: 0,
        CapabilityMaturity.TESTING: 1,
        CapabilityMaturity.DRAFT: 2,
        CapabilityMaturity.REPAIRING: 3,
        CapabilityMaturity.BROKEN: 4,
    }
    return sorted(manifests, key=lambda m: (_ORDER.get(m.maturity, 99), m.name.lower()))


def sort_by_updated(manifests: list[CapabilityManifest]) -> list[CapabilityManifest]:
    return sorted(manifests, key=lambda m: m.updated_at.isoformat() if m.updated_at else "", reverse=True)
