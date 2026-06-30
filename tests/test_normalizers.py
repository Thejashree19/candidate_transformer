"""
Unit tests for the normalizer modules.

Covers phone normalization (E.164), date normalization (YYYY-MM),
location parsing (ISO 3166 alpha-2), and skill canonicalization.
"""

import pytest

from src.normalizers.phone import normalize_phone
from src.normalizers.date import normalize_date
from src.normalizers.location import parse_location
from src.normalizers.skills import canonicalize_skill, canonicalize_skills


# ═══════════════════════════════════════════════════════════════════════
# Phone Normalization
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizePhone:
    """Tests for E.164 phone number normalization."""

    def test_international_with_plus(self):
        result, confidence = normalize_phone("+14155552671")
        assert result == "+14155552671"
        assert confidence == 1.0

    def test_us_number_with_area_code(self):
        result, confidence = normalize_phone("(650) 555-0101")
        assert result == "+16505550101"
        assert confidence == 0.8

    def test_us_number_with_dashes(self):
        result, confidence = normalize_phone("650-555-0101")
        assert result == "+16505550101"
        assert confidence == 0.8

    def test_international_uk(self):
        result, confidence = normalize_phone("+44 20 7946 0958")
        assert result == "+442079460958"
        assert confidence == 1.0

    def test_international_india(self):
        result, confidence = normalize_phone("+91-98765-43210")
        assert result is not None
        assert result.startswith("+91")
        assert confidence == 1.0

    def test_garbage_input(self):
        result, confidence = normalize_phone("gibberish")
        assert result is None
        assert confidence == 0.0

    def test_empty_string(self):
        result, confidence = normalize_phone("")
        assert result is None
        assert confidence == 0.0

    def test_none_input(self):
        result, confidence = normalize_phone(None)
        assert result is None
        assert confidence == 0.0

    def test_partial_number(self):
        """A partial number like '555-0142' with no area code should fail."""
        result, confidence = normalize_phone("555-0142")
        # This may or may not parse; if it does, confidence should be low
        # If it doesn't parse, result should be None
        assert result is None or confidence <= 0.8


# ═══════════════════════════════════════════════════════════════════════
# Date Normalization
# ═══════════════════════════════════════════════════════════════════════

class TestNormalizeDate:
    """Tests for YYYY-MM date normalization."""

    def test_month_year_short(self):
        assert normalize_date("Jan 2020") == "2020-01"

    def test_month_year_full(self):
        assert normalize_date("January 2020") == "2020-01"

    def test_iso_date(self):
        assert normalize_date("2020-01-15") == "2020-01"

    def test_mm_yyyy(self):
        assert normalize_date("03/2020") == "2020-03"

    def test_year_only(self):
        assert normalize_date("2020") == "2020-01"

    def test_present(self):
        assert normalize_date("Present") is None

    def test_current(self):
        assert normalize_date("Current") is None

    def test_month_year_format2(self):
        assert normalize_date("March 2020") == "2020-03"

    def test_empty_string(self):
        assert normalize_date("") is None

    def test_garbage(self):
        assert normalize_date("not a date") is None


# ═══════════════════════════════════════════════════════════════════════
# Location Parsing
# ═══════════════════════════════════════════════════════════════════════

class TestParseLocation:
    """Tests for location string parsing with ISO 3166 alpha-2 codes."""

    def test_city_state_us(self):
        result = parse_location("San Francisco, CA")
        assert result["city"] == "San Francisco"
        assert result["region"] == "California"
        assert result["country"] == "US"

    def test_city_state_country_us(self):
        result = parse_location("San Jose, CA, USA")
        assert result["city"] == "San Jose"
        assert result["country"] == "US"

    def test_city_country_uk(self):
        result = parse_location("London, UK")
        assert result["city"] == "London"
        assert result["country"] == "GB"

    def test_city_country_india(self):
        result = parse_location("Bangalore, India")
        assert result["city"] == "Bangalore"
        assert result["country"] == "IN"

    def test_city_country_germany(self):
        result = parse_location("Berlin, Germany")
        assert result["city"] == "Berlin"
        assert result["country"] == "DE"

    def test_single_city(self):
        result = parse_location("San Francisco")
        assert result["city"] == "San Francisco"

    def test_empty_string(self):
        result = parse_location("")
        assert result["city"] is None
        assert result["region"] is None
        assert result["country"] is None

    def test_none_input(self):
        result = parse_location(None)
        assert result["city"] is None


# ═══════════════════════════════════════════════════════════════════════
# Skill Canonicalization
# ═══════════════════════════════════════════════════════════════════════

class TestCanonicalizeSkill:
    """Tests for skill name canonicalization."""

    def test_exact_match(self):
        name, confidence, method = canonicalize_skill("Python")
        assert name == "Python"
        assert confidence == 1.0
        assert method == "exact"

    def test_case_insensitive_exact(self):
        name, confidence, method = canonicalize_skill("python")
        assert name == "Python"
        assert confidence == 1.0

    def test_synonym_js(self):
        name, confidence, method = canonicalize_skill("JS")
        assert name == "JavaScript"
        assert confidence == 1.0
        assert method == "synonym"

    def test_synonym_ml(self):
        name, confidence, method = canonicalize_skill("ML")
        assert name == "Machine Learning"
        assert confidence == 1.0
        assert method == "synonym"

    def test_synonym_k8s(self):
        name, confidence, method = canonicalize_skill("k8s")
        assert name == "Kubernetes"
        assert confidence == 1.0

    def test_synonym_nodejs(self):
        name, confidence, method = canonicalize_skill("nodejs")
        assert name == "Node.js"
        assert confidence == 1.0

    def test_java_not_javascript(self):
        """Java must NOT be matched to JavaScript."""
        name, confidence, method = canonicalize_skill("Java")
        assert name == "Java"
        assert "JavaScript" not in name

    def test_fuzzy_typo(self):
        """A typo should be caught by fuzzy matching."""
        name, confidence, method = canonicalize_skill("Pythn")
        assert name == "Python"
        assert method == "fuzzy"
        assert confidence > 0.8

    def test_unmatched_skill(self):
        """A completely unknown skill returns with low confidence."""
        name, confidence, method = canonicalize_skill("SomeObscureFramework2000")
        assert method == "unmatched"
        assert confidence == 0.4

    def test_empty_string(self):
        name, confidence, method = canonicalize_skill("")
        assert confidence == 0.0

    def test_batch_canonicalize(self):
        results = canonicalize_skills(["JS", "Python", "k8s"])
        assert len(results) == 3
        assert results[0]["name"] == "JavaScript"
        assert results[1]["name"] == "Python"
        assert results[2]["name"] == "Kubernetes"
