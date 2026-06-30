"""
Extractor for Applicant Tracking System (ATS) JSON exports.

The ATS uses non-standard field names that must be mapped to the canonical
:class:`RawCandidate` schema:

============================  ========================
ATS field                     RawCandidate field
============================  ========================
applicant_name                full_name
contact_email                 emails
contact_phone                 phones
current_employer              current_company
job_title                     current_title
applicant_location            location_raw
applied_position              headline
tech_stack                    skills
work_history                  experience
education_history             education
social_profiles               linkedin_url / github_url
years_of_experience           years_experience
============================  ========================

``raw_data`` may be a JSON string, a single dict, or a list of dicts.
"""

from __future__ import annotations

import json
from typing import Any

from src.models import (
    ExtractionMethod,
    RawCandidate,
    RawEducation,
    RawExperience,
    RawSkill,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)

from .base import BaseExtractor


class ATSExtractor(BaseExtractor):
    """Parse ATS JSON payloads into :class:`RawCandidate` records."""

    _EXTRACTION_METHOD = ExtractionMethod.FIELD_MAPPING

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract candidates from the ATS JSON in *envelope.raw_data*.

        Args:
            envelope: Source envelope containing ATS JSON data.

        Returns:
            A list of :class:`RawCandidate` instances.
        """
        records = self._normalize_input(envelope.raw_data)

        if not records:
            envelope.status = SourceStatus.EMPTY
            return []

        candidates: list[RawCandidate] = []
        for record in records:
            candidate = self._map_record(record)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            envelope.status = SourceStatus.EMPTY

        return candidates

    # ------------------------------------------------------------------
    # Input normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_input(raw: Any) -> list[dict[str, Any]]:
        """Coerce *raw* into a ``list[dict]``.

        Accepts a JSON string, a single dict, or a list of dicts.
        Returns ``[]`` for anything it cannot parse.
        """
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return []

        if isinstance(raw, dict):
            # Unwrap common wrapper keys: {"candidates": [...]} or {"data": [...]}
            for wrapper_key in ("candidates", "data", "applicants", "records"):
                if wrapper_key in raw and isinstance(raw[wrapper_key], list):
                    return [r for r in raw[wrapper_key] if isinstance(r, dict)]
            # Single candidate object
            return [raw]
        if isinstance(raw, list):
            return [r for r in raw if isinstance(r, dict)]
        return []

    # ------------------------------------------------------------------
    # Field mapping
    # ------------------------------------------------------------------

    def _map_record(self, rec: dict[str, Any]) -> RawCandidate | None:
        """Map a single ATS dict to :class:`RawCandidate`."""

        def _str(key: str) -> str | None:
            val = rec.get(key)
            if val is None:
                return None
            text = str(val).strip()
            return text or None

        full_name = _str("applicant_name")

        # Emails — accept string or list.
        emails = self._as_string_list(rec.get("contact_email"))

        # Phones — accept string or list.
        phones = self._as_string_list(rec.get("contact_phone"))

        # Skills from tech_stack (list of strings).
        skills: list[RawSkill] = []
        tech_stack = rec.get("tech_stack")
        if isinstance(tech_stack, list):
            for item in tech_stack:
                name = str(item).strip()
                if name:
                    skills.append(
                        RawSkill(
                            name=name,
                            source=SourceType.ATS_JSON,
                            method=self._EXTRACTION_METHOD,
                        )
                    )
        elif isinstance(tech_stack, str) and tech_stack.strip():
            # Fallback: comma-separated string.
            for name in tech_stack.split(","):
                name = name.strip()
                if name:
                    skills.append(
                        RawSkill(
                            name=name,
                            source=SourceType.ATS_JSON,
                            method=self._EXTRACTION_METHOD,
                        )
                    )

        # Work history.
        experience = self._parse_experience(rec.get("work_history"))

        # Education history.
        education = self._parse_education(rec.get("education_history"))

        # Social profiles — extract linkedin and github URLs.
        linkedin_url, github_url = self._parse_social_profiles(
            rec.get("social_profiles")
        )

        # Years of experience.
        years_experience: float | None = None
        yoe_raw = rec.get("years_of_experience")
        if yoe_raw is not None:
            try:
                years_experience = float(yoe_raw)
            except (ValueError, TypeError):
                years_experience = None

        return RawCandidate(
            full_name=full_name,
            emails=emails,
            phones=phones,
            current_company=_str("current_employer"),
            current_title=_str("job_title"),
            location_raw=_str("applicant_location"),
            headline=_str("applied_position"),
            linkedin_url=linkedin_url,
            github_url=github_url,
            skills=skills,
            experience=experience,
            education=education,
            years_experience=years_experience,
            source_type=SourceType.ATS_JSON,
            extraction_method=self._EXTRACTION_METHOD,
        )

    # ------------------------------------------------------------------
    # Nested-object parsers
    # ------------------------------------------------------------------

    def _parse_experience(
        self, raw: Any
    ) -> list[RawExperience]:
        """Parse the ``work_history`` array.

        Each entry is expected to have:
          employer, role, start_date, end_date, description
        """
        if not isinstance(raw, list):
            return []

        results: list[RawExperience] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            results.append(
                RawExperience(
                    company=self._str_or_none(entry.get("employer")),
                    title=self._str_or_none(entry.get("role")),
                    start=self._str_or_none(entry.get("start_date")),
                    end=self._str_or_none(entry.get("end_date")),
                    summary=self._str_or_none(entry.get("description")),
                    source=SourceType.ATS_JSON,
                    method=self._EXTRACTION_METHOD,
                )
            )
        return results

    def _parse_education(
        self, raw: Any
    ) -> list[RawEducation]:
        """Parse the ``education_history`` array.

        Each entry is expected to have:
          school, degree_type, major, graduation_year
        """
        if not isinstance(raw, list):
            return []

        results: list[RawEducation] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue

            end_year: int | None = None
            grad_raw = entry.get("graduation_year")
            if grad_raw is not None:
                try:
                    end_year = int(grad_raw)
                except (ValueError, TypeError):
                    end_year = None

            results.append(
                RawEducation(
                    institution=self._str_or_none(entry.get("school")),
                    degree=self._str_or_none(entry.get("degree_type")),
                    field=self._str_or_none(entry.get("major")),
                    end_year=end_year,
                    source=SourceType.ATS_JSON,
                    method=self._EXTRACTION_METHOD,
                )
            )
        return results

    @staticmethod
    def _parse_social_profiles(
        raw: Any,
    ) -> tuple[str | None, str | None]:
        """Extract LinkedIn and GitHub URLs from ``social_profiles``.

        Accepts a list of dicts (``{type, url}`` or ``{network, url}``),
        a list of plain URL strings, or a single dict.
        """
        linkedin_url: str | None = None
        github_url: str | None = None

        if isinstance(raw, dict):
            # Handle flat format: {"linkedin": "url", "github": "url", "portfolio": "url"}
            for key, val in raw.items():
                if not isinstance(val, str) or not val.strip():
                    continue
                key_lower = key.lower()
                url = val.strip()
                if "linkedin" in key_lower or "linkedin" in url.lower():
                    linkedin_url = linkedin_url or url
                elif "github" in key_lower or "github" in url.lower():
                    github_url = github_url or url
            return linkedin_url, github_url

        if not isinstance(raw, list):
            return linkedin_url, github_url

        for item in raw:
            url: str | None = None
            if isinstance(item, dict):
                url = item.get("url") or item.get("link") or item.get("href")
                # Check the explicit type/network field first.
                network = str(
                    item.get("type") or item.get("network") or ""
                ).lower()
                if url:
                    url = str(url).strip()
                    if "linkedin" in network or "linkedin" in url.lower():
                        linkedin_url = linkedin_url or url
                    elif "github" in network or "github" in url.lower():
                        github_url = github_url or url
            elif isinstance(item, str):
                item_lower = item.strip().lower()
                if "linkedin.com" in item_lower:
                    linkedin_url = linkedin_url or item.strip()
                elif "github.com" in item_lower:
                    github_url = github_url or item.strip()

        return linkedin_url, github_url

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _str_or_none(val: Any) -> str | None:
        """Return a stripped string or ``None``."""
        if val is None:
            return None
        text = str(val).strip()
        return text or None

    @staticmethod
    def _as_string_list(val: Any) -> list[str]:
        """Coerce a value into a list of non-empty strings."""
        if val is None:
            return []
        if isinstance(val, str):
            val = val.strip()
            return [val] if val else []
        if isinstance(val, list):
            result: list[str] = []
            for item in val:
                text = str(item).strip()
                if text:
                    result.append(text)
            return result
        text = str(val).strip()
        return [text] if text else []
