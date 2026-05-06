"""Phase 7B: Quarantined capability audit and review reporting.

Read-only, deterministic, local-only. No script execution, no Python
import from packages, no network, no LLM judge, no activation/promotion.

All functions are pure data transforms that read from quarantine storage
and write only audit/review report files.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.capabilities.errors import CapabilityError
from src.capabilities.schema import (
    CapabilityMaturity,
    CapabilityStatus,
)

if TYPE_CHECKING:
    from src.capabilities.evaluator import CapabilityEvaluator
    from src.capabilities.policy import CapabilityPolicy

# Warning codes that are inherent to quarantine and do NOT block approved_for_testing.
_QUARANTINE_INHERENT_WARNINGS: frozenset[str] = frozenset({
    "quarantined_restricted",
    "install_external_quarantine",
})
# Minor quality warnings that do not block approved_for_testing.
_MINOR_QUALITY_WARNINGS: frozenset[str] = frozenset({
    "short_description",
    "vague_description",
    "missing_triggers",
    "overbroad_trigger",
})

# ── Dangerous patterns for file content scanning ─────────────────────

_SHELL_DANGER_PATTERNS: list[tuple[str, str]] = [
    (r"rm\s+-rf\s+/", "rm -rf / (recursive root removal)"),
    (r"sudo\s+rm", "sudo rm (privileged file removal)"),
    (r"chmod\s+777", "chmod 777 (world-writable permissions)"),
    (r"curl\b.*\|.*(?:ba)?sh", "curl piped to shell"),
    (r"wget\b.*\|.*(?:ba)?sh", "wget piped to shell"),
    (r"dd\s+if=", "dd if= (raw device access)"),
    (r"\bmkfs\b", "mkfs (filesystem creation)"),
    (r":\(\)\s*\{\s*:\|\:&\s*\};:", "fork bomb"),
    (r"~\s*/\.ssh", "modifying ~/.ssh"),
    (r"/etc/(?:passwd|shadow|sudoers)", "modifying system auth files"),
    (r"/dev/(?:null|zero|random)\b.*>", "writing to /dev devices"),
    (r">\s*(?:/etc|/var|/usr|/boot|/sys)", "writing outside workspace boundaries"),
    (r"eval\s+\$", "eval with variable expansion"),
    (r"\.\./\.\./(?:/etc|/var|/usr)", "path traversal to system dirs"),
    (r"os\.system\s*\(", "os.system() call"),
    (r"subprocess\.\w+\s*\(", "subprocess call"),
    (r"exec\s*\(", "exec() call"),
    (r"__import__\s*\(", "__import__() call"),
    (r"importlib\.import_module", "importlib dynamic import"),
    (r"compile\s*\(", "compile() call"),
]

_PROMPT_INJECTION_PATTERNS: list[tuple[str, str]] = [
    (r"ignore\s+(?:all\s+)?(?:previous|prior|above|the\s+above)\s+instructions?", "prompt injection: 'ignore instructions'"),
    (r"you\s+are\s+(?:now|acting\s+as)", "prompt injection: 'you are now'"),
    (r"pretend\s+(?:you\s+are|to\s+be)", "prompt injection: 'pretend you are'"),
    (r"(?:system|developer)\s+prompt", "prompt injection: system prompt reference"),
    (r"override\s+(?:the\s+)?(?:system|safety|security)", "prompt injection: 'override safety'"),
    (r"bypass\s+(?:the\s+)?(?:filter|restriction|policy)", "prompt injection: 'bypass filter'"),
    (r"act\s+as\s+(?:an?\s+)?(?:unrestricted|unfiltered|uncensored)", "prompt injection: 'act as unrestricted'"),
    (r"do\s+not\s+(?:follow|obey)\s+(?:your\s+)?instructions", "prompt injection: 'do not follow instructions'"),
]

_HIGH_RISK_PERMISSIONS: frozenset[str] = frozenset({
    "write", "execute", "network", "sudo", "admin",
    "shell", "file_write", "file_delete", "run_script",
})

_HIGH_RISK_TOOLS: frozenset[str] = frozenset({
    "execute_shell", "run_python_code", "install_package",
    "sudo", "file_write", "file_delete",
})


# ── Data classes ──────────────────────────────────────────────────────


@dataclass
class AuditFinding:
    severity: str  # info, warning, error
    code: str
    message: str
    location: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditReport:
    capability_id: str
    audit_id: str
    created_at: str
    passed: bool
    risk_level: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    recommended_review_status: str = "needs_changes"
    remediation_suggestions: list[str] = field(default_factory=list)


@dataclass
class ReviewDecision:
    capability_id: str
    review_id: str
    review_status: str
    reviewer: str
    reason: str
    created_at: str
    expires_at: str | None = None


# ── Path helpers ──────────────────────────────────────────────────────


def _quarantine_root(store_data_dir: Path) -> Path:
    return store_data_dir / "quarantine"


def _quarantine_dir(store_data_dir: Path, capability_id: str) -> Path:
    _validate_id_token(capability_id)
    return _quarantine_root(store_data_dir) / capability_id


def _validate_id_token(token: str) -> None:
    if not token or "/" in token or "\\" in token or ".." in token:
        raise CapabilityError(f"Invalid capability_id: {token!r}")


# Maximum file size for text scanning (1 MB). Files larger than this are
# skipped and reported as info findings — never read into memory.
_MAX_SCAN_FILE_SIZE: int = 1_048_576


def _is_binary_content(data: bytes) -> bool:
    """Detect binary content by checking for null bytes in the first 8 KiB."""
    chunk = data[:8192]
    return b"\x00" in chunk


def _safe_file_text(path: Path) -> str | None:
    """Read file as text safely.

    Returns None for: non-files, symlinks, oversized files, binary files,
    unreadable files, or invalid UTF-8.
    Never raises — all errors produce None.
    """
    try:
        if not path.is_file():
            return None
        if path.is_symlink():
            return None
        size = path.stat().st_size
        if size > _MAX_SCAN_FILE_SIZE:
            return None
        if size == 0:
            return ""
        data = path.read_bytes()
        if _is_binary_content(data):
            return None
        return data.decode("utf-8")
    except Exception:
        return None


# ── list_quarantined_capabilities ─────────────────────────────────────


def list_quarantined_capabilities(
    *,
    store_data_dir: Path,
    risk_level: str | None = None,
    review_status: str | None = None,
    imported_after: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List quarantined capabilities with compact summaries.

    Reads only from data/capabilities/quarantine/. Does not call
    CapabilityStore.list() or query the active index.
    """
    root = _quarantine_root(store_data_dir)
    if not root.is_dir():
        return []

    results: list[dict[str, Any]] = []
    after_dt = datetime.fromisoformat(imported_after) if imported_after else None

    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        cap_id = entry.name
        try:
            _validate_id_token(cap_id)
        except CapabilityError:
            continue

        report_path = entry / "import_report.json"
        if not report_path.is_file():
            continue

        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Filters
        rl = report.get("risk_level", "low")
        if risk_level and rl != risk_level:
            continue

        # Check for latest review status
        latest_review = _latest_review_status(entry)
        if review_status and latest_review != review_status:
            continue

        # Date filter
        imported_at_str = report.get("imported_at", "")
        if after_dt:
            try:
                imported_dt = datetime.fromisoformat(imported_at_str)
                if imported_dt <= after_dt:
                    continue
            except (ValueError, TypeError):
                continue

        files_summary = report.get("files_summary", {})
        file_count = sum(len(v) for v in files_summary.values()) if isinstance(files_summary, dict) else 0

        results.append({
            "capability_id": cap_id,
            "name": report.get("name", cap_id),
            "type": _safe_manifest_field(entry, "type", "unknown"),
            "risk_level": rl,
            "imported_at": imported_at_str,
            "review_status": latest_review,
            "eval_passed": report.get("eval_passed", True),
            "eval_score": report.get("eval_score", 1.0),
            "file_count": file_count,
            "has_scripts": bool(files_summary.get("scripts")) if isinstance(files_summary, dict) else False,
            "has_tests": bool(files_summary.get("tests")) if isinstance(files_summary, dict) else False,
        })

        if len(results) >= min(limit, 100):
            break

    # Deterministic ordering: by imported_at descending
    results.sort(key=lambda r: r.get("imported_at", ""), reverse=True)
    return results


