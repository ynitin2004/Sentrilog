"""Screens a name against the sanctions list via two independent methods, tuned for recall
per PLAN.md: vector similarity catches names that read differently but mean the same thing,
phonetic matching catches names that sound the same but embed differently. Run both rather
than picking one -- a hit either method would have missed on its own is exactly the failure
mode this design exists to avoid.
"""

from dataclasses import dataclass

from .embeddings import EmbeddingClient
from .phonetic import phonetic_codes
from .qdrant_store import search_by_phonetic_codes, search_by_vector


@dataclass
class SanctionsHit:
    list_source: str
    matched_name: str
    match_score: float
    method: str  # "vector" | "phonetic"


def screen_name(
    client: EmbeddingClient, name: str, *, collection_name: str | None = None
) -> list[SanctionsHit]:
    hits: list[SanctionsHit] = []

    vector = client.embed(name)
    for point in search_by_vector(vector, collection_name=collection_name):
        payload = point.payload or {}
        hits.append(
            SanctionsHit(
                list_source=payload["list_source"],
                matched_name=payload["name"],
                match_score=point.score,
                method="vector",
            )
        )

    primary, secondary = phonetic_codes(name)
    codes = [c for c in (primary, secondary) if c]
    seen = {(h.list_source, h.matched_name) for h in hits}
    for record in search_by_phonetic_codes(codes, collection_name=collection_name):
        payload = record.payload or {}
        key = (payload["list_source"], payload["name"])
        if key in seen:
            # Already surfaced by vector search -- record it once, not once per method, so a
            # strong double-signal match doesn't look like two separate weaker hits.
            continue
        seen.add(key)
        hits.append(
            SanctionsHit(
                list_source=payload["list_source"],
                matched_name=payload["name"],
                match_score=1.0,  # phonetic match is a boolean code match, not a distance score
                method="phonetic",
            )
        )

    return hits
