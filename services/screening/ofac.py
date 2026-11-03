"""Reads OFAC's Specially Designated Nationals list from its two public CSV exports: SDN.CSV (one
row per listed party) and ALT.CSV (its aliases, keyed by the same ent_num). US-government public
domain data (D27).

Only individuals are kept. Customers screened here are people; entity, vessel and aircraft names
would add ~12,000 vectors that can only ever produce false positives against a person's name, and
would triple the ingest and evaluation time. Business screening (KYB) is a different product
surface and would screen against the entity rows.
"""

import csv
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import httpx

SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
ALT_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ALT.CSV"

LIST_SOURCE = "OFAC-SDN"

# OFAC's CSVs mark an empty field with this literal rather than leaving it empty.
_EMPTY = "-0-"


@dataclass(frozen=True)
class ListEntry:
    entity_id: str  # OFAC ent_num, shared by a party's primary name and all its aliases
    name: str  # as published, before normalisation
    is_alias: bool
    program: str | None


def _field(value: str) -> str | None:
    value = value.strip()
    return None if value in ("", _EMPTY) else value


def parse_sdn(sdn_csv: bytes, alt_csv: bytes) -> list[ListEntry]:
    entries: list[ListEntry] = []
    individuals: dict[str, str | None] = {}

    # latin-1, not utf-8: the exports are Windows-1252-ish and contain bytes utf-8 rejects.
    for row in csv.reader(io.StringIO(sdn_csv.decode("latin-1"))):
        # The file ends with a lone 0x1A (DOS end-of-file) row.
        if len(row) < 4 or _field(row[2]) != "individual":
            continue
        entity_id, name, program = row[0].strip(), row[1].strip(), _field(row[3])
        individuals[entity_id] = program
        entries.append(ListEntry(entity_id=entity_id, name=name, is_alias=False, program=program))

    for row in csv.reader(io.StringIO(alt_csv.decode("latin-1"))):
        if len(row) < 4:
            continue
        entity_id, alias = row[0].strip(), _field(row[3])
        if alias is None or entity_id not in individuals:
            continue
        entries.append(
            ListEntry(
                entity_id=entity_id, name=alias, is_alias=True, program=individuals[entity_id]
            )
        )
    return entries


def content_hash(sdn_csv: bytes, alt_csv: bytes) -> str:
    """Hash of both files together: a changed alias with an unchanged SDN.CSV is a new list."""
    digest = hashlib.sha256()
    for part in (sdn_csv, alt_csv):
        digest.update(hashlib.sha256(part).digest())
    return digest.hexdigest()


def download() -> tuple[bytes, bytes]:
    import ssl

    import truststore

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with httpx.Client(verify=context, timeout=120, follow_redirects=True) as c:
        sdn = c.get(SDN_URL)
        sdn.raise_for_status()
        alt = c.get(ALT_URL)
        alt.raise_for_status()
    return sdn.content, alt.content


def read_from_dir(directory: Path) -> tuple[bytes, bytes]:
    """Reads SDN.CSV/ALT.CSV, or the gzipped copies the evaluation harness pins."""
    import gzip

    def read(stem: str) -> bytes:
        plain, gz = directory / f"{stem}.CSV", directory / f"{stem}.CSV.gz"
        if gz.exists():
            return gzip.decompress(gz.read_bytes())
        return plain.read_bytes()

    return read("SDN"), read("ALT")
