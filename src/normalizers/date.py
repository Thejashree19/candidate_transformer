"""
Date normalizer.

Converts a variety of date string formats into the canonical ``YYYY-MM``
representation used throughout the pipeline.
"""

from __future__ import annotations

import re

from dateutil import parser as dateutil_parser


# Tokens that mean "still employed" — no date to return.
_CURRENT_TOKENS: set[str] = {
    "present",
    "current",
    "now",
    "ongoing",
    "today",
}

# Matches a bare 4-digit year like "2020".
_YEAR_ONLY_RE = re.compile(r"^\s*(\d{4})\s*$")

# Matches MM/YYYY or MM-YYYY (but not a full date like 01/15/2020).
_MONTH_YEAR_SLASH_RE = re.compile(r"^\s*(\d{1,2})[/\-](\d{4})\s*$")


def normalize_date(raw: str) -> str | None:
    """Normalize a raw date string to ``YYYY-MM`` format.

    Handles the following patterns (among others):

    * ``"Jan 2020"`` / ``"January 2020"``
    * ``"2020-01-15"`` (day portion is discarded)
    * ``"01/2020"``
    * ``"2020"`` (year-only → ``"2020-01"``)
    * ``"Present"`` / ``"Current"`` → ``None``

    Parameters
    ----------
    raw:
        The date string to normalize.

    Returns
    -------
    str | None
        A ``YYYY-MM`` string, or ``None`` if the input represents an
        ongoing/current position or cannot be parsed.
    """
    if not raw or not isinstance(raw, str):
        return None

    cleaned = raw.strip()
    if not cleaned:
        return None

    # ------------------------------------------------------------------
    # "Present", "Current", etc.
    # ------------------------------------------------------------------
    if cleaned.lower() in _CURRENT_TOKENS:
        return None

    # ------------------------------------------------------------------
    # Year-only: "2020" → "2020-01"
    # ------------------------------------------------------------------
    m = _YEAR_ONLY_RE.match(cleaned)
    if m:
        year = int(m.group(1))
        if 1900 <= year <= 2100:
            return f"{year:04d}-01"
        return None

    # ------------------------------------------------------------------
    # MM/YYYY or MM-YYYY: "01/2020" → "2020-01"
    # ------------------------------------------------------------------
    m = _MONTH_YEAR_SLASH_RE.match(cleaned)
    if m:
        month, year = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1900 <= year <= 2100:
            return f"{year:04d}-{month:02d}"
        return None

    # ------------------------------------------------------------------
    # General parsing via python-dateutil.
    # ------------------------------------------------------------------
    try:
        dt = dateutil_parser.parse(cleaned, dayfirst=False, fuzzy=True)
        return f"{dt.year:04d}-{dt.month:02d}"
    except (ValueError, OverflowError, TypeError):
        return None
