"""
Candidate matching and merging engine.

Groups ``RawCandidate`` records that refer to the same person into clusters,
then merges each cluster into a single ``CanonicalProfile`` using a
deterministic source-priority policy.

Matching keys (any match ⇒ same person):
    • Primary: normalized e-mail (lowercase, stripped)
    • Secondary: (normalized full_name + normalized phone) when both present
    • Secondary: (normalized full_name + company) when both present
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.models import (
    CanonicalProfile,
    CanonicalSkill,
    Education,
    Experience,
    ExtractionMethod,
    Links,
    Location,
    ProvenanceRecord,
    RawCandidate,
    RawEducation,
    RawExperience,
    RawSkill,
    SourceType,
)

# Normalizer imports — these modules are expected at src.normalizers.*
from src.normalizers.phone import normalize_phone
from src.normalizers.date import normalize_date
from src.normalizers.location import parse_location
from src.normalizers.skills import canonicalize_skill, load_skill_synonyms

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---------------------------------------------------------------------------
# Source priority (lower number = higher priority)
# ---------------------------------------------------------------------------
_SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.ATS_JSON: 1,
    SourceType.RECRUITER_CSV: 2,

    SourceType.RECRUITER_NOTES: 4,
}


def _normalize_name(name: Optional[str]) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not name:
        return ""
    return re.sub(r"\s+", " ", name.strip().lower())


def _normalize_email(email: str) -> str:
    """Lowercase and strip an e-mail address."""
    return email.strip().lower()


def _normalize_phone_key(phone: str) -> str:
    """Reduce a phone string to a normalized digit key for matching.

    Attempts E.164 normalization first (so '+1-650-555-0101' and
    '(650) 555-0101' both resolve to '16505550101'), falling back
    to simple digit extraction.
    """
    try:
        from src.normalizers.phone import normalize_phone as _norm
        normed, _conf = _norm(phone)
        if normed:
            # Strip the leading '+' for matching purposes
            return normed.lstrip("+")
    except Exception:
        pass
    # Fallback: strip everything except digits
    return re.sub(r"[^0-9]", "", phone.strip())


def _normalize_company(company: Optional[str]) -> str:
    """Lowercase, strip for company matching."""
    if not company:
        return ""
    return company.strip().lower()


# ---------------------------------------------------------------------------
# Union-Find for clustering
# ---------------------------------------------------------------------------

class _UnionFind:
    """Weighted quick-union with path compression."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


# ---------------------------------------------------------------------------
# CandidateMerger
# ---------------------------------------------------------------------------

