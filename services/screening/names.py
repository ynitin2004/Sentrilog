"""One normalisation for every name that reaches screening, applied identically to list entries
at ingest and to the incoming name at screen time -- the two sides of a similarity score have to
be in the same form, or the score measures formatting rather than identity.

OFAC writes individuals as "SURNAME, Given Names"; the MRZ path produces "GIVEN NAMES SURNAME"
(services/pipeline/extraction/mrz.py). Reordering, case-folding, stripping accents and collapsing
punctuation removes all of that before a vector or phonetic code is ever computed.
"""

import re
import unicodedata

_NON_NAME_CHARS = re.compile(r"[^a-z0-9' -]+")
_WHITESPACE = re.compile(r"\s+")


def normalize_name(raw: str) -> str:
    name = raw.strip()
    # "SURNAME, Given" -> "Given SURNAME". Only the first comma: a second one is part of a
    # title or suffix ("ABBAS, Abu, Jr.") and is dropped by the character filter below.
    if "," in name:
        surname, _, given = name.partition(",")
        if given.strip():
            name = f"{given.strip()} {surname.strip()}"
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    lowered = ascii_only.casefold()
    cleaned = _NON_NAME_CHARS.sub(" ", lowered)
    return _WHITESPACE.sub(" ", cleaned).strip()
