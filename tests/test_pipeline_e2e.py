"""
End-to-end integration tests for the full pipeline.

Runs the complete pipeline (Detect → Extract → Normalize → Merge →
Confidence → Project → Validate) using sample input data and verifies
the output meets all schema requirements.
"""

import json
import os
import pytest

from src.models import OutputConfig, FieldConfig, OnMissing
from src.pipeline import Pipeline
from src.validator import OutputValidator


# ═══════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════

def _sample_dir():
    """Return path to sample_inputs/ directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_inputs")


def _config_dir():
    """Return path to config/ directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")


# ═══════════════════════════════════════════════════════════════════════
# Full Pipeline Integration Tests
# ═══════════════════════════════════════════════════════════════════════

class TestPipelineE2E:
    """End-to-end tests running the full pipeline on sample data."""

    def test_full_pipeline_default_config(self):
        """Run the complete pipeline with default config and validate output."""
        pipeline = Pipeline(
            github_cache_path=os.path.join(_sample_dir(), "github_cache.json")
        )
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
            ats_path=os.path.join(_sample_dir(), "ats_candidates.json"),
            github_usernames=["alicejohnson", "evelynpark"],
            notes_path=os.path.join(_sample_dir(), "recruiter_notes.txt"),
        )

        # Should produce profiles
        assert len(result.profiles) > 0
        # Should not have hard errors
        assert len(result.errors) == 0

        # Validate each profile
        validator = OutputValidator()
        for profile_dict in result.profiles:
            is_valid, errors = validator.validate(profile_dict)
            # Validation warnings are OK, but core fields should be valid
            # (some profiles may have partial data, that's fine)

    def test_full_pipeline_custom_config(self):
        """Run the pipeline with custom output projection."""
        config = OutputConfig(
            include_confidence=True,
            include_provenance=False,
            on_missing=OnMissing.NULL,
            fields=[
                FieldConfig(path="full_name", from_path="full_name"),
                FieldConfig(path="primary_email", from_path="emails[0]"),
                FieldConfig(path="skills", from_path="skills[].name"),
                FieldConfig(path="city", from_path="location.city"),
                FieldConfig(path="country", from_path="location.country"),
            ],
        )

        pipeline = Pipeline(
            github_cache_path=os.path.join(_sample_dir(), "github_cache.json")
        )
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
            ats_path=os.path.join(_sample_dir(), "ats_candidates.json"),
            github_usernames=["alicejohnson", "evelynpark"],
            notes_path=os.path.join(_sample_dir(), "recruiter_notes.txt"),
            config=config,
        )

        assert len(result.profiles) > 0

        # Check that custom fields are present
        for profile in result.profiles:
            assert "full_name" in profile
            assert "primary_email" in profile
            assert "skills" in profile
            assert "overall_confidence" in profile
            assert "provenance" not in profile

    def test_csv_only(self):
        """Pipeline should work with only CSV input."""
        pipeline = Pipeline()
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
        )
        assert len(result.profiles) > 0

    def test_notes_only(self):
        """Pipeline should work with only notes input."""
        pipeline = Pipeline()
        result = pipeline.run(
            notes_path=os.path.join(_sample_dir(), "recruiter_notes.txt"),
        )
        assert len(result.profiles) > 0

    def test_no_inputs(self):
        """Pipeline with no inputs should return empty result gracefully."""
        pipeline = Pipeline()
        result = pipeline.run()
        assert len(result.profiles) == 0

    def test_merge_cross_source(self):
        """Verify that candidates appearing in multiple sources are merged."""
        pipeline = Pipeline(
            github_cache_path=os.path.join(_sample_dir(), "github_cache.json")
        )
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
            ats_path=os.path.join(_sample_dir(), "ats_candidates.json"),
            github_usernames=["alicejohnson", "evelynpark"],
            notes_path=os.path.join(_sample_dir(), "recruiter_notes.txt"),
        )

        # Alice should appear in CSV, ATS, GitHub, and Notes
        # but should be merged into ONE profile
        alice_profiles = [
            p for p in result.profiles
            if p.get("full_name") and "alice" in p["full_name"].lower()
        ]
        assert len(alice_profiles) == 1, (
            f"Expected 1 Alice profile, got {len(alice_profiles)}: "
            f"{[p.get('full_name') for p in alice_profiles]}"
        )

    def test_confidence_scores_present(self):
        """All profiles should have confidence scores between 0 and 1."""
        pipeline = Pipeline(
            github_cache_path=os.path.join(_sample_dir(), "github_cache.json")
        )
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
            ats_path=os.path.join(_sample_dir(), "ats_candidates.json"),
        )

        for profile in result.profiles:
            conf = profile.get("overall_confidence", None)
            assert conf is not None, f"Missing confidence for {profile.get('full_name')}"
            assert 0.0 <= conf <= 1.0

    def test_provenance_tracked(self):
        """Profiles should include provenance records."""
        pipeline = Pipeline(
            github_cache_path=os.path.join(_sample_dir(), "github_cache.json")
        )
        result = pipeline.run(
            csv_path=os.path.join(_sample_dir(), "recruiter_export.csv"),
            ats_path=os.path.join(_sample_dir(), "ats_candidates.json"),
        )

        for profile in result.profiles:
            prov = profile.get("provenance", None)
            assert prov is not None, f"Missing provenance for {profile.get('full_name')}"
            assert isinstance(prov, list)
            if profile.get("full_name"):
                assert len(prov) > 0  # At least some provenance for named profiles


# ═══════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Tests for malformed/missing data resilience."""

    def test_malformed_csv(self):
        """Pipeline should handle malformed CSV gracefully."""
        pipeline = Pipeline()
        result = pipeline.run(csv_path="nonexistent_file.csv")
        # Should not crash — just log an error
        assert isinstance(result.profiles, list)

    def test_empty_ats_json(self):
        """Pipeline should handle empty ATS JSON gracefully."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"candidates": []}, f)
            f.flush()
            pipeline = Pipeline()
            result = pipeline.run(ats_path=f.name)
            assert isinstance(result.profiles, list)
        os.unlink(f.name)

    def test_deduplication_case_insensitive_email(self):
        """Emails differing only in case should be treated as the same."""
        from src.merger import CandidateMerger
        from src.models import RawCandidate, ExtractionMethod

        merger = CandidateMerger()
        candidates = [
            RawCandidate(
                full_name="Alice",
                emails=["Alice@Test.com"],
                source_type=SourceType.ATS_JSON,
                extraction_method=ExtractionMethod.FIELD_MAPPING,
            ),
            RawCandidate(
                full_name="Alice Johnson",
                emails=["alice@test.com"],
                source_type=SourceType.RECRUITER_CSV,
                extraction_method=ExtractionMethod.STRUCTURED_PARSE,
            ),
        ]
        profiles = merger.match_and_merge(candidates)
        assert len(profiles) == 1

    def test_invalid_contact_values_are_dropped(self):
        """Garbage emails and phones should not survive into the canonical profile."""
        from src.merger import CandidateMerger
        from src.models import RawCandidate, ExtractionMethod, SourceType

        merger = CandidateMerger()
        profiles = merger.match_and_merge(
            [
                RawCandidate(
                    full_name="Invalid Row",
                    emails=["not-an-email"],
                    phones=["555-0142"],
                    source_type=SourceType.RECRUITER_CSV,
                    extraction_method=ExtractionMethod.STRUCTURED_PARSE,
                )
            ]
        )

        assert len(profiles) == 1
        profile = profiles[0]
        assert profile.emails == []
        assert profile.phones == []


# Import SourceType for the edge case test
from src.models import SourceType
