"""
Extractor for free-text recruiter notes.

Uses regex and heuristic pattern matching to locate candidate information
in unstructured text.  Supports multi-candidate notes separated by ``---``
or double blank-line boundaries.
"""

from __future__ import annotations

import re
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

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level for performance)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", re.IGNORECASE
)

_PHONE_RE = re.compile(
    r"""
    (?<!\d)                       # not preceded by a digit
    (?:
        \+?\d{1,3}[\s.-]?        # optional country code
    )?
    \(?\d{2,4}\)?[\s.-]?         # area code
    \d{3,4}[\s.-]?               # exchange
    \d{3,4}                      # subscriber
    (?!\d)                        # not followed by a digit
    """,
    re.VERBOSE,
)

# Name extraction heuristics.
_NAME_PATTERNS = [
    re.compile(r"(?:candidate|name)\s*:\s*(.+)", re.IGNORECASE),
    re.compile(r"spoke\s+with\s+(.+?)(?:\.|,|;|\s+about|\s+regarding|$)", re.IGNORECASE),
    re.compile(r"interviewed\s+(.+?)(?:\.|,|;|\s+for|$)", re.IGNORECASE),
    re.compile(r"met\s+with\s+(.+?)(?:\.|,|;|\s+about|\s+regarding|$)", re.IGNORECASE),
]

# Skills patterns.
_SKILLS_PATTERNS = [
    re.compile(
        r"(?:skills|technologies|tech\s*stack)\s*:\s*(.+?)(?:\n|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:experienced|proficient|expertise|skilled)\s+in\s+(.+?)(?:\.|;|\n|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"knows\s+(.+?)(?:\.|;|\n|$)", re.IGNORECASE
    ),
]

# Company patterns.
_COMPANY_PATTERNS = [
    re.compile(
        r"(?:works?\s+at|currently\s+at|employed\s+at|company)\s*:?\s*(.+?)(?:\.|,|;|\n|$)",
        re.IGNORECASE,
    ),
]

# Title patterns.
_TITLE_PATTERNS = [
    re.compile(
        r"(?:title|role|position)\s*:\s*(.+?)(?:\.|,|;|\n|$)", re.IGNORECASE
    ),
    re.compile(
        r"works?\s+as\s+(?:a\s+|an\s+)?(.+?)(?:\.|,|;|\n|$)", re.IGNORECASE
    ),
]

# Years of experience.
_YEARS_EXP_RE = re.compile(
    r"(\d+)\+?\s*(?:years?|yrs?)(?:\s+of)?\s*(?:experience|exp)?",
    re.IGNORECASE,
)

# Education patterns.
_EDUCATION_PATTERNS = [
    # "BS in Computer Science from MIT"
    re.compile(
        r"(BS|BA|MS|MA|MBA|PhD|Ph\.?D|B\.?S|M\.?S|B\.?A|M\.?A)\s+in\s+(.+?)\s+from\s+(.+?)(?:\.|,|;|\n|$)",
        re.IGNORECASE,
    ),
    # "BS from MIT"
    re.compile(
        r"(BS|BA|MS|MA|MBA|PhD|Ph\.?D|B\.?S|M\.?S|B\.?A|M\.?A)\s+from\s+(.+?)(?:\.|,|;|\n|$)",
        re.IGNORECASE,
    ),
    # "degree in X from Y"
    re.compile(
        r"degree\s+in\s+(.+?)\s+from\s+(.+?)(?:\.|,|;|\n|$)",
        re.IGNORECASE,
    ),
    # "graduated from X"
    re.compile(
        r"graduated\s+from\s+(.+?)(?:\.|,|;|\n|$)", re.IGNORECASE
    ),
]

# Location patterns.
_LOCATION_PATTERNS = [
    re.compile(
        r"(?:based|located|lives?)\s+in\s+(.+?)(?:\.|,|;|\n|$)", re.IGNORECASE
    ),
    re.compile(
        r"(?:location|from)\s*:\s*(.+?)(?:\.|,|;|\n|$)", re.IGNORECASE
    ),
]

# Section separator for multi-candidate notes.
_SECTION_SPLIT_RE = re.compile(r"(?:\n\s*---+\s*\n|\n{3,})")


