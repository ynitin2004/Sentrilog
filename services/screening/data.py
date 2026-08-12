"""A small, clearly-synthetic sample sanctions list for local dev/testing -- NOT the real OFAC
SDN or UN Consolidated list. PLAN.md Phase 6 scope is explicitly "sample OFAC/UN list into
Qdrant"; pulling and continuously refreshing the real public feeds is a production ingestion
concern (Phase 9/10), not something to fake data for here. Every name below is invented.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SanctionsEntry:
    list_source: str
    name: str


SAMPLE_SANCTIONS_LIST: list[SanctionsEntry] = [
    # The transliteration-variant case PLAN.md names explicitly -- both spellings are listed
    # separately, as real sanctions lists often do, since screening must catch either spelling
    # of an incoming name regardless of which one the list itself uses.
    SanctionsEntry("OFAC-SAMPLE", "Mohammed Al-Rashid"),
    SanctionsEntry("UN-SAMPLE", "Muhammad Al-Rashid"),
    SanctionsEntry("OFAC-SAMPLE", "Aleksandr Petrov"),
    SanctionsEntry("UN-SAMPLE", "Alexander Petrov"),
    SanctionsEntry("OFAC-SAMPLE", "Viktor Ivanovich Sokolov"),
    SanctionsEntry("UN-SAMPLE", "Chen Wei Ming"),
    SanctionsEntry("OFAC-SAMPLE", "Sofia Marquez Delgado"),
    SanctionsEntry("UN-SAMPLE", "Jean-Baptiste Moreau"),
    SanctionsEntry("OFAC-SAMPLE", "Katarzyna Nowak"),
    SanctionsEntry("UN-SAMPLE", "Ahmed Hassan Ibrahim"),
]