def _safe_manifest_field(quarantine_dir: Path, field: str, default: str) -> str:
    """Read a single field from manifest.json without exposing raw content."""
    try:
        manifest_path = quarantine_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return str(manifest.get(field, default))
    except Exception:
        pass
    return default


def _latest_review_status(quarantine_dir: Path) -> str | None:
    """Find the latest review decision status, or None if no review exists."""
    reviews_dir = quarantine_dir / "quarantine_reviews"
    if not reviews_dir.is_dir():
        return None
    latest: tuple[str, str] | None = None  # (created_at, status)
    for rev_file in sorted(reviews_dir.iterdir()):
        if not rev_file.suffix == ".json":
            continue
        try:
            rev = json.loads(rev_file.read_text(encoding="utf-8"))
            created = rev.get("created_at", "")
            status = rev.get("review_status", "")
            if latest is None or created > latest[0]:
                latest = (created, status)
        except (json.JSONDecodeError, OSError):
            continue
    return latest[1] if latest else None


# ── view_quarantine_report ────────────────────────────────────────────


def view_quarantine_report(
    *,
    store_data_dir: Path,
    capability_id: str,
    include_findings: bool = True,
    include_files_summary: bool = True,
) -> dict[str, Any]:
    """Return the import report and existing eval/policy findings.

    No script contents, no raw source paths, no execution.
    """
    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        raise CapabilityError(f"Quarantined capability not found: {capability_id}")

    report_path = qdir / "import_report.json"
    if not report_path.is_file():
        raise CapabilityError(f"No import report found for: {capability_id}")

    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise CapabilityError(f"Cannot read import report for: {capability_id}")

    result: dict[str, Any] = {
        "capability_id": capability_id,
        "name": _safe_manifest_field(qdir, "name", capability_id),
        "type": _safe_manifest_field(qdir, "type", "unknown"),
        "risk_level": _safe_manifest_field(qdir, "risk_level", "low"),
        "status": CapabilityStatus.QUARANTINED.value,
        "maturity": CapabilityMaturity.DRAFT.value,
        "imported_at": report.get("imported_at", ""),
        "imported_by": report.get("imported_by"),
        "source_path_hash": report.get("source_path_hash", ""),
        "target_scope": report.get("target_scope", "user"),
        "quarantine_reason": report.get("quarantine_reason", ""),
        "source_type": report.get("source_type", ""),
    }

    if include_findings:
        result["eval_passed"] = report.get("eval_passed", True)
        result["eval_score"] = report.get("eval_score", 1.0)
        # Sanitize findings: strip any raw paths or content bodies
        result["eval_findings"] = _sanitize_findings(report.get("eval_findings", []))
        result["policy_findings"] = _sanitize_findings(report.get("policy_findings", []))

    if include_files_summary:
        files_summary = report.get("files_summary", {})
        result["files_summary"] = {
            k: {"count": len(v), "names": v}
            for k, v in files_summary.items()
            if isinstance(v, list)
        }

    # Include latest review status if present
    result["latest_review_status"] = _latest_review_status(qdir)

    return result


