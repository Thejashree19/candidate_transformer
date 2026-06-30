"""
Unit tests for all extractor modules.

Tests CSV, ATS JSON, GitHub, and Notes extractors with realistic
sample data and edge cases.
"""

import pytest

from src.models import (
    ExtractionMethod,
    RawCandidate,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)
from src.extractors.csv_extractor import CSVExtractor
from src.extractors.ats_extractor import ATSExtractor
from src.extractors.github_extractor import GitHubExtractor
from src.extractors.notes_extractor import NotesExtractor


# ═══════════════════════════════════════════════════════════════════════
# CSV Extractor
# ═══════════════════════════════════════════════════════════════════════

class TestCSVExtractor:
    """Tests for the recruiter CSV extractor."""

    def _make_envelope(self, csv_text: str) -> SourceEnvelope:
        return SourceEnvelope(
            source_type=SourceType.RECRUITER_CSV,
            path="test.csv",
            raw_data=csv_text,
        )

    def test_basic_csv(self):
        csv = (
            "name,email,phone,current_company,title,skills\n"
            "Alice,alice@test.com,555-1234,TechCorp,Engineer,Python;JS\n"
        )
        extractor = CSVExtractor()
        result = extractor.extract(self._make_envelope(csv))
        assert len(result) == 1
        assert result[0].full_name == "Alice"
        assert result[0].emails == ["alice@test.com"]
        assert len(result[0].skills) == 2

    def test_multiple_rows(self):
        csv = (
            "name,email\n"
            "Alice,alice@test.com\n"
            "Bob,bob@test.com\n"
        )
        result = CSVExtractor().extract(self._make_envelope(csv))
        assert len(result) == 2

    def test_skip_empty_name_and_email(self):
        csv = (
            "name,email,phone\n"
            ",,,\n"
            "Alice,alice@test.com,555-1234\n"
        )
        result = CSVExtractor().extract(self._make_envelope(csv))
        assert len(result) == 1
        assert result[0].full_name == "Alice"

    def test_missing_columns(self):
        """CSV with only name and email columns should still work."""
        csv = "name,email\nAlice,alice@test.com\n"
        result = CSVExtractor().extract(self._make_envelope(csv))
        assert len(result) == 1
        assert result[0].skills == []

    def test_empty_csv(self):
        envelope = self._make_envelope("")
        result = CSVExtractor().extract(envelope)
        assert len(result) == 0
        assert envelope.status == SourceStatus.EMPTY

    def test_source_type_set(self):
        csv = "name,email\nAlice,alice@test.com\n"
        result = CSVExtractor().extract(self._make_envelope(csv))
        assert result[0].source_type == SourceType.RECRUITER_CSV


# ═══════════════════════════════════════════════════════════════════════
# ATS Extractor
# ═══════════════════════════════════════════════════════════════════════

class TestATSExtractor:
    """Tests for the ATS JSON extractor."""

    def _make_envelope(self, data) -> SourceEnvelope:
        return SourceEnvelope(
            source_type=SourceType.ATS_JSON,
            path="test.json",
            raw_data=data,
        )

    def test_single_candidate(self):
        data = {
            "applicant_name": "Alice Johnson",
            "contact_email": "alice@test.com",
            "contact_phone": "+14155552671",
            "tech_stack": ["Python", "JS"],
        }
        result = ATSExtractor().extract(self._make_envelope(data))
        assert len(result) == 1
        assert result[0].full_name == "Alice Johnson"
        assert result[0].emails == ["alice@test.com"]
        assert len(result[0].skills) == 2

    def test_candidates_wrapper(self):
        """Handle {"candidates": [...]} wrapper format."""
        data = {
            "candidates": [
                {"applicant_name": "Alice", "contact_email": "alice@test.com"},
                {"applicant_name": "Bob", "contact_email": "bob@test.com"},
            ]
        }
        result = ATSExtractor().extract(self._make_envelope(data))
        assert len(result) == 2

    def test_work_history(self):
        data = {
            "applicant_name": "Alice",
            "work_history": [
                {
                    "employer": "TechCorp",
                    "role": "Engineer",
                    "start_date": "Jan 2020",
                    "end_date": "Present",
                }
            ],
        }
        result = ATSExtractor().extract(self._make_envelope(data))
        assert len(result[0].experience) == 1
        assert result[0].experience[0].company == "TechCorp"

    def test_social_profiles_flat_dict(self):
        """Handle {"linkedin": "url", "github": "url"} format."""
        data = {
            "applicant_name": "Alice",
            "social_profiles": {
                "linkedin": "https://linkedin.com/in/alice",
                "github": "https://github.com/alice",
            },
        }
        result = ATSExtractor().extract(self._make_envelope(data))
        assert result[0].linkedin_url == "https://linkedin.com/in/alice"
        assert result[0].github_url == "https://github.com/alice"

    def test_empty_data(self):
        envelope = self._make_envelope({})
        result = ATSExtractor().extract(envelope)
        assert len(result) == 1  # Single empty record

    def test_source_type(self):
        data = {"applicant_name": "Alice"}
        result = ATSExtractor().extract(self._make_envelope(data))
        assert result[0].source_type == SourceType.ATS_JSON


# ═══════════════════════════════════════════════════════════════════════
# GitHub Extractor
# ═══════════════════════════════════════════════════════════════════════

