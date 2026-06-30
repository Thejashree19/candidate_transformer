"""
Configurable output projection layer.

Transforms a ``CanonicalProfile`` into a plain ``dict`` based on an
``OutputConfig``, supporting:
  • Field selection via JSONPath-like expressions
  • Field renaming (``path`` vs ``from`` / ``from_path``)
  • Confidence and provenance inclusion toggles
  • Missing-value policies: ``null``, ``omit``, ``error``
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from pydantic import BaseModel

from src.normalizers.date import normalize_date
from src.normalizers.phone import normalize_phone
from src.normalizers.skills import canonicalize_skill
from src.models import (
    CanonicalProfile,
    FieldConfig,
    OnMissing,
    OutputConfig,
)

logger = logging.getLogger(__name__)

# Pre-compiled regex for array-index access: e.g. ``emails[0]``
_INDEX_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$")
# Pre-compiled regex for array-map access: e.g. ``skills[].name``
_MAP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[\]\.(.+)$")


class OutputProjector:
    """Projects a ``CanonicalProfile`` to a configurable output dict.

    Parameters
    ----------
    config:
        An ``OutputConfig`` defining which fields to include, how to
        rename/transform them, and how to handle missing values.

    Raises
    ------
    ValueError
        If ``config`` contains invalid field definitions at init time.
    """

    def __init__(self, config: OutputConfig) -> None:
        self._config = config
        self._validate_config()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def project(self, profile: CanonicalProfile) -> dict:
        """Project *profile* to a plain dict per the configured output shape.

        Parameters
        ----------
        profile:
            The canonical profile to project.

        Returns
        -------
        dict
            A plain dictionary containing the selected fields.

        Raises
        ------
        ValueError
            If ``on_missing`` is ``error`` and a required field is missing.
        """
        # Serialize profile to dict for path resolution
        profile_dict = self._profile_to_dict(profile)

        # If no fields configured, output everything
        if not self._config.fields:
            result = dict(profile_dict)
            if not self._config.include_confidence:
                result.pop("overall_confidence", None)
            if not self._config.include_provenance:
                result.pop("provenance", None)
            return result

        result: dict[str, Any] = {}

        for field_cfg in self._config.fields:
            source_path = field_cfg.from_path or field_cfg.path
            output_key = field_cfg.path

            try:
                value = self._resolve_path(profile_dict, source_path)
            except (KeyError, IndexError, TypeError):
                value = None

            value = self._apply_normalization(value, field_cfg.normalize)

            # Handle missing values
            if value is None or (isinstance(value, (list, dict, str)) and not value):
                is_missing = True
            else:
                is_missing = False

            if is_missing and field_cfg.required:
                behavior = self._config.on_missing
                if behavior == OnMissing.ERROR:
                    raise ValueError(
                        f"Required field '{output_key}' (from '{source_path}') is missing"
                    )
                elif behavior == OnMissing.OMIT:
                    continue
                else:  # NULL
                    result[output_key] = None
                    continue
            elif is_missing and not field_cfg.required:
                behavior = self._config.on_missing
                if behavior == OnMissing.OMIT:
                    continue
                else:
                    result[output_key] = None
                    continue

            result[output_key] = value

        # Include confidence if configured
        if self._config.include_confidence:
            result["overall_confidence"] = profile.overall_confidence
            # Also include per-skill confidence if skills are in output
            if "skills" in result and isinstance(result["skills"], list):
                # Skills are already serialized with confidence from profile_dict
                pass

        # Include provenance if configured
        if self._config.include_provenance:
            result["provenance"] = profile_dict.get("provenance", [])

        return result

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    def _resolve_path(self, profile_dict: dict, path: str) -> Any:
        """Resolve a JSONPath-like expression against a profile dict.

        Supported patterns:
          - ``full_name``               — simple top-level field
          - ``location.city``           — nested dot access
          - ``emails[0]``              — array index
          - ``skills[].name``          — array map
          - ``experience[0].title``    — array index + nested
        """
        if not path:
            return None

        # Check for array-map pattern first: ``skills[].name``
        map_match = _MAP_RE.match(path)
        if map_match:
            array_key = map_match.group(1)
            sub_path = map_match.group(2)
            arr = profile_dict.get(array_key)
            if not isinstance(arr, list):
                return None
            return [self._resolve_nested(item, sub_path) for item in arr]

        # Check for array-index pattern: ``emails[0]`` or ``experience[0].title``
        parts = path.split(".", 1)
        idx_match = _INDEX_RE.match(parts[0])
        if idx_match:
            array_key = idx_match.group(1)
            index = int(idx_match.group(2))
            arr = profile_dict.get(array_key)
            if not isinstance(arr, list) or index >= len(arr):
                return None
            element = arr[index]
            if len(parts) > 1:
                # Nested access after index: experience[0].title
                if isinstance(element, dict):
                    return self._resolve_nested(element, parts[1])
                return None
            return element

        # Simple or nested dot path: ``full_name`` or ``location.city``
        return self._resolve_nested(profile_dict, path)

    @staticmethod
    def _resolve_nested(data: Any, path: str) -> Any:
        """Walk a dotted path into a dict/nested structure."""
        if data is None:
            return None

        parts = path.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    def _apply_normalization(self, value: Any, normalize: Optional[str]) -> Any:
        """Apply an output-level normalization hint to a projected value."""
        if value is None or not normalize:
            return value

        normalize_key = normalize.strip().lower()

        if normalize_key == "e164":
            return self._normalize_phone_value(value)
        if normalize_key == "canonical":
            return self._normalize_skill_value(value)
        if normalize_key in {"yyyy-mm", "yyyy-mm-dd", "date"}:
            return self._normalize_date_value(value)

        logger.debug("Unknown output normalization '%s' — leaving value unchanged", normalize)
        return value

    @staticmethod
    def _normalize_phone_value(value: Any) -> Any:
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                phone, _confidence = normalize_phone(item)
                if phone:
                    normalized.append(phone)
            return normalized

        if isinstance(value, str):
            phone, _confidence = normalize_phone(value)
            return phone

        return value

    @staticmethod
    def _normalize_skill_value(value: Any) -> Any:
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                skill_name, _confidence, _method = canonicalize_skill(item)
                normalized.append(skill_name)
            return normalized

        if isinstance(value, str):
            skill_name, _confidence, _method = canonicalize_skill(value)
            return skill_name

        return value

    @staticmethod
    def _normalize_date_value(value: Any) -> Any:
        if isinstance(value, list):
            normalized: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                date_value = normalize_date(item)
                if date_value:
                    normalized.append(date_value)
            return normalized

        if isinstance(value, str):
            return normalize_date(value)

        return value

    # ------------------------------------------------------------------
    # Type validation
    # ------------------------------------------------------------------

    def _validate_type(self, value: Any, expected_type: str) -> bool:
        """Check whether *value* matches the expected type string.

        Supported type strings: ``string``, ``string[]``, ``number``,
        ``object``, ``object[]``, ``boolean``.
        """
        if value is None:
            return True  # None is acceptable for any type (checked separately)

        type_map: dict[str, type | tuple[type, ...]] = {
            "string": str,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
        }

        if expected_type == "string[]":
            return isinstance(value, list) and all(isinstance(v, str) for v in value)
        if expected_type == "object[]":
            return isinstance(value, list) and all(isinstance(v, dict) for v in value)
        if expected_type in type_map:
            return isinstance(value, type_map[expected_type])

        # Unknown type → accept anything
        logger.debug("Unknown expected type: %s — accepting any value", expected_type)
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_config(self) -> None:
        """Validate the ``OutputConfig`` at init time."""
        for i, field in enumerate(self._config.fields):
            if not field.path:
                raise ValueError(f"Field at index {i} has an empty 'path'")

    @staticmethod
    def _profile_to_dict(profile: CanonicalProfile) -> dict:
        """Serialize a profile to a plain dict, handling Pydantic models."""
        return profile.model_dump(mode="python")