class CandidateMerger:
    """Matches and merges ``RawCandidate`` records into ``CanonicalProfile`` objects.

    Usage::

        merger = CandidateMerger()
        profiles = merger.match_and_merge(raw_candidates)
    """

    def __init__(self, skill_synonyms_path: str = "config/skill_synonyms.json") -> None:
        self._skill_synonyms_path = skill_synonyms_path
        try:
            _canonical_list, self._synonym_map = load_skill_synonyms(skill_synonyms_path)
        except Exception:
            logger.warning(
                "Could not load skill synonyms from %s — skill canonicalization disabled",
                skill_synonyms_path,
            )
            self._synonym_map = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match_and_merge(self, candidates: list[RawCandidate]) -> list[CanonicalProfile]:
        """Group *candidates* by identity keys, then merge each cluster.

        Returns one ``CanonicalProfile`` per unique person.
        """
        if not candidates:
            return []

        clusters = self._build_clusters(candidates)
        profiles: list[CanonicalProfile] = []
        for cluster in clusters:
            try:
                profile = self._merge_cluster(cluster)
                profiles.append(profile)
            except Exception:
                logger.exception("Failed to merge cluster of %d candidates", len(cluster))
        return profiles

    # ------------------------------------------------------------------
    # Step 1 — Clustering via match keys
    # ------------------------------------------------------------------

    def _build_clusters(self, candidates: list[RawCandidate]) -> list[list[RawCandidate]]:
        """Build identity clusters using Union-Find and match keys."""
        n = len(candidates)
        uf = _UnionFind(n)

        # Build inverted indexes for each match key type
        email_index: dict[str, list[int]] = {}
        name_phone_index: dict[str, list[int]] = {}
        name_company_index: dict[str, list[int]] = {}

        for i, c in enumerate(candidates):
            # Primary: email
            for email in c.emails:
                key = _normalize_email(email)
                if key:
                    email_index.setdefault(key, []).append(i)

            norm_name = _normalize_name(c.full_name)

            # Secondary: name + phone
            if norm_name:
                for phone in c.phones:
                    pkey = _normalize_phone_key(phone)
                    if pkey:
                        composite = f"{norm_name}||{pkey}"
                        name_phone_index.setdefault(composite, []).append(i)

            # Secondary: name + company
            if norm_name:
                comp = _normalize_company(c.current_company)
                if comp:
                    composite = f"{norm_name}||{comp}"
                    name_company_index.setdefault(composite, []).append(i)

        # Union candidates that share any match key
        for group in (email_index, name_phone_index, name_company_index):
            for indices in group.values():
                first = indices[0]
                for idx in indices[1:]:
                    uf.union(first, idx)

        # Collect clusters
        clusters_map: dict[int, list[int]] = {}
        for i in range(n):
            root = uf.find(i)
            clusters_map.setdefault(root, []).append(i)

        return [[candidates[i] for i in cluster] for cluster in clusters_map.values()]

    # ------------------------------------------------------------------
    # Step 2 — Merge a cluster
    # ------------------------------------------------------------------

    def _merge_cluster(self, cluster: list[RawCandidate]) -> CanonicalProfile:
        """Merge a cluster of candidates into a single ``CanonicalProfile``."""
        # Sort by source priority so highest-priority comes first
        cluster_sorted = sorted(cluster, key=lambda c: _SOURCE_PRIORITY.get(c.source_type, 99))

        profile = CanonicalProfile()
        provenance: list[ProvenanceRecord] = []

        # --- Scalar fields (highest-priority non-null wins) ---
        scalar_fields = ["full_name", "headline", "location_raw", "current_company", "current_title"]
        for field_name in scalar_fields:
            for c in cluster_sorted:
                val = getattr(c, field_name, None)
                if val is not None and str(val).strip():
                    if field_name == "location_raw":
                        # Parse location string into Location object
                        try:
                            loc_dict = parse_location(val)
                            profile.location = Location(
                                city=loc_dict.get("city"),
                                region=loc_dict.get("region"),
                                country=loc_dict.get("country"),
                            )
                        except Exception:
                            profile.location = Location()
                            logger.debug("Failed to parse location: %s", val)
                    elif field_name in ("current_company", "current_title"):
                        # These aren't direct fields on CanonicalProfile;
                        # they are recorded in provenance and used if headline is absent.
                        pass
                    else:
                        setattr(profile, field_name, val.strip())
                    provenance.append(ProvenanceRecord(
                        field=field_name,
                        source=c.source_type.value,
                        method=c.extraction_method.value,
                    ))
                    break  # first non-null from highest priority wins

        # --- Build headline from current_company/current_title if missing ---
        if not profile.headline:
            for c in cluster_sorted:
                if c.current_title and c.current_company:
                    profile.headline = f"{c.current_title.strip()} at {c.current_company.strip()}"
                    break
                elif c.current_title:
                    profile.headline = c.current_title.strip()
                    break

        # --- years_experience: maximum across sources ---
        max_yoe: Optional[float] = None
        yoe_source: Optional[RawCandidate] = None
        for c in cluster_sorted:
            if c.years_experience is not None:
                if max_yoe is None or c.years_experience > max_yoe:
                    max_yoe = c.years_experience
                    yoe_source = c
        if max_yoe is not None:
            profile.years_experience = max_yoe
            provenance.append(ProvenanceRecord(
                field="years_experience",
                source=yoe_source.source_type.value if yoe_source else "unknown",
                method=yoe_source.extraction_method.value if yoe_source else "unknown",
            ))

        # --- Array fields: union / deduplicate ---

        # Emails
        seen_emails: set[str] = set()
        merged_emails: list[str] = []
        for c in cluster_sorted:
            for email in c.emails:
                norm = _normalize_email(email)
                if not norm or not _EMAIL_RE.match(norm):
                    continue
                if norm not in seen_emails:
                    seen_emails.add(norm)
                    merged_emails.append(norm)
                    provenance.append(ProvenanceRecord(
                        field="emails",
                        source=c.source_type.value,
                        method=c.extraction_method.value,
                    ))
        profile.emails = merged_emails

        # Phones (normalized to E.164)
        seen_phones: set[str] = set()
        merged_phones: list[str] = []
        for c in cluster_sorted:
            for phone in c.phones:
                try:
                    normed_result = normalize_phone(phone)
                    # normalize_phone returns (str|None, float)
                    normed_str = normed_result[0]
                except Exception:
                    normed_str = None
                if not normed_str:
                    continue
                key = _normalize_phone_key(normed_str)
                if key and key not in seen_phones:
                    seen_phones.add(key)
                    merged_phones.append(normed_str)
                    provenance.append(ProvenanceRecord(
                        field="phones",
                        source=c.source_type.value,
                        method=c.extraction_method.value,
                    ))
        profile.phones = merged_phones

        # Skills (canonicalize, deduplicate by canonical name)
        skill_map: dict[str, CanonicalSkill] = {}
        for c in cluster_sorted:
            for raw_skill in c.skills:
                canonical_name, confidence = self._canonicalize_raw_skill(raw_skill)
                existing = skill_map.get(canonical_name)
                if existing is None:
                    skill_map[canonical_name] = CanonicalSkill(
                        name=canonical_name,
                        confidence=confidence,
                        sources=[c.source_type.value],
                    )
                else:
                    # Merge sources list (deduplicated)
                    src_val = c.source_type.value
                    if src_val not in existing.sources:
                        existing.sources.append(src_val)
                    # Keep higher confidence
                    if confidence > existing.confidence:
                        existing.confidence = confidence
                provenance.append(ProvenanceRecord(
                    field="skills",
                    source=c.source_type.value,
                    method=raw_skill.method.value,
                ))
        profile.skills = list(skill_map.values())

        # Experience (deduplicate by company+title+start)
        exp_keys: set[str] = set()
        merged_exp: list[Experience] = []
        for c in cluster_sorted:
            for raw_exp in c.experience:
                exp_obj = self._normalize_experience(raw_exp)
                if exp_obj is None:
                    continue
                key = f"{exp_obj.company.lower()}|{exp_obj.title.lower()}|{exp_obj.start or ''}"
                if key not in exp_keys:
                    exp_keys.add(key)
                    merged_exp.append(exp_obj)
                    provenance.append(ProvenanceRecord(
                        field="experience",
                        source=c.source_type.value,
                        method=raw_exp.method.value,
                    ))
        profile.experience = merged_exp

        # Education (deduplicate by institution+degree)
        edu_keys: set[str] = set()
        merged_edu: list[Education] = []
        for c in cluster_sorted:
            for raw_edu in c.education:
                edu_obj = self._normalize_education(raw_edu)
                if edu_obj is None:
                    continue
                key = f"{edu_obj.institution.lower()}|{(edu_obj.degree or '').lower()}"
                if key not in edu_keys:
                    edu_keys.add(key)
                    merged_edu.append(edu_obj)
                    provenance.append(ProvenanceRecord(
                        field="education",
                        source=c.source_type.value,
                        method=raw_edu.method.value,
                    ))
        profile.education = merged_edu

        # Links (union)
        links = Links()
        other_links_set: set[str] = set()
        for c in cluster_sorted:
            if c.portfolio_url and not links.portfolio:
                links.portfolio = c.portfolio_url.strip()
            for link in c.other_links:
                stripped = link.strip()
                if stripped and stripped not in other_links_set:
                    other_links_set.add(stripped)
        links.other = list(other_links_set)
        profile.links = links

        # Finalize
        profile.provenance = provenance
        profile.candidate_id = profile.generate_candidate_id()
        return profile

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _canonicalize_raw_skill(self, raw: RawSkill) -> tuple[str, float]:
        """Return (canonical_name, confidence) for a raw skill."""
        try:
            # canonicalize_skill returns (name, confidence, method) — 3-tuple
            name, confidence, method = canonicalize_skill(raw.name, self._synonym_map)
            return name, confidence
        except Exception:
            # Fallback: title-case the original name
            return raw.name.strip().title(), 0.5

    def _normalize_experience(self, raw: RawExperience) -> Optional[Experience]:
        """Convert a ``RawExperience`` into a canonical ``Experience``."""
        company = (raw.company or "").strip()
        title = (raw.title or "").strip()
        if not company and not title:
            return None

        start: Optional[str] = None
        end: Optional[str] = None
        if raw.start:
            try:
                start = normalize_date(raw.start)
            except Exception:
                start = raw.start.strip()
        if raw.end:
            try:
                end = normalize_date(raw.end)
            except Exception:
                end = raw.end.strip()

        return Experience(
            company=company or "Unknown",
            title=title or "Unknown",
            start=start,
            end=end,
            summary=(raw.summary or "").strip() or None,
        )

    def _normalize_education(self, raw: RawEducation) -> Optional[Education]:
        """Convert a ``RawEducation`` into a canonical ``Education``."""
        institution = (raw.institution or "").strip()
        if not institution:
            return None

        return Education(
            institution=institution,
            degree=(raw.degree or "").strip() or None,
            field=(raw.field or "").strip() or None,
            end_year=raw.end_year,
        )