def _sanitize_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove raw paths, script bodies, or other sensitive content from findings."""
    cleaned: list[dict[str, Any]] = []
    for f in findings:
        item: dict[str, Any] = {
            "severity": f.get("severity", "info"),
            "code": f.get("code", ""),
            "message": f.get("message", ""),
        }
        if "location" in f:
            item["location"] = f["location"]
        # Strip any details that might contain raw paths or bodies
        details = f.get("details", {})
        if isinstance(details, dict):
            safe_details = {}
            for k, v in details.items():
                if k in ("source_path", "raw_path", "file_contents", "script_body", "body"):
                    continue
                safe_details[k] = v
            if safe_details:
                item["details"] = safe_details
        cleaned.append(item)
    return cleaned


# ── audit_quarantined_capability ──────────────────────────────────────


def audit_quarantined_capability(
    *,
    store_data_dir: Path,
    capability_id: str,
    evaluator: "CapabilityEvaluator",
    policy: "CapabilityPolicy",
    write_report: bool = True,
) -> AuditReport:
    """Deterministic local audit of a quarantined capability.

    Re-runs evaluator and policy checks, scans metadata for dangerous
    patterns. Never executes scripts, imports code, runs tests, accesses
    network, or calls an LLM.

    Returns an AuditReport. Writes quarantine_audit_report.json only if
    write_report=True.
    """
    from src.capabilities.document import CapabilityParser

    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        raise CapabilityError(f"Quarantined capability not found: {capability_id}")

    audit_id = _generate_audit_id()
    now = datetime.now(timezone.utc)
    findings: list[dict[str, Any]] = []

    # 1. Parse the quarantined document
    parser = CapabilityParser()
    try:
        doc = parser.parse(qdir)
    except Exception as e:
        return AuditReport(
            capability_id=capability_id,
            audit_id=audit_id,
            created_at=now.isoformat(),
            passed=False,
            risk_level="unknown",
            findings=[{
                "severity": "error",
                "code": "parse_failed",
                "message": f"Cannot parse capability document: {e}",
                "location": str(qdir),
            }],
            recommended_review_status="rejected",
            remediation_suggestions=["Fix CAPABILITY.md / manifest.json so the document can be parsed"],
        )

    manifest = doc.manifest

    # 2. Run evaluator
    eval_record = evaluator.evaluate(doc)
    for f in eval_record.findings:
        findings.append({
            "severity": f.severity.value,
            "code": f.code,
            "message": f.message,
            "location": f.location or "",
            "source": "evaluator",
        })

    # 3. Run policy install check
    install_decision = policy.validate_install(
        manifest,
        source="external_package",
        context={"source_path": str(qdir)},
    )
    if install_decision.code not in ("install_allowed", "policy_allow"):
        findings.append({
            "severity": install_decision.severity.value,
            "code": install_decision.code,
            "message": install_decision.message,
            "location": "policy.validate_install",
            "source": "policy",
            "allowed": install_decision.allowed,
        })

    # 4. Scan file contents for dangerous patterns (text only, no execution)
    file_findings = _scan_quarantine_files(qdir)
    findings.extend(file_findings)

    # 5. Check required_tools risk
    tool_risk_findings = _check_tool_risk(manifest)
    findings.extend(tool_risk_findings)

    # 6. Check required_permissions risk
    perm_risk_findings = _check_permission_risk(manifest)
    findings.extend(perm_risk_findings)

    # 7. Check package status mismatch
    status_mismatch = _check_status_mismatch(manifest, qdir)
    findings.extend(status_mismatch)

    # 8. Check missing sections in CAPABILITY.md
    section_findings = _check_required_sections(qdir)
    findings.extend(section_findings)

    # Compute results
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    passed = len(errors) == 0

    risk_level = manifest.risk_level.value if hasattr(manifest.risk_level, "value") else str(manifest.risk_level)

    # Determine recommended review status: only errors and actionable warnings
    # (those not inherent to quarantine and not minor quality issues) block
    # approved_for_testing.
    blocking = [
        f for f in warnings
        if f.get("code", "") not in _QUARANTINE_INHERENT_WARNINGS
        and f.get("code", "") not in _MINOR_QUALITY_WARNINGS
    ]
    if errors:
        recommended = "needs_changes"
    elif blocking:
        recommended = "needs_changes"
    else:
        recommended = "approved_for_testing"

    # Build remediation suggestions
    remediation: list[str] = []
    for f in errors + warnings:
        code = f.get("code", "")
        if code == "dangerous_shell_pattern":
            remediation.append(f"Remove or replace dangerous shell pattern: {f.get('message', '')}")
        elif code == "prompt_injection_like":
            remediation.append(f"Remove suspicious prompt injection text: {f.get('message', '')}")
        elif code.startswith("missing_section"):
            remediation.append(f"Add missing section to CAPABILITY.md: {f.get('message', '')}")
        elif code == "script_file_present":
            remediation.append(f"Script files detected in quarantine — review and remove if unnecessary")
        elif code == "test_file_present":
            remediation.append(f"Test files detected in quarantine — review and remove if unnecessary")
        elif code == "path_anomaly":
            remediation.append(f"Path anomaly detected: {f.get('message', '')}")
        elif code == "high_risk_tool":
            remediation.append(f"Consider reducing tool requirements or justifying: {f.get('message', '')}")
        elif code == "high_risk_permission":
            remediation.append(f"Consider reducing permission requirements or justifying: {f.get('message', '')}")
        elif code == "status_mismatch":
            remediation.append(f"Fix manifest.json status to be 'quarantined'")
        elif code == "maturity_mismatch":
            remediation.append(f"Fix manifest.json maturity to be 'draft'")

    # Deduplicate
    remediation = list(dict.fromkeys(remediation))

    audit_report = AuditReport(
        capability_id=capability_id,
        audit_id=audit_id,
        created_at=now.isoformat(),
        passed=passed,
        risk_level=risk_level,
        findings=findings,
        recommended_review_status=recommended,
        remediation_suggestions=remediation,
    )

    # Write report if requested
    if write_report:
        _write_audit_report(qdir, audit_id, audit_report)

    return audit_report


def _scan_quarantine_files(qdir: Path) -> list[dict[str, Any]]:
    """Scan text files in quarantine for dangerous patterns.

    Reads files as text only — never executes, imports, or evaluates them.
    Files in scripts/, tests/, examples/ subdirectories are scanned.
    """
    findings: list[dict[str, Any]] = []
    scannable_dirs = ("scripts", "tests", "examples")

    for sub in scannable_dirs:
        subdir = qdir / sub
        if not subdir.is_dir():
            continue
        for fpath in sorted(subdir.iterdir()):
            if fpath.name == ".gitkeep":
                continue

            # Reject symlinks inside quarantine — never follow them
            if fpath.is_symlink():
                findings.append({
                    "severity": "warning",
                    "code": "symlink_in_quarantine",
                    "message": f"Symlink detected in quarantine and skipped: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })
                continue

            if not fpath.is_file():
                continue

            # Check file size before reading
            file_size = 0
            try:
                file_size = fpath.stat().st_size
            except OSError:
                pass

            if file_size > _MAX_SCAN_FILE_SIZE:
                findings.append({
                    "severity": "info",
                    "code": "large_file_skipped",
                    "message": f"File exceeds scan size limit ({_MAX_SCAN_FILE_SIZE} bytes) and was skipped: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                    "file_size": file_size,
                })

            # Try to read as text for binary detection
            is_binary = False
            try:
                raw = fpath.read_bytes()
                is_binary = _is_binary_content(raw)
            except Exception:
                pass

            if is_binary:
                findings.append({
                    "severity": "info",
                    "code": "binary_file_skipped",
                    "message": f"Binary file skipped during content scan: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })

            # Flag presence
            if sub == "scripts":
                findings.append({
                    "severity": "warning",
                    "code": "script_file_present",
                    "message": f"Script file present in quarantine: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })
            elif sub == "tests":
                findings.append({
                    "severity": "info",
                    "code": "test_file_present",
                    "message": f"Test file present in quarantine: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })
            elif sub == "examples":
                findings.append({
                    "severity": "info",
                    "code": "example_file_present",
                    "message": f"Example file present in quarantine: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })

            # Scan for dangerous patterns in text content
            # Skip if oversized or binary — already flagged above
            if file_size > _MAX_SCAN_FILE_SIZE or is_binary:
                continue

            content = _safe_file_text(fpath)
            if content is None:
                findings.append({
                    "severity": "warning",
                    "code": "unreadable_file",
                    "message": f"File could not be read as text: {sub}/{fpath.name}",
                    "location": f"{sub}/{fpath.name}",
                    "file_name": fpath.name,
                })
                continue

            for pattern, description in _SHELL_DANGER_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    findings.append({
                        "severity": "error",
                        "code": "dangerous_shell_pattern",
                        "message": f"Dangerous pattern in {sub}/{fpath.name}: {description}",
                        "location": f"{sub}/{fpath.name}",
                        "file_name": fpath.name,
                    })

            for pattern, description in _PROMPT_INJECTION_PATTERNS:
                if re.search(pattern, content, re.IGNORECASE):
                    findings.append({
                        "severity": "warning",
                        "code": "prompt_injection_like",
                        "message": f"Prompt injection text in {sub}/{fpath.name}: {description}",
                        "location": f"{sub}/{fpath.name}",
                        "file_name": fpath.name,
                    })

    # Check for path anomalies outside standard dirs
    for entry in sorted(qdir.iterdir()):
        if entry.name.startswith("."):
            findings.append({
                "severity": "warning",
                "code": "hidden_file",
                "message": f"Hidden file/dir in quarantine: {entry.name}",
                "location": entry.name,
            })
        elif entry.is_dir() and entry.name not in (
            "scripts", "tests", "examples", "evals", "traces", "versions",
            "quarantine_audit_reports", "quarantine_reviews",
        ):
            findings.append({
                "severity": "warning",
                "code": "unknown_directory",
                "message": f"Unexpected directory in quarantine: {entry.name}",
                "location": entry.name,
            })

    return findings


def _check_tool_risk(manifest: Any) -> list[dict[str, Any]]:
    """Check required_tools for high-risk entries."""
    findings: list[dict[str, Any]] = []
    tools = getattr(manifest, "required_tools", []) or []
    for tool in tools:
        if tool in _HIGH_RISK_TOOLS:
            findings.append({
                "severity": "warning",
                "code": "high_risk_tool",
                "message": f"Capability requires high-risk tool: {tool}",
                "location": "manifest.required_tools",
                "tool": tool,
            })
        elif "install" in tool or "sudo" in tool or "admin" in tool or "root" in tool:
            findings.append({
                "severity": "warning",
                "code": "suspicious_tool",
                "message": f"Capability requires suspicious tool name: {tool}",
                "location": "manifest.required_tools",
                "tool": tool,
            })
    return findings


def _check_permission_risk(manifest: Any) -> list[dict[str, Any]]:
    """Check required_permissions for high-risk entries."""
    findings: list[dict[str, Any]] = []
    perms = getattr(manifest, "required_permissions", []) or []
    for perm in perms:
        if perm.lower() in _HIGH_RISK_PERMISSIONS:
            findings.append({
                "severity": "warning",
                "code": "high_risk_permission",
                "message": f"Capability requires high-risk permission: {perm}",
                "location": "manifest.required_permissions",
                "permission": perm,
            })
    return findings


def _check_status_mismatch(manifest: Any, qdir: Path) -> list[dict[str, Any]]:
    """Check that manifest status/maturity match expected quarantine values."""
    findings: list[dict[str, Any]] = []

    status = manifest.status.value if hasattr(manifest.status, "value") else str(manifest.status)
    maturity = manifest.maturity.value if hasattr(manifest.maturity, "value") else str(manifest.maturity)

    if status != CapabilityStatus.QUARANTINED.value:
        findings.append({
            "severity": "error",
            "code": "status_mismatch",
            "message": f"Expected status=quarantined, got status={status}",
            "location": "manifest.status",
            "expected": CapabilityStatus.QUARANTINED.value,
            "actual": status,
        })

    if maturity != CapabilityMaturity.DRAFT.value:
        findings.append({
            "severity": "warning",
            "code": "maturity_mismatch",
            "message": f"Expected maturity=draft, got maturity={maturity}",
            "location": "manifest.maturity",
            "expected": CapabilityMaturity.DRAFT.value,
            "actual": maturity,
        })

    return findings


def _check_required_sections(qdir: Path) -> list[dict[str, Any]]:
    """Check CAPABILITY.md for required sections."""
    findings: list[dict[str, Any]] = []
    md_path = qdir / "CAPABILITY.md"
    if not md_path.is_file():
        findings.append({
            "severity": "error",
            "code": "missing_capability_md",
            "message": "CAPABILITY.md not found in quarantine",
            "location": str(qdir.name),
        })
        return findings

    content = _safe_file_text(md_path)
    if content is None:
        findings.append({
            "severity": "error",
            "code": "unreadable_capability_md",
            "message": "CAPABILITY.md exists but cannot be read",
            "location": "CAPABILITY.md",
        })
        return findings

    body_lower = content.lower()
    required = {
        "verification": "Verification",
        "failure handling": "Failure handling",
    }
    for key, label in required.items():
        if not re.search(rf"^#+\s+.*{re.escape(key)}", body_lower, re.MULTILINE):
            findings.append({
                "severity": "warning",
                "code": f"missing_section_{key.replace(' ', '_')}",
                "message": f"Missing required section: '{label}'",
                "location": "CAPABILITY.md",
            })

    return findings


def _generate_audit_id() -> str:
    return f"audit_{uuid.uuid4().hex[:12]}"


def _write_audit_report(qdir: Path, audit_id: str, report: AuditReport) -> Path:
    """Write audit report to quarantine_audit_reports/<audit_id>.json."""
    reports_dir = qdir / "quarantine_audit_reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    report_data = {
        "capability_id": report.capability_id,
        "audit_id": report.audit_id,
        "created_at": report.created_at,
        "passed": report.passed,
        "risk_level": report.risk_level,
        "findings": report.findings,
        "recommended_review_status": report.recommended_review_status,
        "remediation_suggestions": report.remediation_suggestions,
    }

    report_path = reports_dir / f"{audit_id}.json"
    report_path.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return report_path


# ── mark_quarantine_review ────────────────────────────────────────────


def mark_quarantine_review(
    *,
    store_data_dir: Path,
    capability_id: str,
    review_status: str,
    reviewer: str = "",
    reason: str = "",
    expires_at: str | None = None,
) -> ReviewDecision:
    """Write a review decision for a quarantined capability.

    This is STRICTLY report-only:
    - Does NOT change capability status from quarantined
    - Does NOT change maturity from draft
    - Does NOT call CapabilityLifecycleManager
    - Does NOT update the active index
    - Does NOT promote, activate, or move files
    - Does NOT make the capability retrievable by default
    - Does NOT make the capability visible in StateView

    approved_for_testing means ONLY: "review report says this quarantined
    package may be considered later by an explicit operator-only bridge."
    """
    if review_status not in ("needs_changes", "approved_for_testing", "rejected"):
        raise CapabilityError(
            f"Invalid review_status: {review_status!r}. "
            "Must be one of: needs_changes, approved_for_testing, rejected"
        )

    if not reason.strip():
        raise CapabilityError("reason is required for quarantine review")

    qdir = _quarantine_dir(store_data_dir, capability_id)
    if not qdir.is_dir():
        raise CapabilityError(f"Quarantined capability not found: {capability_id}")

    # Verify manifest still says quarantined/draft (safety check)
    manifest = None
    manifest_path = qdir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    if manifest:
        actual_status = manifest.get("status", "")
        if actual_status != CapabilityStatus.QUARANTINED.value:
            raise CapabilityError(
                f"Cannot review: manifest status is {actual_status!r}, "
                f"expected {CapabilityStatus.QUARANTINED.value!r}"
            )

    # Write review decision
    review_id = _generate_review_id()
    now = datetime.now(timezone.utc)

    decision = ReviewDecision(
        capability_id=capability_id,
        review_id=review_id,
        review_status=review_status,
        reviewer=reviewer.strip(),
        reason=reason.strip(),
        created_at=now.isoformat(),
        expires_at=expires_at,
    )

    reviews_dir = qdir / "quarantine_reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    review_path = reviews_dir / f"{review_id}.json"
    review_path.write_text(
        json.dumps({
            "capability_id": decision.capability_id,
            "review_id": decision.review_id,
            "review_status": decision.review_status,
            "reviewer": decision.reviewer,
            "reason": decision.reason,
            "created_at": decision.created_at,
            "expires_at": decision.expires_at,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return decision


def _generate_review_id() -> str:
    return f"review_{uuid.uuid4().hex[:12]}"
