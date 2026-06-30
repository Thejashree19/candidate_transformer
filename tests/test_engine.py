"""
Unit tests for the core engine modules.

Tests the merger (matching + merging), confidence scorer,
output projector, and validator.
"""

import pytest

from src.models import (
    CanonicalProfile,
    CanonicalSkill,
    Education,
    Experience,
    ExtractionMethod,
    FieldConfig,
    Links,
    Location,
    OnMissing,
    OutputConfig,
    PhoneEntry,
    ProvenanceRecord,
    RawCandidate,
    RawEducation,
    RawExperience,
    RawSkill,
    SourceType,
)
from src.merger import CandidateMerger
from src.confidence import ConfidenceScorer
from src.projection import OutputProjector
from src.validator import OutputValidator


# ═══════════════════════════════════════════════════════════════════════
# Merger
# ═══════════════════════════════════════════════════════════════════════

class TestCandidateMerger:
    """Tests for candidate matching and merging."""

    def _make_candidate(self, **kwargs) -> RawCandidate:
        defaults = {
            "source_type": SourceType.RECRUITER_CSV,
            "extraction_method": ExtractionMethod.STRUCTURED_PARSE,
        }
        defaults.update(kwargs)
        return RawCandidate(**defaults)

    def test_single_candidate_passthrough(self):
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
            )
        ]
        profiles = merger.match_and_merge(candidates)
        assert len(profiles) == 1
        assert profiles[0].full_name == "Alice"
        assert "alice@test.com" in profiles[0].emails

    def test_merge_by_email(self):
        """Two candidates with the same email should merge into one."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice Johnson",
                emails=["alice@test.com"],
                source_type=SourceType.ATS_JSON,
                extraction_method=ExtractionMethod.FIELD_MAPPING,
            ),
            self._make_candidate(
                full_name="Alice J.",
                emails=["alice@test.com"],
                phones=["(650) 555-0101"],
                source_type=SourceType.RECRUITER_CSV,
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert len(profiles) == 1
        # ATS has higher priority, so name should come from ATS
        assert profiles[0].full_name == "Alice Johnson"

    def test_no_merge_different_emails(self):
        """Two candidates with different emails should stay separate."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
            ),
            self._make_candidate(
                full_name="Bob",
                emails=["bob@test.com"],
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert len(profiles) == 2

    def test_skill_canonicalization(self):
        """Skills should be canonicalized during merge."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                skills=[
                    RawSkill(name="JS", source=SourceType.RECRUITER_CSV,
                             method=ExtractionMethod.STRUCTURED_PARSE),
                    RawSkill(name="Python", source=SourceType.RECRUITER_CSV,
                             method=ExtractionMethod.STRUCTURED_PARSE),
                ],
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        skill_names = [s.name for s in profiles[0].skills]
        assert "JavaScript" in skill_names
        assert "Python" in skill_names

    def test_array_field_union(self):
        """Array fields from multiple sources should be unioned."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                skills=[
                    RawSkill(name="Python", source=SourceType.RECRUITER_CSV,
                             method=ExtractionMethod.STRUCTURED_PARSE),
                ],
            ),
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                skills=[
                    RawSkill(name="Python", source=SourceType.ATS_JSON,
                             method=ExtractionMethod.FIELD_MAPPING),
                    RawSkill(name="Docker", source=SourceType.ATS_JSON,
                             method=ExtractionMethod.FIELD_MAPPING),
                ],
                source_type=SourceType.ATS_JSON,
                extraction_method=ExtractionMethod.FIELD_MAPPING,
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert len(profiles) == 1
        skill_names = [s.name for s in profiles[0].skills]
        assert "Python" in skill_names
        assert "Docker" in skill_names

    def test_years_experience_max(self):
        """Years of experience should take the maximum across sources."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                years_experience=5.0,
            ),
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                years_experience=8.0,
                source_type=SourceType.ATS_JSON,
                extraction_method=ExtractionMethod.FIELD_MAPPING,
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert profiles[0].years_experience == 8.0

    def test_empty_input(self):
        merger = CandidateMerger()
        profiles = merger.match_and_merge([])
        assert profiles == []

    def test_candidate_id_generated(self):
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert profiles[0].candidate_id is not None
        assert len(profiles[0].candidate_id) > 0

    def test_location_parsed(self):
        """Location strings should be parsed into Location objects."""
        merger = CandidateMerger()
        candidates = [
            self._make_candidate(
                full_name="Alice",
                emails=["alice@test.com"],
                location_raw="San Francisco, CA",
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert profiles[0].location is not None
        assert profiles[0].location.city == "San Francisco"
        assert profiles[0].location.country == "US"


# ═══════════════════════════════════════════════════════════════════════
# Confidence Scorer
# ═══════════════════════════════════════════════════════════════════════

class TestConfidenceScorer:
    """Tests for the confidence scoring engine."""

    def _make_profile(self) -> CanonicalProfile:
        return CanonicalProfile(
            full_name="Alice Johnson",
            emails=["alice@test.com"],
            phones=[PhoneEntry(raw="+16505550101", normalized="+16505550101", confidence=1.0)],
            skills=[
                CanonicalSkill(name="Python", confidence=1.0, sources=["ats_json"]),
            ],
            provenance=[
                ProvenanceRecord(field="full_name", source="ats_json", method="field_mapping"),
                ProvenanceRecord(field="emails", source="ats_json", method="field_mapping"),
                ProvenanceRecord(field="emails", source="recruiter_csv", method="structured_parse"),
                ProvenanceRecord(field="phones", source="recruiter_csv", method="structured_parse"),
                ProvenanceRecord(field="skills", source="ats_json", method="field_mapping"),
            ],
        )

    def test_score_profile(self):
        scorer = ConfidenceScorer()
        profile = self._make_profile()
        result = scorer.score_profile(profile, [SourceType.ATS_JSON, SourceType.RECRUITER_CSV])
        assert 0.0 <= result.overall_confidence <= 1.0
        assert result.overall_confidence > 0.5  # Multiple sources should give high confidence

    def test_single_source_lower_confidence(self):
        scorer = ConfidenceScorer()
        profile = CanonicalProfile(
            full_name="Alice",
            emails=["alice@test.com"],
            provenance=[
                ProvenanceRecord(field="full_name", source="recruiter_notes", method="heuristic"),
                ProvenanceRecord(field="emails", source="recruiter_notes", method="regex"),
            ],
        )
        result = scorer.score_profile(profile, [SourceType.RECRUITER_NOTES])
        # Notes-only should have lower confidence than multi-source
        assert result.overall_confidence < 0.8

    def test_empty_profile(self):
        scorer = ConfidenceScorer()
        profile = CanonicalProfile()
        result = scorer.score_profile(profile, [])
        assert result.overall_confidence == 0.0

    def test_skill_confidence_scaled(self):
        scorer = ConfidenceScorer()
        profile = self._make_profile()
        result = scorer.score_profile(profile, [SourceType.ATS_JSON])
        assert result.skills[0].confidence > 0
        assert result.skills[0].confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════
# Output Projector
# ═══════════════════════════════════════════════════════════════════════

class TestOutputProjector:
    """Tests for the configurable output projection layer."""

    def _make_profile(self) -> CanonicalProfile:
        return CanonicalProfile(
            full_name="Alice Johnson",
            emails=["alice@test.com", "alice@work.com"],
            phones=[PhoneEntry(raw="+16505550101", normalized="+16505550101", confidence=1.0)],
            location=Location(city="San Francisco", region="California", country="US"),
            skills=[
                CanonicalSkill(name="Python", confidence=1.0, sources=["ats_json"]),
                CanonicalSkill(name="JavaScript", confidence=0.9, sources=["recruiter_csv"]),
            ],
            experience=[
                Experience(company="TechCorp", title="Engineer", start="2020-01", end=None),
            ],
            overall_confidence=0.85,
        )

    def test_default_full_output(self):
        config = OutputConfig()
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert "full_name" in result
        assert result["full_name"] == "Alice Johnson"

    def test_field_selection(self):
        config = OutputConfig(
            fields=[
                FieldConfig(path="name", from_path="full_name"),
                FieldConfig(path="primary_email", from_path="emails[0]"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert result["name"] == "Alice Johnson"
        assert result["primary_email"] == "alice@test.com"

    def test_nested_path(self):
        config = OutputConfig(
            fields=[
                FieldConfig(path="city", from_path="location.city"),
                FieldConfig(path="country", from_path="location.country"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert result["city"] == "San Francisco"
        assert result["country"] == "US"

    def test_array_map(self):
        config = OutputConfig(
            fields=[
                FieldConfig(path="skill_names", from_path="skills[].name"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert "Python" in result["skill_names"]
        assert "JavaScript" in result["skill_names"]

    def test_output_normalization(self):
        profile = CanonicalProfile(
            full_name="Alice Johnson",
            emails=["alice@test.com"],
            phones=[PhoneEntry(raw="(415) 555-2671", normalized=None, confidence=0.0)],
            location=Location(city="San Francisco", region="California", country="US"),
            skills=[
                CanonicalSkill(name="reactjs", confidence=0.7, sources=["ats_json"]),
            ],
            experience=[
                Experience(company="TechCorp", title="Engineer", start="Jan 2020", end=None),
            ],
            overall_confidence=0.85,
        )
        config = OutputConfig(
            fields=[
                FieldConfig(path="phone", from_path="phones[0]", type="string", normalize="E164"),
                FieldConfig(path="skill_names", from_path="skills[].name", type="string[]", normalize="canonical"),
                FieldConfig(path="start", from_path="experience[0].start", type="string", normalize="YYYY-MM"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(profile)
        assert result["phone"] == "+14155552671"
        assert result["skill_names"] == ["React"]
        assert result["start"] == "2020-01"

    def test_include_confidence(self):
        config = OutputConfig(include_confidence=True)
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert "overall_confidence" in result

    def test_exclude_confidence(self):
        config = OutputConfig(include_confidence=False)
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert "overall_confidence" not in result

    def test_on_missing_null(self):
        config = OutputConfig(
            on_missing=OnMissing.NULL,
            fields=[
                FieldConfig(path="nonexistent", from_path="nonexistent_field"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert result["nonexistent"] is None

    def test_on_missing_omit(self):
        config = OutputConfig(
            on_missing=OnMissing.OMIT,
            fields=[
                FieldConfig(path="nonexistent", from_path="nonexistent_field"),
            ]
        )
        projector = OutputProjector(config)
        result = projector.project(self._make_profile())
        assert "nonexistent" not in result

    def test_on_missing_error(self):
        config = OutputConfig(
            on_missing=OnMissing.ERROR,
            fields=[
                FieldConfig(path="nonexistent", from_path="nonexistent_field", required=True),
            ]
        )
        projector = OutputProjector(config)
        with pytest.raises(ValueError):
            projector.project(self._make_profile())


# ═══════════════════════════════════════════════════════════════════════
# Validator
# ═══════════════════════════════════════════════════════════════════════

class TestOutputValidator:
    """Tests for the output validator."""

    def test_valid_default_output(self):
        output = {
            "candidate_id": "abc123",
            "full_name": "Alice Johnson",
            "emails": ["alice@test.com"],
            "phones": ["+16505550101"],
            "location": {"city": "San Francisco", "region": "California", "country": "US"},
            "skills": [{"name": "Python", "confidence": 1.0}],
            "experience": [{"company": "TechCorp", "title": "Engineer"}],
            "education": [{"institution": "MIT"}],
            "overall_confidence": 0.85,
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert is_valid
        assert len(errors) == 0

    def test_missing_candidate_id(self):
        output = {
            "full_name": "Alice",
            "emails": [],
            "phones": [],
            "overall_confidence": 0.5,
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert not is_valid
        assert any("candidate_id" in e for e in errors)

    def test_invalid_email(self):
        output = {
            "candidate_id": "abc123",
            "full_name": "Alice",
            "emails": ["not-an-email"],
            "phones": [],
            "overall_confidence": 0.5,
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert not is_valid
        assert any("email" in e.lower() for e in errors)

    def test_invalid_phone(self):
        output = {
            "candidate_id": "abc123",
            "full_name": "Alice",
            "emails": [],
            "phones": ["555-1234"],  # Not E.164
            "overall_confidence": 0.5,
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert not is_valid
        assert any("E.164" in e for e in errors)

    def test_confidence_out_of_range(self):
        output = {
            "candidate_id": "abc123",
            "full_name": "Alice",
            "emails": [],
            "phones": [],
            "overall_confidence": 1.5,  # > 1.0
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert not is_valid

    def test_collects_all_errors(self):
        """Validator should report all errors, not just the first one."""
        output = {
            # Missing candidate_id
            # Missing full_name
            "emails": ["not-an-email"],
            "phones": ["bad-phone"],
            "overall_confidence": 2.0,
        }
        validator = OutputValidator()
        is_valid, errors = validator.validate(output)
        assert not is_valid
        assert len(errors) >= 3  # At least 3 different errors
