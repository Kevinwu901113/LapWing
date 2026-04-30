"""Custom errors for the capability document system."""

from __future__ import annotations


class CapabilityError(Exception):
    """Base error for all capability-related failures."""


class InvalidManifestError(CapabilityError):
    """The manifest.json is missing, malformed, or fails validation."""


class InvalidDocumentError(CapabilityError):
    """The CAPABILITY.md is missing, malformed, or fails validation."""


class MissingFieldError(CapabilityError):
    """A required field is absent from the manifest or front matter."""

    def __init__(self, field: str, source: str = "") -> None:
        self.field = field
        self.source = source
        msg = f"Missing required field '{field}'"
        if source:
            msg += f" in {source}"
        super().__init__(msg)


class InvalidEnumValueError(CapabilityError):
    """A field value is not a recognised enum member."""

    def __init__(self, field: str, value: str, allowed: frozenset[str], source: str = "") -> None:
        self.field = field
        self.value = value
        self.allowed = allowed
        self.source = source
        msg = f"Invalid value '{value}' for field '{field}'; allowed: {sorted(allowed)}"
        if source:
            msg += f" in {source}"
        super().__init__(msg)


class HashVerificationError(CapabilityError):
    """The stored content_hash does not match the computed hash."""


class MalformedFrontMatterError(CapabilityError):
    """The YAML front matter in CAPABILITY.md could not be parsed."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(f"Malformed YAML front matter: {detail}")
