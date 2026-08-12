from services.screening.phonetic import phonetic_codes, phonetic_match


def test_mohammed_muhammad_share_a_primary_code() -> None:
    """The exact case PLAN.md names: two transliterations of the same name must produce the
    same phonetic code -- confirmed directly against the real metaphone library."""
    assert phonetic_codes("Mohammed") == ("MHMT", "")
    assert phonetic_codes("Muhammad") == ("MHMT", "")


def test_phonetic_match_true_for_transliteration_variants() -> None:
    assert phonetic_match("Mohammed", "Muhammad") is True


def test_phonetic_match_false_for_unrelated_names() -> None:
    assert phonetic_match("Mohammed", "Katarzyna") is False


def test_phonetic_match_checks_cross_primary_secondary_combinations() -> None:
    # Smith=('SM0','XMT'), Schmidt=('XMT','SMT'): primary-vs-primary (SM0 vs XMT) does NOT
    # match, and neither does secondary-vs-secondary (XMT vs SMT) -- the only overlap is
    # Smith's *secondary* code equalling Schmidt's *primary* code. A naive implementation that
    # only compared primary-to-primary would incorrectly report these as unrelated.
    assert phonetic_codes("Smith") == ("SM0", "XMT")
    assert phonetic_codes("Schmidt") == ("XMT", "SMT")
    assert phonetic_match("Smith", "Schmidt") is True
