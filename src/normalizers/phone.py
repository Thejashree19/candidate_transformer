"""
Phone number normalizer.

Parses raw phone strings into E.164 format using the ``phonenumbers`` library
and assigns a confidence score based on how the number was resolved.
"""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def normalize_phone(
    raw: str,
    default_region: str = "US",
) -> tuple[str | None, float]:
    """Normalize a raw phone string to E.164 format.

    Parameters
    ----------
    raw:
        The raw phone number string (e.g. ``"+14155552671"``,
        ``"(415) 555-2671"``, ``"555-2671"``).
    default_region:
        ISO 3166-1 alpha-2 region code used as a fallback when the number
        does not include an international prefix.  Defaults to ``"US"``.

    Returns
    -------
    tuple[str | None, float]
        A ``(e164_string_or_None, confidence)`` pair.

        * **confidence 1.0** – the number contained an international prefix
          (``+``) and validated successfully.
        * **confidence 0.8** – the number was only valid when parsed with the
          *default_region* fallback.
        * **confidence 0.0** – the number could not be parsed or failed
          validation.
    """
    if not raw or not isinstance(raw, str):
        return None, 0.0

    cleaned = raw.strip()
    if not cleaned:
        return None, 0.0

    # ------------------------------------------------------------------
    # Layer 1: Try parsing *without* a default region (expects a "+" prefix).
    # ------------------------------------------------------------------
    try:
        parsed = phonenumbers.parse(cleaned, None)
        if phonenumbers.is_valid_number(parsed):
            e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
            return e164, 1.0
    except NumberParseException:
        pass  # Fall through to layer 2

    # ------------------------------------------------------------------
    # Layer 2: Parse with the default region.
    # ------------------------------------------------------------------
    try:
        parsed = phonenumbers.parse(cleaned, default_region)
        if phonenumbers.is_valid_number(parsed):
            e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
            return e164, 0.8
    except NumberParseException:
        pass

    return None, 0.0
