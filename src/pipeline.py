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
from src.extractors.notes_extractor import NotesExtractor
from src.extractors.resume_extractor import ResumeExtractor
from src.merger import CandidateMerger
from src.confidence import ConfidenceScorer
from src.projection import OutputProjector
from src.validator import OutputValidator, RecordValidator

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
            SourceType.RECRUITER_NOTES: NotesExtractor(),
            SourceType.RESUME: ResumeExtractor(),
        }
        self.merger = CandidateMerger()
        self.confidence_scorer = ConfidenceScorer()
        self.validator = OutputValidator()
        self.record_validator = RecordValidator()

    def run(
        self,
        csv_path: Optional[str] = None,
        ats_path: Optional[str] = None,
        notes_path: Optional[str] = None,
        resume_paths: Optional[list[str]] = None,
        config: Optional[OutputConfig] = None,
    ) -> PipelineResult:
        """
        Run the full pipeline end-to-end.

        Args:
            csv_path: Path to recruiter CSV file.
            ats_path: Path to ATS JSON file.
            notes_path: Path to recruiter notes text file.
            resume_paths: List of paths to resume files (PDF, DOCX, TXT).
            config: Output configuration. Defaults to full canonical schema.

        Returns:
            PipelineResult with profiles, source statuses, warnings, and errors.
        """
        if config is None:
            config = OutputConfig.default()

        result = PipelineResult()

        # ─── Stage 1: Detect & Ingest ───────────────────────────────
        envelopes = self._detect_and_ingest(
            csv_path, ats_path, notes_path, resume_paths
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

        # ─── Stage 2.5: Pre-Merge Validation ────────────────────────
        all_candidates, validation_errors = self.record_validator.validate_batch(all_candidates)
        result.warnings.extend(validation_errors)

        if not all_candidates:
            result.warnings.append("No valid candidates remained after pre-merge validation.")
            return result

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
