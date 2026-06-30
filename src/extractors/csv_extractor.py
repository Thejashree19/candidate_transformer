"""
Extractor for recruiter-supplied CSV files.

Expected columns (all optional — missing columns are handled gracefully):
  name, email, phone, current_company, title, location, skills,
  linkedin_url, github_url, years_experience, education

Skills are semicolon-separated (e.g. ``Python;JavaScript;ML``).
"""

from __future__ import annotations

import csv
import io
from typing import Any

from src.models import (
    ExtractionMethod,
    RawCandidate,
    RawEducation,
    RawSkill,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)

from .base import BaseExtractor


class CSVExtractor(BaseExtractor):
    """Parse a recruiter CSV into :class:`RawCandidate` records."""

    # Mapping from CSV column names to internal attribute names.
    # Extend this if vendor CSVs use slightly different headers.
    _COLUMN_MAP: dict[str, str] = {
        "name": "full_name",
        "email": "email",
        "phone": "phone",
        "current_company": "current_company",
        "title": "current_title",
        "location": "location_raw",
        "skills": "skills",
        "linkedin_url": "linkedin_url",
        "github_url": "github_url",
        "years_experience": "years_experience",
        "education": "education",
    }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Parse the CSV content stored in *envelope.raw_data*.

        ``raw_data`` may be a string (CSV text) or a ``list[dict]``
        already parsed externally.

        Args:
            envelope: Source envelope whose ``raw_data`` holds CSV content.

        Returns:
            A list of :class:`RawCandidate` instances.
        """
        raw = envelope.raw_data
        rows = self._to_rows(raw)

        if not rows:
            envelope.status = SourceStatus.EMPTY
            return []

        candidates: list[RawCandidate] = []
        for row in rows:
            candidate = self._row_to_candidate(row)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            envelope.status = SourceStatus.EMPTY

        return candidates

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_rows(raw: Any) -> list[dict[str, str]]:
        """Convert *raw* into a list of row dicts.

        Handles three input shapes:
        * ``str`` — CSV text parsed with :class:`csv.DictReader`.
        * ``list[dict]`` — already parsed rows.
        * Anything else — returns an empty list.
        """
        if isinstance(raw, str):
            reader = csv.DictReader(io.StringIO(raw))
            return list(reader)
        if isinstance(raw, list) and all(isinstance(r, dict) for r in raw):
            return raw  # type: ignore[return-value]
        return []

    def _row_to_candidate(self, row: dict[str, str]) -> RawCandidate | None:
        """Map a single CSV row dict to a :class:`RawCandidate`.

        Returns ``None`` when both *name* and *email* are empty/missing.
        """

        def _get(col: str) -> str:
            """Safely retrieve and strip a column value."""
            val = row.get(col, "")
            if val is None:
                return ""
            return str(val).strip()

        name = _get("name")
        email = _get("email")

        # Skip rows where both identifiers are absent.
        if not name and not email:
            return None

        # Parse semicolon-separated skills.
        skills_raw = _get("skills")
        skills: list[RawSkill] = []
        if skills_raw:
            for skill_name in skills_raw.split(";"):
                skill_name = skill_name.strip()
                if skill_name:
                    skills.append(
                        RawSkill(
                            name=skill_name,
                            source=SourceType.RECRUITER_CSV,
                            method=ExtractionMethod.STRUCTURED_PARSE,
                        )
                    )

        # Parse years_experience safely.
        years_experience: float | None = None
        yoe_str = _get("years_experience")
        if yoe_str:
            try:
                years_experience = float(yoe_str)
            except (ValueError, TypeError):
                years_experience = None

        # Parse education (plain string → single RawEducation with institution).
        education: list[RawEducation] = []
        edu_str = _get("education")
        if edu_str:
            education.append(
                RawEducation(
                    institution=edu_str,
                    source=SourceType.RECRUITER_CSV,
                    method=ExtractionMethod.STRUCTURED_PARSE,
                )
            )

        # Build emails / phones lists.
        emails: list[str] = [email] if email else []
        phone = _get("phone")
        phones: list[str] = [phone] if phone else []

        return RawCandidate(
            full_name=name or None,
            emails=emails,
            phones=phones,
            current_company=_get("current_company") or None,
            current_title=_get("title") or None,
            location_raw=_get("location") or None,
            linkedin_url=_get("linkedin_url") or None,
            github_url=_get("github_url") or None,
            skills=skills,
            years_experience=years_experience,
            education=education,
            source_type=SourceType.RECRUITER_CSV,
            extraction_method=ExtractionMethod.STRUCTURED_PARSE,
        )
