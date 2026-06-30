"""
Core data models for the Multi-Source Candidate Data Transformer.

All Pydantic models that define the canonical schema, intermediate representations,
source envelopes, configuration, and output types.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SourceType(str, Enum):
    """Enumeration of supported data source types."""
    RECRUITER_CSV = "recruiter_csv"
    ATS_JSON = "ats_json"
    GITHUB = "github"
    RECRUITER_NOTES = "recruiter_notes"
    LINKEDIN = "linkedin"
    RESUME = "resume"


class SourceStatus(str, Enum):
    """Status of a source after ingestion attempt."""
    OK = "ok"
    EMPTY = "empty"
    MALFORMED = "malformed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractionMethod(str, Enum):
    """How a value was extracted from a source."""
    STRUCTURED_PARSE = "structured_parse"
    FIELD_MAPPING = "field_mapping"
    API_FETCH = "api_fetch"
    REGEX = "regex"
    HEURISTIC = "heuristic"
    INFERRED = "inferred"


class OnMissing(str, Enum):
    """Behavior when a required field is missing in output projection."""
    NULL = "null"
    OMIT = "omit"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Source Envelope
# ---------------------------------------------------------------------------

class SourceEnvelope(BaseModel):
    """Wraps raw input from a single source with metadata."""
    source_type: SourceType
    path: str = ""
    status: SourceStatus = SourceStatus.OK
    error_message: Optional[str] = None
    raw_data: Any = None


# ---------------------------------------------------------------------------
# Intermediate / Raw Candidate (per-source extraction result)
# ---------------------------------------------------------------------------

class RawSkill(BaseModel):
    """A skill extracted from a single source, before canonicalization."""
    name: str
    source: SourceType
    method: ExtractionMethod = ExtractionMethod.STRUCTURED_PARSE


class RawExperience(BaseModel):
    """A single work experience entry from a source."""
    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = None  # Will be normalized to YYYY-MM
    end: Optional[str] = None    # Will be normalized to YYYY-MM or null (current)
    summary: Optional[str] = None
    source: SourceType = SourceType.RECRUITER_CSV
    method: ExtractionMethod = ExtractionMethod.STRUCTURED_PARSE


class RawEducation(BaseModel):
    """A single education entry from a source."""
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None
    source: SourceType = SourceType.RECRUITER_CSV
    method: ExtractionMethod = ExtractionMethod.STRUCTURED_PARSE


class RawCandidate(BaseModel):
    """
    Intermediate representation of a candidate from a single source.
    All fields are optional — a source may only provide a subset.
    """
    full_name: Optional[str] = None
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    location_raw: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    other_links: list[str] = Field(default_factory=list)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list[RawSkill] = Field(default_factory=list)
    experience: list[RawExperience] = Field(default_factory=list)
    education: list[RawEducation] = Field(default_factory=list)
    current_company: Optional[str] = None
    current_title: Optional[str] = None

    # Provenance
    source_type: SourceType
    extraction_method: ExtractionMethod = ExtractionMethod.STRUCTURED_PARSE


# ---------------------------------------------------------------------------
# Canonical Output Schema
# ---------------------------------------------------------------------------

class Location(BaseModel):
    """Normalized location with ISO 3166 alpha-2 country code."""
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None  # ISO 3166 alpha-2


class Links(BaseModel):
    """Candidate's web presence."""
    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: list[str] = Field(default_factory=list)


class CanonicalSkill(BaseModel):
    """A canonicalized skill with confidence and source provenance."""
    name: str
    confidence: float = 0.0
    sources: list[str] = Field(default_factory=list)


class Experience(BaseModel):
    """A normalized work experience entry."""
    company: str
    title: str
    start: Optional[str] = None   # YYYY-MM
    end: Optional[str] = None     # YYYY-MM or null for current
    summary: Optional[str] = None


class Education(BaseModel):
    """A normalized education entry."""
    institution: str
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = None


class ProvenanceRecord(BaseModel):
    """Tracks where a specific field value came from."""
    field: str
    source: str
    method: str


class CanonicalProfile(BaseModel):
    """
    The final, merged canonical profile for a single candidate.
    This is the internal "truth" record before any output projection.
    """
    candidate_id: str = ""
    full_name: str = ""
    emails: list[str] = Field(default_factory=list)
    phones: list[str] = Field(default_factory=list)
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: list[CanonicalSkill] = Field(default_factory=list)
    experience: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    overall_confidence: float = 0.0

    def generate_candidate_id(self) -> str:
        """Generate a deterministic UUID v5 from the primary email or name."""
        namespace = uuid.UUID("a3bb189e-8bf9-3888-9912-ace4e6543002")
        if self.emails:
            seed = self.emails[0].lower().strip()
        elif self.full_name:
            seed = self.full_name.lower().strip()
        else:
            seed = "unknown"
        return str(uuid.uuid5(namespace, seed))


# ---------------------------------------------------------------------------
# Runtime Output Configuration
# ---------------------------------------------------------------------------

class FieldConfig(BaseModel):
    """Configuration for a single output field."""
    path: str                          # Output field name
    from_path: Optional[str] = Field(default=None, alias="from")
    type: str = "string"               # "string", "string[]", "number", "object"
    required: bool = False
    normalize: Optional[str] = None    # "E164", "canonical", "YYYY-MM", etc.

    model_config = {"populate_by_name": True}


class OutputConfig(BaseModel):
    """
    Runtime configuration that reshapes the canonical output.
    Enables field selection, renaming, normalization toggles,
    and missing-value behavior — no code changes needed.
    """
    fields: list[FieldConfig] = Field(default_factory=list)
    include_confidence: bool = True
    include_provenance: bool = True
    on_missing: OnMissing = OnMissing.NULL

    @classmethod
    def default(cls) -> "OutputConfig":
        """Return a config that outputs the full canonical schema."""
        return cls(
            fields=[],  # Empty means "output everything"
            include_confidence=True,
            include_provenance=True,
            on_missing=OnMissing.NULL,
        )


# ---------------------------------------------------------------------------
# Pipeline Result
# ---------------------------------------------------------------------------

class PipelineResult(BaseModel):
    """Result of running the full pipeline on a set of inputs."""
    profiles: list[dict] = Field(default_factory=list)
    source_statuses: list[SourceEnvelope] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
