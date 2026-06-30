"""
Pipeline orchestrator for the Multi-Source Candidate Data Transformer.

Runs all 7 stages: Detect → Extract → Normalize → Merge → Confidence → Project → Validate.
Coordinates extractors, normalizers, merger, confidence scorer, projection, and validation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from src.models import (
    CanonicalProfile,
    FieldConfig,
    OnMissing,
    OutputConfig,
    PipelineResult,
    RawCandidate,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)
from src.extractors.base import BaseExtractor
from src.extractors.csv_extractor import CSVExtractor
from src.extractors.ats_extractor import ATSExtractor
from src.extractors.github_extractor import GitHubExtractor
from src.extractors.linkedin_extractor import LinkedInExtractor
from src.extractors.notes_extractor import NotesExtractor
from src.extractors.resume_extractor import ResumeExtractor
from src.merger import CandidateMerger
from src.confidence import ConfidenceScorer
from src.projection import OutputProjector
from src.validator import OutputValidator

logger = logging.getLogger(__name__)


class Pipeline:
    """
    End-to-end pipeline that transforms multi-source candidate data
    into clean, canonical profiles.

    Usage:
        pipeline = Pipeline()
        result = pipeline.run(
            csv_path="recruiter_export.csv",
            ats_path="ats_candidates.json",
            github_usernames=["alicejohnson"],
            notes_path="recruiter_notes.txt",
            config=OutputConfig.default(),
        )
    """

    def __init__(self, github_cache_path: Optional[str] = None):
        """
        Initialize the pipeline.

        Args:
            github_cache_path: Path to a JSON file with cached GitHub API
                responses, for deterministic testing without hitting the API.
        """
        self.extractors: dict[SourceType, BaseExtractor] = {
            SourceType.RECRUITER_CSV: CSVExtractor(),
            SourceType.ATS_JSON: ATSExtractor(),
            SourceType.GITHUB: GitHubExtractor(),
            SourceType.LINKEDIN: LinkedInExtractor(),
            SourceType.RECRUITER_NOTES: NotesExtractor(),
            SourceType.RESUME: ResumeExtractor(),
        }
        self.merger = CandidateMerger()
        self.confidence_scorer = ConfidenceScorer()
        self.validator = OutputValidator()
        self.github_cache: dict[str, Any] = {}
        self.linkedin_cache: dict[str, Any] = {}

        if github_cache_path:
            self._load_github_cache(github_cache_path)

    def _load_github_cache(self, path: str) -> None:
        """Load cached GitHub API responses from a JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.github_cache = json.load(f)
            logger.info("Loaded GitHub cache with %d profiles", len(self.github_cache))
        except Exception as e:
            logger.warning("Failed to load GitHub cache from %s: %s", path, e)

    def _load_linkedin_cache(self, path: str) -> None:
        """Load cached LinkedIn API responses from a JSON file."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.linkedin_cache = json.load(f)
            logger.info("Loaded LinkedIn cache with %d profiles", len(self.linkedin_cache))
        except Exception as e:
            logger.warning("Failed to load LinkedIn cache from %s: %s", path, e)

    def run(
        self,
        csv_path: Optional[str] = None,
        ats_path: Optional[str] = None,
        github_usernames: Optional[list[str]] = None,
        linkedin_urls: Optional[list[str]] = None,
        notes_path: Optional[str] = None,
        resume_paths: Optional[list[str]] = None,
        config: Optional[OutputConfig] = None,
        github_cache_path: Optional[str] = None,
        linkedin_cache_path: Optional[str] = None,
    ) -> PipelineResult:
        """
        Run the full pipeline end-to-end.

        Args:
            csv_path: Path to recruiter CSV file.
            ats_path: Path to ATS JSON file.
            github_usernames: List of GitHub usernames to fetch.
            linkedin_urls: List of LinkedIn URLs (or usernames) to fetch.
            notes_path: Path to recruiter notes text file.
            resume_paths: List of paths to resume files (PDF, DOCX, TXT).
            config: Output configuration. Defaults to full canonical schema.
            github_cache_path: Path to cached GitHub responses (overrides init cache).
            linkedin_cache_path: Path to cached LinkedIn responses.

        Returns:
            PipelineResult with profiles, source statuses, warnings, and errors.
        """
        if config is None:
            config = OutputConfig.default()

        if github_cache_path:
            self._load_github_cache(github_cache_path)

        if linkedin_cache_path:
            self._load_linkedin_cache(linkedin_cache_path)

        result = PipelineResult()

        # ─── Stage 1: Detect & Ingest ───────────────────────────────
        envelopes = self._detect_and_ingest(
            csv_path, ats_path, github_usernames, linkedin_urls, notes_path, resume_paths
        )

        if not envelopes:
            result.warnings.append("No valid sources provided.")
            return result

        # ─── Stage 2: Extract & Parse ───────────────────────────────
        all_candidates: list[RawCandidate] = []
        for envelope in envelopes:
            extractor = self.extractors.get(envelope.source_type)
            if extractor is None:
                envelope.status = SourceStatus.FAILED
                envelope.error_message = f"No extractor for {envelope.source_type}"
                result.warnings.append(envelope.error_message)
                continue

            candidates = extractor.safe_extract(envelope)
            if not candidates and envelope.status == SourceStatus.OK:
                envelope.status = SourceStatus.EMPTY
                result.warnings.append(
                    f"Source {envelope.source_type.value} at '{envelope.path}' "
                    f"yielded no candidates."
                )
            else:
                logger.info(
                    "Extracted %d candidates from %s",
                    len(candidates),
                    envelope.source_type.value,
                )
            all_candidates.extend(candidates)

        result.source_statuses = envelopes

        if not all_candidates:
            result.warnings.append("No candidates extracted from any source.")
            return result

        logger.info("Total raw candidates extracted: %d", len(all_candidates))

        # ─── Stage 3 & 4: Normalize + Merge ────────────────────────
        # (Normalization happens inside the merger during field-level merge)
        canonical_profiles = self.merger.match_and_merge(all_candidates)
        logger.info("Merged into %d canonical profiles", len(canonical_profiles))

        # ─── Stage 5: Score Confidence ──────────────────────────────
        for profile in canonical_profiles:
            # Collect source types that contributed to this profile
            source_types = set()
            for prov in profile.provenance:
                try:
                    source_types.add(SourceType(prov.source))
                except ValueError:
                    pass
            self.confidence_scorer.score_profile(
                profile, list(source_types)
            )

        # ─── Stage 6: Project to Output Config ─────────────────────
        projector = OutputProjector(config)
        projected_profiles = []
        for profile in canonical_profiles:
            try:
                projected = projector.project(profile)
                projected_profiles.append(projected)
            except ValueError as e:
                result.errors.append(
                    f"Projection error for candidate "
                    f"'{profile.full_name}': {e}"
                )

        # ─── Stage 7: Validate Output ──────────────────────────────
        validated_profiles = []
        for profile_dict in projected_profiles:
            is_valid, errors = self.validator.validate(profile_dict, config)
            if not is_valid:
                name = profile_dict.get("full_name", profile_dict.get("candidate_id", "unknown"))
                for err in errors:
                    result.warnings.append(
                        f"Validation warning for '{name}': {err}"
                    )
            validated_profiles.append(profile_dict)

        result.profiles = validated_profiles
        return result

    def _detect_and_ingest(
        self,
        csv_path: Optional[str],
        ats_path: Optional[str],
        github_usernames: Optional[list[str]],
        linkedin_urls: Optional[list[str]],
        notes_path: Optional[str],
        resume_paths: Optional[list[str]],
    ) -> list[SourceEnvelope]:
        """
        Stage 1: Detect source types and create SourceEnvelopes.
        Reads raw data into each envelope for processing.
        """
        envelopes: list[SourceEnvelope] = []

        # CSV source
        if csv_path:
            envelope = self._ingest_file(
                csv_path, SourceType.RECRUITER_CSV
            )
            envelopes.append(envelope)

        # ATS JSON source
        if ats_path:
            envelope = self._ingest_json(ats_path, SourceType.ATS_JSON)
            envelopes.append(envelope)

        # GitHub sources (one envelope per username)
        if github_usernames:
            for username in github_usernames:
                envelope = SourceEnvelope(
                    source_type=SourceType.GITHUB,
                    path=f"github://{username}",
                )
                if username.lower() in self.github_cache:
                    envelope.raw_data = self.github_cache[username.lower()]
                else:
                    envelope.raw_data = {"username": username}
                envelopes.append(envelope)

        # LinkedIn sources (one envelope per URL/username)
        if linkedin_urls:
            for url in linkedin_urls:
                envelope = SourceEnvelope(
                    source_type=SourceType.LINKEDIN,
                    path=url,
                )
                # Simple extraction of username from URL for cache lookup
                username = url.strip("/").split("/")[-1].lower()
                if username in self.linkedin_cache:
                    envelope.raw_data = self.linkedin_cache[username]
                else:
                    envelope.raw_data = url
                envelopes.append(envelope)

        # Recruiter notes source
        if notes_path:
            envelope = self._ingest_file(
                notes_path, SourceType.RECRUITER_NOTES
            )
            envelopes.append(envelope)

        # Resume sources (one envelope per file)
        if resume_paths:
            for path in resume_paths:
                envelope = self._ingest_binary_file(
                    path, SourceType.RESUME
                )
                envelopes.append(envelope)

        return envelopes

    def _ingest_file(
        self, path: str, source_type: SourceType
    ) -> SourceEnvelope:
        """Read a text file into a SourceEnvelope."""
        envelope = SourceEnvelope(
            source_type=source_type,
            path=path,
        )
        try:
            file_path = Path(path)
            if not file_path.exists():
                envelope.status = SourceStatus.FAILED
                envelope.error_message = f"File not found: {path}"
                return envelope

            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                envelope.status = SourceStatus.EMPTY
                envelope.error_message = f"File is empty: {path}"
                return envelope

            envelope.raw_data = content
        except Exception as e:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = f"Error reading {path}: {e}"

        return envelope

    def _ingest_json(
        self, path: str, source_type: SourceType
    ) -> SourceEnvelope:
        """Read and parse a JSON file into a SourceEnvelope."""
        envelope = SourceEnvelope(
            source_type=source_type,
            path=path,
        )
        try:
            file_path = Path(path)
            if not file_path.exists():
                envelope.status = SourceStatus.FAILED
                envelope.error_message = f"File not found: {path}"
                return envelope

            content = file_path.read_text(encoding="utf-8")
            if not content.strip():
                envelope.status = SourceStatus.EMPTY
                envelope.error_message = f"File is empty: {path}"
                return envelope

            envelope.raw_data = json.loads(content)
        except json.JSONDecodeError as e:
            envelope.status = SourceStatus.MALFORMED
            envelope.error_message = f"Invalid JSON in {path}: {e}"
        except Exception as e:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = f"Error reading {path}: {e}"

        return envelope

    def _ingest_binary_file(
        self, path: str, source_type: SourceType
    ) -> SourceEnvelope:
        """Read a file as bytes into a SourceEnvelope (for PDF/DOCX parsing)."""
        envelope = SourceEnvelope(
            source_type=source_type,
            path=path,
        )
        try:
            file_path = Path(path)
            if not file_path.exists():
                envelope.status = SourceStatus.FAILED
                envelope.error_message = f"File not found: {path}"
                return envelope

            content = file_path.read_bytes()
            if not content:
                envelope.status = SourceStatus.EMPTY
                envelope.error_message = f"File is empty: {path}"
                return envelope

            envelope.raw_data = content
        except Exception as e:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = f"Error reading {path}: {e}"

        return envelope
