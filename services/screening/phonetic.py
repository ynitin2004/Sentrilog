"""Double Metaphone phonetic matching -- catches transliteration variants a vector embedding
alone can miss or over/under-weight, e.g. "Mohammed" vs "Muhammad" (both produce the primary
code MHMT, confirmed directly against the real metaphone package, not assumed).
"""

from metaphone import doublemetaphone


def phonetic_codes(name: str) -> tuple[str, str]:
    """Returns (primary, secondary) codes; secondary may be ''."""
    primary, secondary = doublemetaphone(name)
    return primary, secondary


def phonetic_match(name_a: str, name_b: str) -> bool:
    """True if either name's primary or secondary code overlaps with the other's -- Double
    Metaphone's whole point is that a name can have two plausible pronunciations, so requiring
    only the primary codes to match would silently drop matches it was designed to catch.
    """
    a_primary, a_secondary = phonetic_codes(name_a)
    b_primary, b_secondary = phonetic_codes(name_b)
    a_codes = {c for c in (a_primary, a_secondary) if c}
    b_codes = {c for c in (b_primary, b_secondary) if c}
    return bool(a_codes & b_codes)
