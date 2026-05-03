"""
dob.settings.filters
~~~~~~~~~~~~~~~~~~~~
Filter value parser shared by RowPickerScreen and ObservationScreen.
"""

from __future__ import annotations

from typing import Any


def parse_filter_value(raw: str) -> Any:
    """
    Convert a raw string (from user input) to a typed filter value.

    Rules:
      "NULL"        → None   (IS NULL)
      ""            → ""     (caller treats as 'clear filter')
      integer-like  → int
      float-like    → float
      else          → str
    """
    text = raw.strip()
    if text.upper() == "NULL":
        return None
    if text == "":
        return ""
    if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
        try:
            return int(text)
        except Exception:
            pass
    try:
        if any(ch in text for ch in (".", "e", "E")):
            return float(text)
    except Exception:
        pass
    return text
