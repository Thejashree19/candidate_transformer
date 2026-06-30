"""
Location parser.

Splits raw location strings (e.g. ``"San Francisco, CA"``,
``"London, UK"``) into structured ``{city, region, country}`` dicts
with ISO 3166 alpha-2 country codes.
"""

from __future__ import annotations

from typing import Any

import pycountry

# -----------------------------------------------------------------------
# US state abbreviations → full names
# -----------------------------------------------------------------------

US_STATES: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# Common country aliases not always resolved by pycountry's fuzzy search.
_COUNTRY_ALIASES: dict[str, str] = {
    "usa": "US",
    "u.s.a.": "US",
    "u.s.": "US",
    "united states": "US",
    "united states of america": "US",
    "uk": "GB",
    "u.k.": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "south korea": "KR",
    "north korea": "KP",
    "russia": "RU",
    "taiwan": "TW",
    "iran": "IR",
    "syria": "SY",
    "vietnam": "VN",
    "venezuela": "VE",
    "bolivia": "BO",
    "tanzania": "TZ",
    "czech republic": "CZ",
    "czechia": "CZ",
    "the netherlands": "NL",
    "holland": "NL",
}


def _resolve_country(text: str) -> str | None:
    """Return ISO 3166 alpha-2 code for *text*, or ``None``.

    Tries, in order:
    1. Direct alpha-2 match (e.g. ``"US"``).
    2. Hard-coded alias table.
    3. ``pycountry.countries.lookup`` (exact name / alpha-3 / numeric).
    4. ``pycountry.countries.search_fuzzy`` (fuzzy name match).
    """
    if not text:
        return None

    upper = text.strip().upper()

    # 1. Direct alpha-2
    try:
        country = pycountry.countries.get(alpha_2=upper)
        if country is not None:
            return country.alpha_2
    except (KeyError, LookupError):
        pass

    # 2. Alias table
    lower = text.strip().lower()
    if lower in _COUNTRY_ALIASES:
        return _COUNTRY_ALIASES[lower]

    # 3. Exact lookup (name, alpha-3, numeric)
    try:
        country = pycountry.countries.lookup(text.strip())
        return country.alpha_2
    except LookupError:
        pass

    # 4. Fuzzy search
    try:
        results = pycountry.countries.search_fuzzy(text.strip())
        if results:
            return results[0].alpha_2
    except LookupError:
        pass

    return None


def parse_location(raw: str) -> dict[str, Any]:
    """Parse a raw location string into structured components.

    Parameters
    ----------
    raw:
        A free-form location string such as ``"San Francisco, CA"``,
        ``"New York, NY, USA"``, ``"London, UK"``, ``"Berlin, Germany"``,
        or ``"Bangalore, India"``.

    Returns
    -------
    dict
        ``{"city": str | None, "region": str | None, "country": str | None}``
        where *country* is an ISO 3166 alpha-2 code when resolved.
    """
    empty: dict[str, Any] = {"city": None, "region": None, "country": None}

    if not raw or not isinstance(raw, str):
        return empty

    cleaned = raw.strip()
    if not cleaned:
        return empty

    parts = [p.strip() for p in cleaned.split(",") if p.strip()]

    if not parts:
        return empty

    city: str | None = None
    region: str | None = None
    country: str | None = None

    if len(parts) == 1:
        # Could be a city, state abbreviation, or country by itself.
        token = parts[0]
        token_upper = token.upper()

        # Is it a US state abbreviation?
        if token_upper in US_STATES:
            region = US_STATES[token_upper]
            country = "US"
        else:
            # Try as a country first.
            resolved = _resolve_country(token)
            if resolved:
                country = resolved
            else:
                # Treat as a bare city.
                city = token

    elif len(parts) == 2:
        # "City, State" or "City, Country"
        token = parts[1].strip()
        token_upper = token.upper()

        if token_upper in US_STATES:
            city = parts[0]
            region = US_STATES[token_upper]
            country = "US"
        else:
            resolved = _resolve_country(token)
            if resolved:
                city = parts[0]
                country = resolved
            else:
                # Treat as "City, Region"
                city = parts[0]
                region = token

    elif len(parts) >= 3:
        # "City, State/Region, Country"
        city = parts[0]

        # Try the last part as country.
        country_token = parts[-1].strip()
        resolved = _resolve_country(country_token)
        if resolved:
            country = resolved
        # If last part is a US state abbreviation and no country resolved,
        # it's likely "City, SubArea, State".
        region_token = parts[1].strip()
        region_upper = region_token.upper()

        if region_upper in US_STATES:
            region = US_STATES[region_upper]
            if country is None:
                country = "US"
        else:
            region = region_token

    return {"city": city, "region": region, "country": country}
