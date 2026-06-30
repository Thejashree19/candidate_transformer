"""
Extractor for candidate resumes (PDF, DOCX, TXT).

Reads binary file content from the source envelope, extracts text using
pypdf or python-docx, and then leverages the heuristic/regex parsing
from NotesExtractor to find candidate details.
"""

from __future__ import annotations

import io
from typing import Any

from src.models import (
    ExtractionMethod,
    RawCandidate,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)
from .notes_extractor import NotesExtractor


class ResumeExtractor(NotesExtractor):
    """Parse resume files (PDF/DOCX/TXT) into :class:`RawCandidate` records."""

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract a single candidate from a resume file.

        Args:
            envelope: Source envelope containing file bytes in ``raw_data``
                      and the original filename in ``path``.

        Returns:
            A list containing the extracted :class:`RawCandidate`.
        """
        text = self._extract_text_from_file(envelope.raw_data, envelope.path)
        if not text:
            envelope.status = SourceStatus.EMPTY
            return []

        # Parse section using inherited NotesExtractor logic
        candidate = self._parse_section(text)
        
        if candidate is not None:
            # Override provenance metadata for resumes
            candidate.source_type = SourceType.RESUME
            candidate.extraction_method = ExtractionMethod.HEURISTIC
            
            for skill in candidate.skills:
                skill.source = SourceType.RESUME
                skill.method = ExtractionMethod.HEURISTIC
                
            for exp in candidate.experience:
                exp.source = SourceType.RESUME
                exp.method = ExtractionMethod.HEURISTIC
                
            for edu in candidate.education:
                edu.source = SourceType.RESUME
                edu.method = ExtractionMethod.HEURISTIC

            return [candidate]

        envelope.status = SourceStatus.EMPTY
        return []

    def _extract_text_from_file(self, raw: Any, path: str) -> str:
        """Extract plain text from bytes based on file extension."""
        if not isinstance(raw, bytes) and not isinstance(raw, str):
            return ""

        # If it's already a string, just return it
        if isinstance(raw, str):
            return raw.strip()

        path_lower = path.lower()
        
        try:
            if path_lower.endswith(".pdf"):
                return self._parse_pdf(raw)
            elif path_lower.endswith(".docx"):
                return self._parse_docx(raw)
            else:
                # Default to UTF-8 text decode
                return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""

    @staticmethod
    def _parse_pdf(file_bytes: bytes) -> str:
        """Extract text from a PDF file using pypdf."""
        try:
            import pypdf
        except ImportError:
            return ""

        try:
            pdf = pypdf.PdfReader(io.BytesIO(file_bytes))
            text_pages = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_pages.append(page_text)
            return "\n".join(text_pages)
        except Exception:
            return ""

    @staticmethod
    def _parse_docx(file_bytes: bytes) -> str:
        """Extract text from a DOCX file using python-docx."""
        try:
            import docx
        except ImportError:
            return ""

        try:
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join(paragraph.text for paragraph in doc.paragraphs)
        except Exception:
            return ""
