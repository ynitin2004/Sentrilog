"""Name normalisation and the OFAC CSV parser, on hand-written rows in the exports' real
format (latin-1, "-0-" for empty fields, a trailing 0x1A row)."""

import pytest

from services.screening.names import normalize_name
from services.screening.ofac import content_hash, parse_sdn

_SDN = (
    '36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \r\n'
    '2676,"AL ZAWAHIRI, Dr. Ayman","individual","SDGT","Leader",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,'
    '"DOB 19 Jun 1951."\r\n'
    '9001,"MÜLLER, Jörg","individual","RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- '
    "\r\n"
    "\x1a"
).encode("latin-1")
_ALT = (
    '36,12,"aka","AERO-CARIBBEAN",-0- \r\n'
    '2676,100,"aka","ZAWAHIRI, Aiman",-0- \r\n'
    '2676,101,"aka",-0- ,-0- \r\n'
).encode("latin-1")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AL ZAWAHIRI, Dr. Ayman", "dr ayman al zawahiri"),
        ("Muhammad Al-Rashid", "muhammad al-rashid"),
        ("MÜLLER, Jörg", "jorg muller"),
        ("  JOHN   ROBERT  SMITH ", "john robert smith"),
        ("O'NEILL, Sean", "sean o'neill"),
        (",", ""),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_mrz_order_and_list_order_normalise_to_the_same_string() -> None:
    # The MRZ path yields "GIVEN SURNAME"; OFAC lists "SURNAME, Given". Screening only works if
    # both sides meet in one form.
    assert normalize_name("AYMAN AL ZAWAHIRI") == normalize_name("AL ZAWAHIRI, Ayman")


def test_parse_sdn_keeps_individuals_and_their_aliases_only() -> None:
    entries = parse_sdn(_SDN, _ALT)
    assert [(e.entity_id, e.name, e.is_alias) for e in entries] == [
        ("2676", "AL ZAWAHIRI, Dr. Ayman", False),
        ("9001", "MÜLLER, Jörg", False),
        ("2676", "ZAWAHIRI, Aiman", True),
    ]
    assert entries[2].program == "SDGT"  # an alias inherits its party's programme


def test_content_hash_changes_when_only_an_alias_changes() -> None:
    assert content_hash(_SDN, _ALT) != content_hash(_SDN, _ALT.replace(b"Aiman", b"Aimen"))
