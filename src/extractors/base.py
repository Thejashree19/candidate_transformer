"""
Abstract base class for all candidate data extractors.

Every source-specific extractor inherits from BaseExtractor and implements
the ``extract`` method.  The ``safe_extract`` wrapper provides a uniform
error-handling boundary so callers never see unhandled exceptions.
"""

from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from src.models import RawCandidate, SourceEnvelope, SourceStatus

logger = logging.getLogger(__name__)


class BaseExtractor(ABC):
    """Base class that all source extractors must extend."""

    @abstractmethod
    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract candidate records from a source envelope.

        Subclasses must implement this method.  It should assume the
        envelope status has already been validated as ``SourceStatus.OK``.

        Args:
            envelope: The source envelope containing raw data and metadata.

        Returns:
            A list of ``RawCandidate`` instances.  An empty list is returned
            when no candidates can be extracted.
        """

    def safe_extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Error-safe wrapper around :meth:`extract`.

        * Skips envelopes whose status is not ``OK``.
        * Catches **all** exceptions, marks the envelope as ``FAILED``,
          and returns an empty list so the pipeline can continue.

        Args:
            envelope: The source envelope to process.

        Returns:
            A list of extracted candidates, or ``[]`` on any failure.
        """
        try:
            if envelope.status != SourceStatus.OK:
                return []
            candidates = self.extract(envelope)
            # Stamp each record with a unique source_id and fetch timestamp
            timestamp = datetime.utcnow().isoformat() + "Z"
            for candidate in candidates:
                if not candidate.source_id:
                    candidate.source_id = str(uuid.uuid4())
                if not candidate.fetched_at:
                    candidate.fetched_at = timestamp
                
                # Propagate to nested arrays
                for skill in candidate.skills:
                    skill.source_id = candidate.source_id
                    skill.fetched_at = candidate.fetched_at
                for exp in candidate.experience:
                    exp.source_id = candidate.source_id
                    exp.fetched_at = candidate.fetched_at
                for edu in candidate.education:
                    edu.source_id = candidate.source_id
                    edu.fetched_at = candidate.fetched_at
            return candidates
        except Exception as e:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = str(e)
            logger.error("Extraction failed for %s: %s", envelope.source_type.value, e)
            return []
