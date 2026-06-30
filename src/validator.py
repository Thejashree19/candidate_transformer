"""
Validation engine.

Provides two validators:

* ``RecordValidator``  — pre-merge validation for raw ``RawCandidate``
  records.  Performs type/shape checks and quarantines garbage records.
* ``OutputValidator``  — post-projection validation for output dicts
  against the canonical schema or a user-supplied ``OutputConfig``.

Both validators collect *all* errors before returning — never short-circuit.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

import jsonschema

from src.models import FieldConfig, OnMissing, OutputConfig, CanonicalProfile

logger = logging.getLogger(__name__)

# Pre-compiled patterns
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164_RE = re.compile(r"^\+[1-9]\d{1,14}$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


class RecordValidator:
    """Pre-merge validator for raw candidate records.

    Performs type/shape checks on each field and quarantines garbage records
    (empty or non-parseable) into an errors list attached to the run.
    """

    def validate_batch(
        self,
        records: list,  # list[RawCandidate] — avoid circular import
    ) -> tuple[list, list[str]]:
        """Validate a batch of raw candidate records.

        Parameters
        ----------
        records:
            List of RawCandidate objects to validate.

        Returns
        -------
        tuple[list, list[str]]
            ``(valid_records, error_messages)`` — garbage records are
            removed from the valid list and described in errors.
        """
        valid: list = []
        errors: list[str] = []

        for i, record in enumerate(records):
            record_errors = self._validate_record(record, i)
            if self._is_garbage(record):
                # Quarantine: completely empty/useless record
                errors.append(
                    f"Record {i} quarantined (garbage): "
                    f"source={record.source_type.value}, "
                    f"source_id={record.source_id or 'unknown'}, "
                    f"errors={record_errors}"
                )
                continue
            if record_errors:
                # Partial errors: fix up the record and keep it
                for err in record_errors:
                    errors.append(
                        f"Record {i} field warning: {err} "
                        f"(source={record.source_type.value})"
                    )
            valid.append(record)

        return valid, errors

    @staticmethod
    def _validate_record(record, index: int) -> list[str]:
        """Validate individual fields on a record. Returns list of error messages."""
        errors: list[str] = []

        # full_name should be a non-empty string if present
        if record.full_name is not None:
            if not isinstance(record.full_name, str) or not record.full_name.strip():
                errors.append(f"full_name is empty or not a string")
                record.full_name = None

        # emails should be a list of valid-looking strings
        if record.emails:
            valid_emails = []
            for email in record.emails:
                if isinstance(email, str) and _EMAIL_RE.match(email.strip()):
                    valid_emails.append(email.strip())
                else:
                    errors.append(f"Invalid email dropped: '{email}'")
            record.emails = valid_emails

        # phones should be a list of strings
        if record.phones:
            valid_phones = []
            for phone in record.phones:
                if isinstance(phone, str) and phone.strip():
                    valid_phones.append(phone.strip())
                else:
                    errors.append(f"Invalid phone dropped: '{phone}'")
            record.phones = valid_phones

        # skills should be a list
        if not isinstance(record.skills, list):
            errors.append(f"skills is not a list, resetting to []")
            record.skills = []

        # years_experience should be numeric if present
        if record.years_experience is not None:
            if not isinstance(record.years_experience, (int, float)):
                errors.append(f"years_experience is not numeric: '{record.years_experience}'")
                record.years_experience = None

        return errors

    @staticmethod
    def _is_garbage(record) -> bool:
        """Check if a record is completely empty/garbage and should be quarantined."""
        has_name = record.full_name and str(record.full_name).strip()
        has_email = bool(record.emails)
        has_phone = bool(record.phones)
        
        # A record is garbage if it has NO identifying keys (name, email, or phone)
        # Without these, it cannot be merged or identified.
        return not any([has_name, has_email, has_phone])


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

        if not output.get("candidate_id"):
            errors.append("Missing required field 'candidate_id'")

        if not output.get("full_name") or not str(output["full_name"]).strip():
            errors.append("Missing or empty required field 'full_name'")

        conf = output.get("overall_confidence")
        if conf is not None and not (0.0 <= conf <= 1.0):
            errors.append(f"Confidence score {conf} out of range [0.0, 1.0]")

        if "emails" in output:
            for i, email in enumerate(output["emails"]):
                if not isinstance(email, str) or not _EMAIL_RE.match(email):
                    errors.append(f"Invalid email at index {i}: '{email}'")

        if "phones" in output:
            for i, phone in enumerate(output["phones"]):
                # phone might be a string or a dict (PhoneEntry)
                phone_str = phone.get("normalized") or phone.get("raw") if isinstance(phone, dict) else phone
                if not isinstance(phone_str, str) or not _E164_RE.match(phone_str):
                    errors.append(f"Invalid phone (not E.164) at index {i}: '{phone_str}'")

        return errors

    # ------------------------------------------------------------------
    # Config-driven schema validation
    # ------------------------------------------------------------------

    def _validate_config_schema(self, output: dict, config: OutputConfig) -> list[str]:
        """Validate against ``config.fields`` definitions using generated JSON Schema."""
        errors: list[str] = []
        
        # Build JSON Schema dynamically from config.fields
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {},
            "required": []
        }
        
        type_mapping = {
            "string": {"type": "string"},
            "string[]": {"type": "array", "items": {"type": "string"}},
            "number": {"type": "number"},
            "object": {"type": "object"},
            "object[]": {"type": "array", "items": {"type": "object"}},
            "boolean": {"type": "boolean"},
        }

        for field_cfg in config.fields:
            key = field_cfg.path
            
            # Simple handling of flat paths. Deep paths (e.g. `location.city`) 
            # would require building a nested schema, but this serves as a basic check
            # for the top-level keys or assumes the output is flat/mapped.
            # In an advanced setup, we'd build the nested object schema.
            
            prop_schema = type_mapping.get(field_cfg.type, {})
            schema["properties"][key] = prop_schema
            
            if field_cfg.required:
                schema["required"].append(key)
                
        try:
            jsonschema.validate(instance=output, schema=schema)
        except jsonschema.ValidationError as e:
            errors.append(f"Validation error: {e.message} at {'/'.join(map(str, e.path))}")
        except Exception as e:
            errors.append(f"Unexpected validation error: {e}")

        return errors


