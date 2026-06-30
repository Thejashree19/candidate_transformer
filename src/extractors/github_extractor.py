"""
Extractor for GitHub developer profiles.

Fetches a user profile and their repositories via the GitHub REST API,
then maps the data to :class:`RawCandidate`.  If the envelope already
contains cached API responses (a dict with ``profile`` and ``repos``
keys), those are used instead of live HTTP calls.

Skills are derived from:
* The primary ``language`` of each repository.
* Repository ``topics`` arrays.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from src.models import (
    ExtractionMethod,
    RawCandidate,
    RawSkill,
    SourceEnvelope,
    SourceStatus,
    SourceType,
)

from .base import BaseExtractor

_GITHUB_API_BASE = "https://api.github.com"
_TIMEOUT = 10.0  # seconds


class GitHubExtractor(BaseExtractor):
    """Fetch a GitHub profile and derive a :class:`RawCandidate`."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, envelope: SourceEnvelope) -> list[RawCandidate]:
        """Extract a single candidate from a GitHub profile.

        ``envelope.raw_data`` should be one of:
        * A GitHub username as a plain string.
        * A GitHub profile URL (``https://github.com/<username>``).
        * A dict with pre-fetched ``profile`` and ``repos`` keys.

        Args:
            envelope: Source envelope with GitHub user data or username.

        Returns:
            A single-element list with the extracted candidate, or ``[]``.
        """
        raw = envelope.raw_data

        # --- Determine whether we have cached data or need to fetch ---
        if isinstance(raw, dict) and "profile" in raw and "repos" in raw:
            profile = raw["profile"]
            repos = raw["repos"]
            if not isinstance(profile, dict):
                envelope.status = SourceStatus.MALFORMED
                envelope.error_message = "Cached profile data is not a dict."
                return []
            if not isinstance(repos, list):
                repos = []
        elif isinstance(raw, dict) and "username" in raw:
            # Pipeline provides {"username": "X"} when no cache hit
            username = raw["username"]
            profile, repos = self._fetch_github(username, envelope)
            if profile is None:
                return []
        else:
            username = self._resolve_username(raw)
            if not username:
                envelope.status = SourceStatus.MALFORMED
                envelope.error_message = (
                    "Could not determine a GitHub username from raw_data."
                )
                return []

            profile, repos = self._fetch_github(username, envelope)
            if profile is None:
                return []

        candidate = self._build_candidate(profile, repos)
        return [candidate] if candidate else []

    # ------------------------------------------------------------------
    # Username resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_username(raw: Any) -> str | None:
        """Extract a GitHub username from a string (URL or plain name).

        Returns ``None`` if no valid username can be determined.
        """
        if not isinstance(raw, str):
            return None

        text = raw.strip().rstrip("/")
        if not text:
            return None

        # Handle full GitHub URLs.
        match = re.match(
            r"https?://(?:www\.)?github\.com/([A-Za-z0-9_-]+)", text
        )
        if match:
            return match.group(1)

        # Treat a bare token (no spaces, no slashes) as a username.
        if re.fullmatch(r"[A-Za-z0-9_-]+", text):
            return text

        return None

    # ------------------------------------------------------------------
    # HTTP fetch
    # ------------------------------------------------------------------

    @staticmethod
    def _fetch_github(
        username: str, envelope: SourceEnvelope
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Fetch profile and repos from the GitHub REST API.

        Returns ``(profile_dict, repos_list)`` on success, or
        ``(None, [])`` on failure (envelope is updated accordingly).
        """
        headers = {"Accept": "application/vnd.github+json"}

        try:
            with httpx.Client(timeout=_TIMEOUT, headers=headers) as client:
                # -- Profile --
                profile_resp = client.get(
                    f"{_GITHUB_API_BASE}/users/{username}"
                )

                if profile_resp.status_code == 404:
                    envelope.status = SourceStatus.EMPTY
                    envelope.error_message = (
                        f"GitHub user '{username}' not found."
                    )
                    return None, []

                if profile_resp.status_code == 403:
                    envelope.status = SourceStatus.FAILED
                    envelope.error_message = (
                        "GitHub API rate limit exceeded (HTTP 403)."
                    )
                    return None, []

                profile_resp.raise_for_status()
                profile: dict[str, Any] = profile_resp.json()

                # -- Repos --
                repos_resp = client.get(
                    f"{_GITHUB_API_BASE}/users/{username}/repos",
                    params={"per_page": 100, "sort": "pushed"},
                )
                repos: list[dict[str, Any]] = []
                if repos_resp.status_code == 200:
                    repos = repos_resp.json()
                    if not isinstance(repos, list):
                        repos = []

        except httpx.TimeoutException:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = (
                f"Timeout while fetching GitHub data for '{username}'."
            )
            return None, []
        except httpx.HTTPError as exc:
            envelope.status = SourceStatus.FAILED
            envelope.error_message = (
                f"HTTP error fetching GitHub data for '{username}': {exc}"
            )
            return None, []

        return profile, repos

    # ------------------------------------------------------------------
    # Candidate construction
    # ------------------------------------------------------------------

    def _build_candidate(
        self,
        profile: dict[str, Any],
        repos: list[dict[str, Any]],
    ) -> RawCandidate | None:
        """Build a :class:`RawCandidate` from profile + repo data."""

        def _pstr(key: str) -> str | None:
            val = profile.get(key)
            if val is None:
                return None
            text = str(val).strip()
            return text or None

        full_name = _pstr("name")
        email = _pstr("email")
        bio = _pstr("bio")
        location = _pstr("location")
        company = _pstr("company")
        blog = _pstr("blog")
        html_url = _pstr("html_url")

        emails: list[str] = [email] if email else []

        # Aggregate skills from repo languages and topics.
        skills = self._aggregate_skills(repos)

        # Build other_links from blog if present.
        other_links: list[str] = []
        if blog:
            # Ensure blog URL has a scheme.
            if not blog.startswith(("http://", "https://")):
                blog = f"https://{blog}"
            other_links.append(blog)

        return RawCandidate(
            full_name=full_name,
            emails=emails,
            headline=bio,
            location_raw=location,
            current_company=company,
            github_url=html_url,
            other_links=other_links,
            skills=skills,
            source_type=SourceType.GITHUB,
            extraction_method=ExtractionMethod.API_FETCH,
        )

    @staticmethod
    def _aggregate_skills(
        repos: list[dict[str, Any]],
    ) -> list[RawSkill]:
        """Derive skills from repository languages and topics.

        Each unique language / topic is emitted once as a :class:`RawSkill`.
        """
        seen: set[str] = set()
        skills: list[RawSkill] = []

        for repo in repos:
            if not isinstance(repo, dict):
                continue

            # Primary language.
            lang = repo.get("language")
            if isinstance(lang, str) and lang.strip():
                key = lang.strip().lower()
                if key not in seen:
                    seen.add(key)
                    skills.append(
                        RawSkill(
                            name=lang.strip(),
                            source=SourceType.GITHUB,
                            method=ExtractionMethod.API_FETCH,
                        )
                    )

            # Topics.
            topics = repo.get("topics")
            if isinstance(topics, list):
                for topic in topics:
                    if isinstance(topic, str) and topic.strip():
                        key = topic.strip().lower()
                        if key not in seen:
                            seen.add(key)
                            skills.append(
                                RawSkill(
                                    name=topic.strip(),
                                    source=SourceType.GITHUB,
                                    method=ExtractionMethod.API_FETCH,
                                )
                            )

        return skills
