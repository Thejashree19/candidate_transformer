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
    PhoneEntry,
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
from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ---------------------------------------------------------------------------
# Source priority (lower number = higher priority)
# ---------------------------------------------------------------------------
_SOURCE_PRIORITY: dict[SourceType, int] = {
    SourceType.ATS_JSON: 1,
    SourceType.RECRUITER_CSV: 2,
    SourceType.RESUME: 3,
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
        
        name_comp_list: list[tuple[int, str]] = []

        for i, c in enumerate(candidates):
            # Primary: email
            for email in getattr(c, "emails", []):
                key = _normalize_email(email)
                if key:
                    email_index.setdefault(key, []).append(i)

            norm_name = _normalize_name(getattr(c, "full_name", None))

            # Secondary: name + phone
            if norm_name:
                for phone in getattr(c, "phones", []):
                    pkey = _normalize_phone_key(phone)
                    if pkey:
                        composite = f"{norm_name}||{pkey}"
                        name_phone_index.setdefault(composite, []).append(i)

            # Secondary: name + company
            if norm_name:
                comp = _normalize_company(getattr(c, "current_company", None))
                if comp:
                    composite = f"{norm_name} {comp}"
                    name_comp_list.append((i, composite))

        # Union candidates that share any match key
        for group in (email_index, name_phone_index):
            for indices in group.values():
                first = indices[0]
                for idx in indices[1:]:
                    uf.union(first, idx)

        # Fuzzy matching for name+company as last resort
        for i in range(len(name_comp_list)):
            for j in range(i + 1, len(name_comp_list)):
                idx1, str1 = name_comp_list[i]
                idx2, str2 = name_comp_list[j]
                if uf.find(idx1) != uf.find(idx2):
                    if fuzz.token_sort_ratio(str1, str2) >= 90.0:
                        uf.union(idx1, idx2)
                        setattr(candidates[idx1], "_low_confidence_match", True)
                        setattr(candidates[idx2], "_low_confidence_match", True)

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
        is_low_conf = any(getattr(c, "_low_confidence_match", False) for c in cluster)

        profile = CanonicalProfile()
        profile.low_confidence_match = is_low_conf
        provenance: list[ProvenanceRecord] = []

        # Helper to score scalar values
        # priority: 1) corroboration count, 2) source reliability, 3) recency
        def _resolve_scalar(field_name: str) -> None:
            val_map: dict[str, list[RawCandidate]] = {}
            orig_val_map: dict[str, Any] = {}
            for c in cluster:
                val = getattr(c, field_name, None)
                if val is not None and str(val).strip():
                    norm_val = str(val).strip().lower()
                    val_map.setdefault(norm_val, []).append(c)
                    orig_val_map[norm_val] = val

            if not val_map:
                return

            best_val_norm = None
            best_score = (-1, -1, "")
            weight_map = {1: 0.95, 2: 0.85, 3: 0.85, 4: 0.40, 99: 0.0}

            for norm_val, c_list in val_map.items():
                corrob = len(c_list)
                best_weight = max(weight_map.get(_SOURCE_PRIORITY.get(c.source_type, 99), 0.0) for c in c_list)
                best_recency = max((getattr(c, "fetched_at", "")) for c in c_list)
                
                score = (corrob, best_weight, best_recency)
                if score > best_score:
                    best_score = score
                    best_val_norm = norm_val

            winning_val = orig_val_map[best_val_norm]
            if field_name == "location_raw":
                try:
                    loc_dict = parse_location(winning_val)
                    profile.location = Location(
                        city=loc_dict.get("city"),
                        region=loc_dict.get("region"),
                        country=loc_dict.get("country"),
                    )
                except Exception:
                    profile.location = Location()
            elif field_name in ("current_company", "current_title"):
                pass
            else:
                setattr(profile, field_name, str(winning_val).strip())

            for norm_val, c_list in val_map.items():
                is_alt = (norm_val != best_val_norm)
                for c in c_list:
                    provenance.append(ProvenanceRecord(
                        field=field_name,
                        source=c.source_type.value,
                        method=c.extraction_method.value,
                        value=orig_val_map[norm_val],
                        is_alternate=is_alt
                    ))

        # --- Scalar fields ---
        for field_name in ["full_name", "headline", "location_raw", "current_company", "current_title"]:
            _resolve_scalar(field_name)

        # --- Build headline from current_company/current_title if missing ---
        if not profile.headline:
            title_vals = [c.current_title for c in cluster if getattr(c, "current_title", None)]
            comp_vals = [c.current_company for c in cluster if getattr(c, "current_company", None)]
            if title_vals and comp_vals:
                profile.headline = f"{title_vals[0].strip()} at {comp_vals[0].strip()}"
            elif title_vals:
                profile.headline = title_vals[0].strip()

        # --- years_experience: maximum across sources ---
        max_yoe: Optional[float] = None
        yoe_sources: list[RawCandidate] = []
        for c in cluster:
            yoe = getattr(c, "years_experience", None)
            if yoe is not None:
                if max_yoe is None or yoe > max_yoe:
                    max_yoe = yoe
                    yoe_sources = [c]
                elif yoe == max_yoe:
                    yoe_sources.append(c)

        if max_yoe is not None:
            profile.years_experience = max_yoe
            for c in yoe_sources:
                provenance.append(ProvenanceRecord(
                    field="years_experience",
                    source=c.source_type.value,
                    method=c.extraction_method.value,
                    value=max_yoe,
                    is_alternate=False
                ))

        # --- Array fields: union / deduplicate ---

        # Emails
        seen_emails: set[str] = set()
        merged_emails: list[str] = []
        for c in cluster:
            for email in getattr(c, "emails", []):
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
                        value=email,
                        is_alternate=False
                    ))
        profile.emails = merged_emails

        # Phones (normalized to E.164)
        seen_phones: set[str] = set()
        merged_phones: list[PhoneEntry] = []
        for c in cluster:
            for phone in getattr(c, "phones", []):
                try:
                    normed_result = normalize_phone(phone)
                    normed_str = normed_result[0]
                    conf = normed_result[1]
                except Exception:
                    normed_str = None
                    conf = 0.0
                
                if not normed_str:
                    continue
                
                key = _normalize_phone_key(normed_str)
                if key and key not in seen_phones:
                    seen_phones.add(key)
                    merged_phones.append(PhoneEntry(
                        raw=phone,
                        normalized=normed_str,
                        confidence=conf if normed_str else 0.5
                    ))
                    provenance.append(ProvenanceRecord(
                        field="phones",
                        source=c.source_type.value,
                        method=c.extraction_method.value,
                        value=phone,
                        is_alternate=False
                    ))
        profile.phones = merged_phones

        # Skills (canonicalize, deduplicate by canonical name)
        skill_map: dict[str, CanonicalSkill] = {}
        for c in cluster:
            for raw_skill in getattr(c, "skills", []):
                canonical_name, confidence, unmapped = self._canonicalize_raw_skill(raw_skill)
                existing = skill_map.get(canonical_name)
                if existing is None:
                    skill_map[canonical_name] = CanonicalSkill(
                        name=canonical_name,
                        confidence=confidence,
                        sources=[c.source_type.value],
                        unmapped=unmapped
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
                    value=raw_skill.name,
                    is_alternate=False
                ))
        profile.skills = list(skill_map.values())

        # Experience (deduplicate by company+title+start)
        exp_keys: set[str] = set()
        merged_exp: list[Experience] = []
        for c in cluster:
            for raw_exp in getattr(c, "experience", []):
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
                        value=exp_obj.model_dump(),
                        is_alternate=False
                    ))
        profile.experience = merged_exp

        # Education (deduplicate by institution+degree)
        edu_keys: set[str] = set()
        merged_edu: list[Education] = []
        for c in cluster:
            for raw_edu in getattr(c, "education", []):
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
                        value=edu_obj.model_dump(),
                        is_alternate=False
                    ))
        profile.education = merged_edu

        # Links (union)
        links = Links()
        other_links_set: set[str] = set()
        for c in cluster:
            if getattr(c, "portfolio_url", None) and not links.portfolio:
                links.portfolio = c.portfolio_url.strip()
            
            if getattr(c, "github_url", None):
                other_links_set.add(c.github_url.strip())
            if getattr(c, "linkedin_url", None):
                other_links_set.add(c.linkedin_url.strip())

            for link in getattr(c, "other_links", []):
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

    def _canonicalize_raw_skill(self, raw: RawSkill) -> tuple[str, float, bool]:
        """Return (canonical_name, confidence, unmapped) for a raw skill."""
        try:
            name, confidence, method = canonicalize_skill(raw.name, self._synonym_map)
            unmapped = (method == "unmatched")
            return name, confidence, unmapped
        except Exception:
            return raw.name.strip().title(), 0.5, True

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
