"""
Extractor for LinkedIn profiles.

Simulates fetching a LinkedIn profile via a URL. Due to anti-scraping
measures, this implementation relies on a cached JSON response provided
in the SourceEnvelope. If no cached data is found for the given URL,
it fails gracefully.
"""

from __future__ import annotations

import re
from typing import Any

from src.models import (
    Education,
    Experience,
    ExtractionMethod,
    RawCandidate,
    RawEducation,
    RawExperience,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)
from .base import BaseExtractor


class LinkedInExtractor(BaseExtractor):
    """Fetch a LinkedIn profile (via cache) and derive a :class:`RawCandidate`."""

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract a single candidate from a LinkedIn profile.

        ``envelope.raw_data`` should be a dict containing the cached profile data.
        If it's just a string URL and not in the cache (which would be pre-resolved
        by the pipeline), it will return an empty list.
        """
        raw = envelope.raw_data

        if not isinstance(raw, dict):
            # If we only have a string URL and no cache hit, we can't scrape LinkedIn live.
            envelope.status = SourceStatus.FAILED
            envelope.error_message = (
                "No cached data found for LinkedIn URL. Live scraping is disabled."
            )
            return []

        # We have a cached JSON dictionary
        profile = raw

        candidate = self._build_candidate(profile)
        return [candidate] if candidate else []

    def _build_candidate(self, profile: dict[str, Any]) -> RawCandidate | None:
        """Map cached LinkedIn JSON to RawCandidate."""
        
        def _pstr(key: str) -> str | None:
            val = profile.get(key)
            if val is None:
                return None
            text = str(val).strip()
            return text or None

        full_name = _pstr("name")
        headline = _pstr("headline")
        linkedin_url = _pstr("url")
        location = _pstr("location")
        
        # Build experience
        raw_experience: list[RawExperience] = []
        for exp in profile.get("experience", []):
            if not isinstance(exp, dict):
                continue
            
            raw_exp = RawExperience(
                company=exp.get("company"),
                title=exp.get("title"),
                start=exp.get("start"),
                end=exp.get("end"),
                summary=exp.get("description"),
                source=SourceType.LINKEDIN,
                method=ExtractionMethod.API_FETCH,
            )
            raw_experience.append(raw_exp)

        # Build education
        raw_education: list[RawEducation] = []
        for edu in profile.get("education", []):
            if not isinstance(edu, dict):
                continue
            
            end_year = None
            if edu.get("end_year"):
                try:
                    end_year = int(edu["end_year"])
                except ValueError:
                    pass

            raw_edu = RawEducation(
                institution=edu.get("school"),
                degree=edu.get("degree"),
                field=edu.get("field"),
                end_year=end_year,
                source=SourceType.LINKEDIN,
                method=ExtractionMethod.API_FETCH,
            )
            raw_education.append(raw_edu)

        # Current company and title derived from the most recent experience
        current_company = None
        current_title = None
        if raw_experience:
            # Assume first is current if 'end' is None or not provided
            first_exp = raw_experience[0]
            if not first_exp.end:
                current_company = first_exp.company
                current_title = first_exp.title

        return RawCandidate(
            full_name=full_name,
            headline=headline,
            location_raw=location,
            linkedin_url=linkedin_url,
            experience=raw_experience,
            education=raw_education,
            current_company=current_company,
            current_title=current_title,
            source_type=SourceType.LINKEDIN,
            extraction_method=ExtractionMethod.API_FETCH,
        )
