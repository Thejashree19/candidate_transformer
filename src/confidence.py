"""
Confidence scoring engine for canonical candidate profiles.

Computes per-field and overall confidence scores based on:
  • Source reliability weights
  • Cross-source agreement
  • Extraction quality
  • Field completeness

The scorer mutates a ``CanonicalProfile`` in-place, setting ``overall_confidence``
and per-skill confidence values, then returns the profile.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.models import (
    CanonicalProfile,
    CanonicalSkill,
    ExtractionMethod,
    ProvenanceRecord,
    SourceType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SOURCE_RELIABILITY: dict[str, float] = {
    SourceType.ATS_JSON.value: 0.95,
    SourceType.RECRUITER_CSV.value: 0.85,
    SourceType.RESUME.value: 0.85,
    SourceType.RECRUITER_NOTES.value: 0.40,
}

_SOURCE_PRIORITY_ORDER: list[str] = [
    SourceType.ATS_JSON.value,
    SourceType.RECRUITER_CSV.value,
    SourceType.RESUME.value,
    SourceType.RECRUITER_NOTES.value,
]

_EXTRACTION_QUALITY: dict[str, float] = {
    ExtractionMethod.STRUCTURED_PARSE.value: 1.0,
    ExtractionMethod.FIELD_MAPPING.value: 0.9,
    ExtractionMethod.API_FETCH.value: 0.8,
    ExtractionMethod.REGEX.value: 0.6,
    ExtractionMethod.HEURISTIC.value: 0.3,
    ExtractionMethod.INFERRED.value: 0.2,
}

# Weights for the overall confidence weighted average
_FIELD_IMPORTANCE: dict[str, float] = {
    "full_name": 2.0,
    "emails": 1.5,
    "experience": 1.5,
    "skills": 1.0,
    "phones": 0.5,
    "location": 0.5,
    "links": 0.5,
    "headline": 0.5,
    "years_experience": 0.5,
    "education": 0.5,
}


class ConfidenceScorer:
    """Computes and assigns confidence scores to a ``CanonicalProfile``.

    Usage::

        scorer = ConfidenceScorer()
        profile = scorer.score_profile(profile, source_types)
    """

    def score_profile(
        self,
        profile: CanonicalProfile,
        source_types: list[SourceType],
    ) -> CanonicalProfile:
        """Score *profile* in-place and return it.

        Parameters
        ----------
        profile:
            The canonical profile to score.
        source_types:
            The list of ``SourceType`` values that contributed to this profile.
            Used for agreement calculations.

        Returns
        -------
        CanonicalProfile
            The same profile object with confidence values populated.
        """
        provenance = profile.provenance
        source_strs = [s.value for s in source_types]

        field_scores: dict[str, float] = {}

        # Per-field confidence for scalar / array fields
        scored_fields = [
            "full_name", "emails", "phones", "location", "links",
            "headline", "years_experience", "skills", "experience", "education",
        ]

        for field_name in scored_fields:
            field_conf = self._compute_field_confidence(
                profile, field_name, provenance, source_strs,
            )
            field_scores[field_name] = field_conf

        # Per-skill confidence adjustment
        self._score_skills(profile, provenance, source_strs)

        # Overall confidence: weighted average across non-null fields
        profile.overall_confidence = self._compute_overall(profile, field_scores)

        return profile

    # ------------------------------------------------------------------
    # Per-field confidence
    # ------------------------------------------------------------------

    def _compute_field_confidence(
        self,
        profile: CanonicalProfile,
        field_name: str,
        provenance: list[ProvenanceRecord],
        source_strs: list[str],
    ) -> float:
        """Compute confidence for a single field using the formula:

        ``field_confidence = source_reliability×0.4 + agreement_bonus×0.3
                            + extraction_quality×0.2 + completeness×0.1``
        """
        # Gather provenance records for this field
        field_records = [p for p in provenance if p.field == field_name]

        # completeness: is the field present and non-empty?
        completeness = 1.0 if self._field_is_present(profile, field_name) else 0.0

        if not field_records:
            # No provenance → confidence is just completeness contribution
            return 0.0 * 0.4 + 0.0 * 0.3 + 0.0 * 0.2 + completeness * 0.1

        # source_reliability: weight of the highest-priority source that contributed
        source_reliability = self._best_source_reliability(field_records)

        # agreement_bonus: how many distinct sources confirm this field?
        distinct_sources = {r.source for r in field_records}
        if len(distinct_sources) >= 2:
            agreement_bonus = 1.0
        elif len(distinct_sources) == 1:
            agreement_bonus = 0.5
        else:
            agreement_bonus = 0.0

        # extraction_quality: best extraction method among contributing records
        extraction_quality = self._best_extraction_quality(field_records)

        confidence = (
            source_reliability * 0.4
            + agreement_bonus * 0.3
            + extraction_quality * 0.2
            + completeness * 0.1
        )
        return min(max(confidence, 0.0), 1.0)

    # ------------------------------------------------------------------
    # Skill confidence
    # ------------------------------------------------------------------

    def _score_skills(
        self,
        profile: CanonicalProfile,
        provenance: list[ProvenanceRecord],
        source_strs: list[str],
    ) -> None:
        """Adjust each ``CanonicalSkill.confidence`` by source reliability and agreement."""
        skill_prov = [p for p in provenance if p.field == "skills"]

        for skill in profile.skills:
            # Source reliability: best source that reported this skill
            skill_sources = skill.sources
            best_reliability = 0.0
            for src in skill_sources:
                rel = _SOURCE_RELIABILITY.get(src, 0.3)
                if rel > best_reliability:
                    best_reliability = rel

            # Agreement bonus
            n_sources = len(skill_sources)
            if n_sources >= 2:
                agreement = 1.0
            elif n_sources == 1:
                agreement = 0.5
            else:
                agreement = 0.0

            # Scale existing confidence from canonicalization
            base = skill.confidence if skill.confidence > 0 else 0.5
            scaled = base * (best_reliability * 0.6 + agreement * 0.4)
            skill.confidence = round(min(max(scaled, 0.0), 1.0), 4)

    # ------------------------------------------------------------------
    # Overall confidence
    # ------------------------------------------------------------------

    def _compute_overall(
        self,
        profile: CanonicalProfile,
        field_scores: dict[str, float],
    ) -> float:
        """Weighted average of per-field confidences, weighted by field importance."""
        total_weight = 0.0
        weighted_sum = 0.0

        for field_name, score in field_scores.items():
            if not self._field_is_present(profile, field_name):
                continue
            importance = _FIELD_IMPORTANCE.get(field_name, 0.5)
            weighted_sum += score * importance
            total_weight += importance

        if total_weight == 0.0:
            return 0.0

        overall = weighted_sum / total_weight
        return round(min(max(overall, 0.0), 1.0), 4)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _field_is_present(profile: CanonicalProfile, field_name: str) -> bool:
        """Check whether a field on the profile is present and non-empty."""
        val = getattr(profile, field_name, None)
        if val is None:
            return False
        if isinstance(val, str):
            return bool(val.strip())
        if isinstance(val, list):
            return len(val) > 0
        if isinstance(val, (int, float)):
            return True
        # Pydantic model (Location, Links) — check if any sub-field is set
        if hasattr(val, "model_fields"):
            for sub_field in val.model_fields:
                sub_val = getattr(val, sub_field, None)
                if sub_val is not None:
                    if isinstance(sub_val, str) and sub_val.strip():
                        return True
                    if isinstance(sub_val, list) and len(sub_val) > 0:
                        return True
                    if isinstance(sub_val, (int, float)):
                        return True
            return False
        return bool(val)

    @staticmethod
    def _best_source_reliability(records: list[ProvenanceRecord]) -> float:
        """Return the highest reliability among sources in provenance records."""
        best = 0.0
        for r in records:
            rel = _SOURCE_RELIABILITY.get(r.source, 0.3)
            if rel > best:
                best = rel
        return best

    @staticmethod
    def _best_extraction_quality(records: list[ProvenanceRecord]) -> float:
        """Return the highest extraction quality among methods in provenance records."""
        best = 0.0
        for r in records:
            q = _EXTRACTION_QUALITY.get(r.method, 0.2)
            if q > best:
                best = q
        return best
