"""Small JSON-schema validator for model-facing tool arguments.

The tool schemas in Lapwing are intentionally simple OpenAI function
schemas. Pulling in a full JSON Schema dependency would widen the runtime
surface, so this module validates only the subset the registry emits:
object properties, required fields, primitive types, arrays, and enums.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.tools.types import (
    ToolErrorClass,
    ToolErrorCode,
    ToolResultStatus,
    make_tool_error_result,
)


_PATH_RE = re.compile(r"(?<![\w.~-])(?:/[A-Za-z0-9._~+\-]+){2,}")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|bearer)\s*[:=]\s*[^\s,;]+"
)


@dataclass(frozen=True)
class FieldError:
    field: str
    expected: str
    received_type: str
    allowed_values: list[str] = field(default_factory=list)
    schema_hint: str = ""

    def to_safe_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "field": self.field,
            "expected": self.expected,
            "received_type": self.received_type,
        }
        if self.allowed_values:
            data["allowed_values"] = self.allowed_values
        if self.schema_hint:
            data["schema_hint"] = sanitize_for_tool_error(self.schema_hint)
        return data


@dataclass(frozen=True)
class ValidationReport:
    valid: bool
    missing_fields: list[str] = field(default_factory=list)
    field_errors: list[FieldError] = field(default_factory=list)

    @property
    def invalid_fields(self) -> list[str]:
        return [e.field for e in self.field_errors]


def validate_tool_arguments(
    *,
    tool_name: str,
    schema: dict[str, Any],
    arguments: Any,
) -> ValidationReport:
    missing: list[str] = []
    errors: list[FieldError] = []

    if not isinstance(arguments, dict):
        return ValidationReport(
            valid=False,
            field_errors=[
                FieldError(
                    field="$",
                    expected="object",
                    received_type=_type_name(arguments),
                    schema_hint="Tool arguments must be a JSON object.",
                )
            ],
        )

    if str(schema.get("type") or "object") != "object":
        return ValidationReport(valid=True)

    properties = schema.get("properties") or {}
    required = schema.get("required") or []
    legacy_aliases = schema.get("x_legacy_required_aliases") or {}
    for name in required:
        aliases = legacy_aliases.get(name, []) if isinstance(legacy_aliases, dict) else []
        if name not in arguments and not any(alias in arguments for alias in aliases):
            missing.append(str(name))

    for name, subschema in properties.items():
        if name not in arguments:
            continue
        _validate_field(
            field_name=str(name),
            value=arguments.get(name),
            schema=subschema if isinstance(subschema, dict) else {},
            errors=errors,
        )

    return ValidationReport(valid=not missing and not errors, missing_fields=missing, field_errors=errors)


def validation_error_result(
    *,
    tool_name: str,
    schema: dict[str, Any],
    arguments: Any,
    report: ValidationReport,
):
    safe_details = {
        "tool_name": sanitize_for_tool_error(tool_name),
        "missing_fields": report.missing_fields,
        "invalid_fields": report.invalid_fields,
        "field_errors": [e.to_safe_dict() for e in report.field_errors],
        "schema_hints": _schema_hints(schema, report),
        "repair_guidance": (
            "Retry the tool call with all required fields present and values "
            "matching the expected JSON types/enums."
        ),
    }
    return make_tool_error_result(
        status=ToolResultStatus.VALIDATION_ERROR,
        error_code=ToolErrorCode.SCHEMA_VALIDATION_FAILED,
        error_class=ToolErrorClass.VALIDATION,
        retryable=True,
        safe_details=safe_details,
        reason="tool arguments failed schema validation",
    )


def sanitize_for_tool_error(value: Any) -> str:
    text = str(value or "")
    text = _SECRET_RE.sub(r"\1=[REDACTED]", text)
    text = _PATH_RE.sub("[REDACTED_PATH]", text)
    return text[:300]


def _validate_field(
    *,
    field_name: str,
    value: Any,
    schema: dict[str, Any],
    errors: list[FieldError],
) -> None:
    expected = schema.get("type")
    allowed = schema.get("enum")

    if allowed is not None and value not in allowed:
        errors.append(
            FieldError(
                field=field_name,
                expected="enum",
                received_type=_type_name(value),
                allowed_values=[str(v) for v in allowed],
                schema_hint=str(schema.get("description") or ""),
            )
        )
        return

    if expected is None:
        return

    if isinstance(expected, list):
        if any(_matches_type(value, item) for item in expected):
            return
        expected_text = "|".join(str(item) for item in expected)
    else:
        if _matches_type(value, str(expected)):
            if expected == "array" and isinstance(value, list):
                _validate_array_items(field_name=field_name, value=value, schema=schema, errors=errors)
            return
        expected_text = str(expected)

    errors.append(
        FieldError(
            field=field_name,
            expected=expected_text,
            received_type=_type_name(value),
            allowed_values=[str(v) for v in allowed] if allowed else [],
            schema_hint=str(schema.get("description") or ""),
        )
    )


def _validate_array_items(
    *,
    field_name: str,
    value: list[Any],
    schema: dict[str, Any],
    errors: list[FieldError],
) -> None:
    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return
    expected = item_schema.get("type")
    if expected is None:
        return
    for idx, item in enumerate(value):
        if not _matches_type(item, str(expected)):
            errors.append(
                FieldError(
                    field=f"{field_name}[{idx}]",
                    expected=str(expected),
                    received_type=_type_name(item),
                    schema_hint=str(item_schema.get("description") or schema.get("description") or ""),
                )
            )
            break


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _schema_hints(schema: dict[str, Any], report: ValidationReport) -> dict[str, Any]:
    properties = schema.get("properties") or {}
    hints: dict[str, Any] = {}
    fields = set(report.missing_fields) | set(report.invalid_fields)
    for name in sorted(fields):
        base_name = name.split("[", 1)[0]
        subschema = properties.get(base_name)
        if not isinstance(subschema, dict):
            continue
        hint: dict[str, Any] = {}
        if "type" in subschema:
            hint["expected_type"] = subschema.get("type")
        if "enum" in subschema:
            hint["allowed_values"] = [str(v) for v in subschema.get("enum") or []]
        if subschema.get("description"):
            hint["description"] = sanitize_for_tool_error(subschema.get("description"))
        hints[base_name] = hint
    return hints