class TestGitHubExtractor:
    """Tests for the GitHub extractor (using cached data)."""

    def _make_cached_envelope(self) -> SourceEnvelope:
        return SourceEnvelope(
            source_type=SourceType.GITHUB,
            path="github://testuser",
            raw_data={
                "profile": {
                    "login": "testuser",
                    "name": "Test User",
                    "bio": "Software Developer",
                    "location": "San Francisco",
                    "email": "test@github.com",
                    "company": "TestCo",
                    "blog": "https://test.dev",
                    "html_url": "https://github.com/testuser",
                },
                "repos": [
                    {
                        "name": "my-project",
                        "language": "Python",
                        "topics": ["machine-learning", "api"],
                        "fork": False,
                        "stargazers_count": 50,
                    },
                    {
                        "name": "web-app",
                        "language": "TypeScript",
                        "topics": ["react"],
                        "fork": False,
                        "stargazers_count": 30,
                    },
                    {
                        "name": "forked-repo",
                        "language": "JavaScript",
                        "topics": [],
                        "fork": True,
                        "stargazers_count": 0,
                    },
                ],
            },
        )

    def test_cached_profile_extraction(self):
        extractor = GitHubExtractor()
        result = extractor.extract(self._make_cached_envelope())
        assert len(result) == 1
        candidate = result[0]
        assert candidate.full_name == "Test User"
        assert candidate.github_url == "https://github.com/testuser"
        assert candidate.headline == "Software Developer"

    def test_skills_from_repos(self):
        extractor = GitHubExtractor()
        result = extractor.extract(self._make_cached_envelope())
        skill_names = [s.name for s in result[0].skills]
        assert "Python" in skill_names
        assert "TypeScript" in skill_names

    def test_source_type(self):
        result = GitHubExtractor().extract(self._make_cached_envelope())
        assert result[0].source_type == SourceType.GITHUB
        assert result[0].extraction_method == ExtractionMethod.API_FETCH

    def test_malformed_cache(self):
        envelope = SourceEnvelope(
            source_type=SourceType.GITHUB,
            path="github://bad",
            raw_data={"profile": "not a dict", "repos": []},
        )
        result = GitHubExtractor().extract(envelope)
        assert len(result) == 0
        assert envelope.status == SourceStatus.MALFORMED


# ═══════════════════════════════════════════════════════════════════════
# Notes Extractor
# ═══════════════════════════════════════════════════════════════════════

class TestNotesExtractor:
    """Tests for the recruiter notes extractor."""

    def _make_envelope(self, text: str) -> SourceEnvelope:
        return SourceEnvelope(
            source_type=SourceType.RECRUITER_NOTES,
            path="test.txt",
            raw_data=text,
        )

    def test_extract_email(self):
        text = "Contact: alice@test.com for interview."
        result = NotesExtractor().extract(self._make_envelope(text))
        assert len(result) == 1
        assert "alice@test.com" in result[0].emails

    def test_extract_phone(self):
        text = "Phone: (650) 555-0101. Good candidate."
        result = NotesExtractor().extract(self._make_envelope(text))
        assert len(result) == 1
        assert len(result[0].phones) >= 1

    def test_extract_name(self):
        text = "Candidate: Alice Johnson\nGreat engineer."
        result = NotesExtractor().extract(self._make_envelope(text))
        assert result[0].full_name == "Alice Johnson"

    def test_extract_name_spoke_with(self):
        text = "Spoke with Bob Chen about the position."
        result = NotesExtractor().extract(self._make_envelope(text))
        assert result[0].full_name == "Bob Chen"

    def test_extract_skills(self):
        text = "Skills: Python, JavaScript, React, Docker\nEmail: test@test.com"
        result = NotesExtractor().extract(self._make_envelope(text))
        skill_names = [s.name for s in result[0].skills]
        assert "Python" in skill_names
        assert "React" in skill_names

    def test_extract_location(self):
        text = "Based in San Francisco, CA\nEmail: test@test.com"
        result = NotesExtractor().extract(self._make_envelope(text))
        assert result[0].location_raw is not None
        assert "San Francisco" in result[0].location_raw

    def test_extract_years_experience(self):
        text = "10 years of experience in engineering.\nEmail: test@test.com"
        result = NotesExtractor().extract(self._make_envelope(text))
        assert result[0].years_experience == 10.0

    def test_multi_candidate_split(self):
        text = (
            "Candidate: Alice\nEmail: alice@test.com\n"
            "---\n"
            "Candidate: Bob\nEmail: bob@test.com\n"
        )
        result = NotesExtractor().extract(self._make_envelope(text))
        assert len(result) == 2

    def test_empty_text(self):
        envelope = self._make_envelope("")
        result = NotesExtractor().extract(envelope)
        assert len(result) == 0
        assert envelope.status == SourceStatus.EMPTY

    def test_source_type(self):
        text = "Candidate: Test\nEmail: test@test.com"
        result = NotesExtractor().extract(self._make_envelope(text))
        assert result[0].source_type == SourceType.RECRUITER_NOTES

    def test_extract_education(self):
        text = "Has a BS in Computer Science from MIT.\nEmail: test@test.com"
        result = NotesExtractor().extract(self._make_envelope(text))
        assert len(result[0].education) >= 1
        edu = result[0].education[0]
        assert edu.institution is not None
        assert "MIT" in edu.institution
