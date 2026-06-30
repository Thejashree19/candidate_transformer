"""
Output validation engine.

Validates projected output dicts against:
  • The default canonical schema (when no config is supplied)
  • A user-supplied ``OutputConfig`` with per-field type and required checks

Always collects *all* errors before returning — never short-circuits.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from src.models import FieldConfig, OnMissing, OutputConfig

logger = logging.getLogger(__name__)

# Pre-compiled patterns
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


class OutputValidator:
    """Validates an output dict against a canonical or config-driven schema.

    Usage::

        validator = OutputValidator()
        is_valid, errors = validator.validate(output_dict)
        is_valid, errors = validator.validate(output_dict, config=my_config)
    """

    def validate(
        self,
        output: dict,
        config: Optional[OutputConfig] = None,
    ) -> tuple[bool, list[str]]:
        """Validate *output* and return ``(is_valid, error_messages)``.

        Parameters
        ----------
        output:
            The projected output dictionary to validate.
        config:
            If provided, validate against the config's field definitions.
            If ``None``, validate against the default canonical schema.

        Returns
        -------
        tuple[bool, list[str]]
            A two-tuple of ``(is_valid, list_of_error_messages)``.
            ``is_valid`` is ``True`` only when *error_messages* is empty.
        """
        if not isinstance(output, dict):
            return False, ["Output must be a dictionary"]

        if config is not None and config.fields:
            errors = self._validate_config_schema(output, config)
        else:
            errors = self._validate_default_schema(output)

        return (len(errors) == 0, errors)

    # ------------------------------------------------------------------
    # Default canonical schema validation
    # ------------------------------------------------------------------

    def _validate_default_schema(self, output: dict) -> list[str]:
        """Validate against the default CanonicalProfile schema."""
        errors: list[str] = []

        # candidate_id: non-empty string
        self._check_non_empty_string(output, "candidate_id", errors)

        # full_name: non-empty string
        self._check_non_empty_string(output, "full_name", errors)

        # emails: list of strings, each looks like email
        self._check_email_list(output, "emails", errors)

        # phones: list of strings, each in E.164
        self._check_phone_list(output, "phones", errors)

        # location: object with city/region/country
        self._check_location(output, "location", errors)

        # skills: list of objects with 'name' key
        self._check_skills(output, "skills", errors)

        # experience: list of objects with 'company' and 'title' keys
        self._check_experience(output, "experience", errors)

        # education: list of objects with 'institution' key
        self._check_education(output, "education", errors)

        # overall_confidence: number 0.0-1.0
        self._check_confidence(output, "overall_confidence", errors)

        return errors

    # ------------------------------------------------------------------
    # Config-driven schema validation
    # ------------------------------------------------------------------

    def _validate_config_schema(self, output: dict, config: OutputConfig) -> list[str]:
        """Validate against ``config.fields`` definitions."""
        errors: list[str] = []

        for field_cfg in config.fields:
            key = field_cfg.path
            value = output.get(key)
            is_present = key in output and value is not None

            # Check required
            if field_cfg.required and not is_present:
                errors.append(f"Required field '{key}' is missing or null")
                continue

            if not is_present:
                continue

            # Check type
            if not self._check_type(value, field_cfg.type):
                errors.append(
                    f"Field '{key}' has invalid type: expected {field_cfg.type}, "
                    f"got {type(value).__name__}"
                )

        return errors

    # ------------------------------------------------------------------
    # Default schema field checkers
    # ------------------------------------------------------------------

    @staticmethod
    def _check_non_empty_string(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a non-empty string."""
        val = output.get(key)
        if val is None:
            errors.append(f"'{key}' is missing")
        elif not isinstance(val, str):
            errors.append(f"'{key}' must be a string, got {type(val).__name__}")
        elif not val.strip():
            errors.append(f"'{key}' must be a non-empty string")

    @staticmethod
    def _check_email_list(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a list of valid-looking email strings."""
        val = output.get(key)
        if val is None:
            # emails can be absent — treat as empty list
            return
        if not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")
            return
        for i, email in enumerate(val):
            if not isinstance(email, str):
                errors.append(f"'{key}[{i}]' must be a string, got {type(email).__name__}")
            elif not _EMAIL_RE.match(email):
                errors.append(f"'{key}[{i}]' does not look like a valid email: '{email}'")

    @staticmethod
    def _check_phone_list(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a list of E.164-formatted phone strings."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")
            return
        for i, phone in enumerate(val):
            if not isinstance(phone, str):
                errors.append(f"'{key}[{i}]' must be a string, got {type(phone).__name__}")
            elif not phone.startswith("+"):
                errors.append(
                    f"'{key}[{i}]' must be in E.164 format (starts with +): '{phone}'"
                )

    @staticmethod
    def _check_location(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a location object with valid fields."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, dict):
            errors.append(f"'{key}' must be an object, got {type(val).__name__}")
            return
        country = val.get("country")
        if country is not None and isinstance(country, str) and country.strip():
            if not _COUNTRY_RE.match(country):
                errors.append(
                    f"'{key}.country' must be a 2-character alpha ISO code, got '{country}'"
                )

    @staticmethod
    def _check_skills(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a list of objects with a 'name' key."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")
            return
        for i, skill in enumerate(val):
            if not isinstance(skill, dict):
                errors.append(f"'{key}[{i}]' must be an object, got {type(skill).__name__}")
            elif "name" not in skill:
                errors.append(f"'{key}[{i}]' is missing required key 'name'")

    @staticmethod
    def _check_experience(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a list of objects with 'company' and 'title' keys."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")
            return
        for i, exp in enumerate(val):
            if not isinstance(exp, dict):
                errors.append(f"'{key}[{i}]' must be an object, got {type(exp).__name__}")
                continue
            if "company" not in exp:
                errors.append(f"'{key}[{i}]' is missing required key 'company'")
            if "title" not in exp:
                errors.append(f"'{key}[{i}]' is missing required key 'title'")

    @staticmethod
    def _check_education(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a list of objects with 'institution' key."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, list):
            errors.append(f"'{key}' must be a list, got {type(val).__name__}")
            return
        for i, edu in enumerate(val):
            if not isinstance(edu, dict):
                errors.append(f"'{key}[{i}]' must be an object, got {type(edu).__name__}")
            elif "institution" not in edu:
                errors.append(f"'{key}[{i}]' is missing required key 'institution'")

    @staticmethod
    def _check_confidence(output: dict, key: str, errors: list[str]) -> None:
        """Ensure *key* is a number between 0.0 and 1.0."""
        val = output.get(key)
        if val is None:
            return
        if not isinstance(val, (int, float)):
            errors.append(f"'{key}' must be a number, got {type(val).__name__}")
            return
        if val < 0.0 or val > 1.0:
            errors.append(f"'{key}' must be between 0.0 and 1.0, got {val}")

    # ------------------------------------------------------------------
    # Type checking helper
    # ------------------------------------------------------------------

    @staticmethod
    def _check_type(value: Any, expected_type: str) -> bool:
        """Return True if *value* matches the expected type string.

        Supported type strings:
          - ``string``: ``str``
          - ``string[]``: ``list[str]``
          - ``number``: ``int | float``
          - ``object``: ``dict``
          - ``object[]``: ``list[dict]``
          - ``boolean``: ``bool``
        """
        if value is None:
            return True

        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "string[]":
            return isinstance(value, list) and all(isinstance(v, str) for v in value)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "object[]":
            return isinstance(value, list) and all(isinstance(v, dict) for v in value)
        if expected_type == "boolean":
            return isinstance(value, bool)

        # Unknown type — accept
        return True
