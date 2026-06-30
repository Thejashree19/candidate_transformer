"""
Abstract base class for all candidate data extractors.

Every source-specific extractor inherits from BaseExtractor and implements
the ``extract`` method.  The ``safe_extract`` wrapper provides a uniform
error-handling boundary so callers never see unhandled exceptions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import RawCandidate, SourceEnvelope, SourceStatus


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
            return self.extract(envelope)
        except Exception as e:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = str(e)
            return []