class NotesExtractor(BaseExtractor):
    """Parse free-text recruiter notes into :class:`RawCandidate` records."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract one or more candidates from recruiter note text.

        Args:
            envelope: Source envelope whose ``raw_data`` is a text string.

        Returns:
            A list of :class:`RawCandidate` instances.
        """
        text = self._get_text(envelope.raw_data)
        if not text:
            envelope.status = SourceStatus.EMPTY
            return []

        sections = self._split_sections(text)
        candidates: list[RawCandidate] = []
        for section in sections:
            candidate = self._parse_section(section)
            if candidate is not None:
                candidates.append(candidate)

        if not candidates:
            envelope.status = SourceStatus.EMPTY

        return candidates

    # ------------------------------------------------------------------
    # Text normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _get_text(raw: Any) -> str:
        """Coerce raw_data to a stripped string."""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace").strip()
        return ""

    @staticmethod
    def _split_sections(text: str) -> list[str]:
        """Split on ``---`` dividers or triple-newlines."""
        parts = _SECTION_SPLIT_RE.split(text)
        return [p.strip() for p in parts if p.strip()]

    # ------------------------------------------------------------------
    # Section parsing
    # ------------------------------------------------------------------

    def _parse_section(self, text: str) -> RawCandidate | None:
        """Parse a single section of recruiter notes.

        Returns ``None`` if the section contains no useful data at all.
        """
        emails = self._extract_emails(text)
        phones = self._extract_phones(text)
        name = self._extract_name(text)
        skills = self._extract_skills(text)
        company = self._extract_company(text)
        title = self._extract_title(text)
        years = self._extract_years(text)
        education = self._extract_education(text)
        location = self._extract_location(text)

        # Require at least *some* identifiable data.
        has_identity = bool(name or emails)
        has_details = bool(
            skills or company or title or phones or education or location
        )
        if not has_identity and not has_details:
            return None

        return RawCandidate(
            full_name=name,
            emails=emails,
            phones=phones,
            location_raw=location,
            current_company=company,
            current_title=title,
            skills=skills,
            years_experience=years,
            education=education,
            source_type=SourceType.RECRUITER_NOTES,
            extraction_method=ExtractionMethod.REGEX,
        )

    # ------------------------------------------------------------------
    # Individual field extractors
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_emails(text: str) -> list[str]:
        """Find all email addresses in *text*."""
        return list(dict.fromkeys(_EMAIL_RE.findall(text)))  # dedupe, preserve order

    @staticmethod
    def _extract_phones(text: str) -> list[str]:
        """Find all phone numbers in *text*."""
        matches = _PHONE_RE.findall(text)
        # Deduplicate while preserving order.
        seen: set[str] = set()
        result: list[str] = []
        for m in matches:
            normalized = re.sub(r"\s+", "", m)
            if normalized not in seen:
                seen.add(normalized)
                result.append(m.strip())
        return result

    @staticmethod
    def _extract_name(text: str) -> str | None:
        """Heuristically extract a candidate name from *text*.

        Tries explicit patterns first, then falls back to the first line
        if it looks like a name (short, capitalized, no special chars).
        """
        for pattern in _NAME_PATTERNS:
            match = pattern.search(text)
            if match:
                name = match.group(1).strip().rstrip(".,;:")
                if name and len(name) < 80:
                    return name

        # Fallback: first non-empty line as a name if it looks plausible.
        first_line = text.split("\n", 1)[0].strip()
        if (
            first_line
            and len(first_line) < 60
            and not _EMAIL_RE.search(first_line)
            and not first_line.startswith(("http", "www", "#", "*"))
            and re.match(r"^[A-Z][a-zA-Z\s\.\'-]+$", first_line)
        ):
            return first_line

        return None

    @staticmethod
    def _extract_skills(text: str) -> list[RawSkill]:
        """Extract skill names from *text* via regex patterns."""
        raw_skills: list[str] = []

        for pattern in _SKILLS_PATTERNS:
            for match in pattern.finditer(text):
                chunk = match.group(1).strip()
                # Split on common delimiters: comma, semicolon, "and".
                parts = re.split(r"[,;]|\band\b", chunk)
                for part in parts:
                    part = part.strip().rstrip(".,;:")
                    if part and len(part) < 50:
                        raw_skills.append(part)

        # Deduplicate (case-insensitive) while preserving order.
        seen: set[str] = set()
        skills: list[RawSkill] = []
        for name in raw_skills:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                skills.append(
                    RawSkill(
                        name=name,
                        source=SourceType.RECRUITER_NOTES,
                        method=ExtractionMethod.REGEX,
                    )
                )
        return skills

    @staticmethod
    def _extract_company(text: str) -> str | None:
        """Extract the current company name from *text*."""
        for pattern in _COMPANY_PATTERNS:
            match = pattern.search(text)
            if match:
                company = match.group(1).strip().rstrip(".,;:")
                if company and len(company) < 80:
                    return company
        return None

    @staticmethod
    def _extract_title(text: str) -> str | None:
        """Extract the job title from *text*."""
        for pattern in _TITLE_PATTERNS:
            match = pattern.search(text)
            if match:
                title = match.group(1).strip().rstrip(".,;:")
                if title and len(title) < 80:
                    return title
        return None

    @staticmethod
    def _extract_years(text: str) -> float | None:
        """Extract years of experience from *text*."""
        match = _YEARS_EXP_RE.search(text)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                return None
        return None

    @staticmethod
    def _extract_education(text: str) -> list[RawEducation]:
        """Extract education entries from *text*."""
        results: list[RawEducation] = []

        for pattern in _EDUCATION_PATTERNS:
            for match in pattern.finditer(text):
                groups = match.groups()
                institution: str | None = None
                degree: str | None = None
                field: str | None = None

                if len(groups) == 3:
                    # "BS in CS from MIT"
                    degree = groups[0].strip()
                    field = groups[1].strip()
                    institution = groups[2].strip().rstrip(".,;:")
                elif len(groups) == 2:
                    # Could be "BS from MIT" or "degree in CS from MIT"
                    first = groups[0].strip()
                    second = groups[1].strip().rstrip(".,;:")
                    # Heuristic: if first group looks like a degree abbreviation
                    if re.match(
                        r"^(?:BS|BA|MS|MA|MBA|PhD|Ph\.?D|B\.?S|M\.?S|B\.?A|M\.?A)$",
                        first,
                        re.IGNORECASE,
                    ):
                        degree = first
                        institution = second
                    else:
                        field = first
                        institution = second
                elif len(groups) == 1:
                    # "graduated from X"
                    institution = groups[0].strip().rstrip(".,;:")

                if institution or degree or field:
                    results.append(
                        RawEducation(
                            institution=institution,
                            degree=degree,
                            field=field,
                            source=SourceType.RECRUITER_NOTES,
                            method=ExtractionMethod.REGEX,
                        )
                    )

        return results

    @staticmethod
    def _extract_location(text: str) -> str | None:
        """Extract the candidate's location from *text*."""
        for pattern in _LOCATION_PATTERNS:
            match = pattern.search(text)
            if match:
                location = match.group(1).strip().rstrip(".,;:")
                if location and len(location) < 80:
                    return location
        return None
