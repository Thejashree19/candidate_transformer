"""
Skill canonicalizer.

Resolves raw skill strings to a canonical name using a four-layer
matching strategy:

1. **Exact** — case-insensitive match against the canonical skill list.
2. **Synonym** — lookup in the synonym map loaded from configuration.
3. **Fuzzy** — ``rapidfuzz.fuzz.WRatio`` with a threshold of 85.
4. **Unmatched** — return the original string with low confidence.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

# -----------------------------------------------------------------------
# Default config path (relative to project root)
# -----------------------------------------------------------------------
_DEFAULT_CONFIG_PATH: str = str(
    Path(__file__).resolve().parents[2] / "config" / "skill_synonyms.json"
)

_FUZZY_THRESHOLD: float = 85.0


# -----------------------------------------------------------------------
# Loading helpers
# -----------------------------------------------------------------------

def load_skill_synonyms(
    config_path: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Load the canonical skill list and synonym map from a JSON file.

    Parameters
    ----------
    config_path:
        Absolute or relative path to the JSON config.  Falls back to
        ``config/skill_synonyms.json`` relative to the project root.

    Returns
    -------
    tuple[list[str], dict[str, str]]
        ``(canonical_skills, synonym_map)`` — both default to empty
        collections if the file is missing or malformed.
    """
    path = config_path or _DEFAULT_CONFIG_PATH

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return [], {}

    canonical: list[str] = data.get("canonical_skills", [])
    synonyms: dict[str, str] = data.get("synonym_map", {})
    return canonical, synonyms


# -----------------------------------------------------------------------
# Module-level canonical data (loaded once on import)
# -----------------------------------------------------------------------

_CANONICAL_SKILLS, _SYNONYM_MAP = load_skill_synonyms()

# O(1) lowercase → original-case lookup.
_CANONICAL_LOWER: dict[str, str] = {s.lower(): s for s in _CANONICAL_SKILLS}


def _refresh_canonical_cache(
    canonical: list[str],
    synonyms: dict[str, str],
) -> None:
    """Refresh module-level caches (useful after reloading config)."""
    global _CANONICAL_SKILLS, _SYNONYM_MAP, _CANONICAL_LOWER  # noqa: PLW0603
    _CANONICAL_SKILLS = canonical
    _SYNONYM_MAP = synonyms
    _CANONICAL_LOWER = {s.lower(): s for s in canonical}


# -----------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------

def canonicalize_skill(
    raw: str,
    synonyms: dict[str, str] | None = None,
) -> tuple[str, float, str]:
    """Canonicalize a single skill string.

    Parameters
    ----------
    raw:
        The raw skill name (e.g. ``"reactjs"``, ``"Machine Learning"``).
    synonyms:
        Optional override synonym map.  When ``None`` the module-level
        map loaded from ``skill_synonyms.json`` is used.

    Returns
    -------
    tuple[str, float, str]
        ``(canonical_name, confidence, method)``

        * **method** is one of ``"exact"``, ``"synonym"``, ``"fuzzy"``,
          or ``"unmatched"``.
        * **confidence** ranges from ``0.0`` to ``1.0``.
    """
    if not raw or not isinstance(raw, str):
        return raw or "", 0.0, "unmatched"

    cleaned = raw.strip()
    if not cleaned:
        return cleaned, 0.0, "unmatched"

    lower = cleaned.lower()
    syn_map = synonyms if synonyms is not None else _SYNONYM_MAP

    # ------------------------------------------------------------------
    # Layer 1: Exact match against canonical list (case-insensitive).
    # ------------------------------------------------------------------
    if lower in _CANONICAL_LOWER:
        return _CANONICAL_LOWER[lower], 1.0, "exact"

    # ------------------------------------------------------------------
    # Layer 2: Synonym map lookup.
    # ------------------------------------------------------------------
    # The synonym_map keys in the JSON are already lowercase.
    if lower in syn_map:
        return syn_map[lower], 1.0, "synonym"

    # ------------------------------------------------------------------
    # Layer 3: Fuzzy matching (RapidFuzz WRatio).
    # ------------------------------------------------------------------
    if _CANONICAL_SKILLS:
        result = process.extractOne(
            cleaned,
            _CANONICAL_SKILLS,
            scorer=fuzz.WRatio,
            score_cutoff=_FUZZY_THRESHOLD,
        )
        if result is not None:
            match_name, score, _idx = result
            return match_name, round(score / 100.0, 4), "fuzzy"

    # ------------------------------------------------------------------
    # Layer 4: Unmatched — return original with low confidence.
    # ------------------------------------------------------------------
    return cleaned, 0.4, "unmatched"


def canonicalize_skills(skills: list[str]) -> list[dict[str, Any]]:
    """Canonicalize a batch of skill strings.

    Parameters
    ----------
    skills:
        List of raw skill name strings.

    Returns
    -------
    list[dict]
        Each dict contains ``{"name": str, "confidence": float,
        "method": str}``.
    """
    results: list[dict[str, Any]] = []
    for skill in skills:
        name, confidence, method = canonicalize_skill(skill)
        results.append({
            "name": name,
            "confidence": confidence,
            "method": method,
        })
    return results
