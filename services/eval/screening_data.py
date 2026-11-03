"""Builds the labelled screening query set from the pinned OFAC snapshot (D28).

Three kinds of query, each with a known right answer:

- positive: a listed individual's name (primary or alias) altered the way real input differs
  from a list entry -- a transliteration, a typo, a dropped middle name, surname-first order,
  a split or joined particle. The right answer is that individual's OFAC entity id. The
  transforms are recorded per row, so recall can be broken down by what went wrong.
- negative_synthetic: a name assembled from a pool of common given names and surnames across
  regions, checked not to be on the list. The right answer is no hit.
- negative_hard: a listed individual's given name with a pool surname (or the reverse). It
  shares a real token with a listed person, which is where false positives come from in
  practice. The right answer is still no hit.

The noise model is ours, not observed from real traffic, and every report says so. It measures
robustness to these specific variations; it does not estimate a production false-positive rate.

    uv run python -m services.eval build-screening-dataset
"""

import random
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from services.screening.names import normalize_name
from services.screening.ofac import parse_sdn, read_from_dir

from .datasets import DATA_DIR, verify_files, write_jsonl

LIST_DIR = DATA_DIR / "ofac-sdn-20261103"
DATASET_PATH = DATA_DIR / "screening-queries-v1.jsonl"
GENERATOR_VERSION = "screening-queries-v1"
SEED = 20261103

N_POSITIVE = 1500
N_NEGATIVE_SYNTHETIC = 1000
N_NEGATIVE_HARD = 1000

GIVEN_NAMES = [
    "james", "mary", "robert", "patricia", "michael", "jennifer", "david", "linda", "william",
    "elizabeth", "priya", "rahul", "ananya", "vikram", "sunita", "arjun", "deepa", "wei",
    "li", "jing", "hao", "mei", "jun", "yuki", "haruto", "sakura", "minjun", "jiwoo", "carlos",
    "maria", "jose", "lucia", "diego", "sofia", "juan", "camila", "olga", "dmitri", "anna",
    "sergei", "natalia", "ivan", "fatima", "omar", "aisha", "yusuf", "layla", "karim", "amina",
    "kwame", "ngozi", "chinedu", "amara", "tunde", "zanele", "thabo", "lars", "ingrid", "sven",
    "pierre", "camille", "luca", "giulia", "hans", "greta", "piotr", "agnieszka", "mehmet",
    "elif", "nguyen", "thi",
]  # fmt: skip
SURNAMES = [
    "smith", "johnson", "williams", "brown", "jones", "garcia", "miller", "davis", "wilson",
    "taylor", "sharma", "patel", "gupta", "reddy", "iyer", "singh", "nair", "wang", "zhang",
    "liu", "chen", "yang", "huang", "tanaka", "suzuki", "sato", "kim", "park", "lee",
    "rodriguez", "martinez", "lopez", "gonzalez", "perez", "sanchez", "ivanova", "smirnov",
    "popov", "kuznetsova", "volkov", "okafor", "mensah", "adeyemi", "nkosi", "dlamini",
    "abubakar", "haddad", "khoury", "mansour", "nasser", "rahman", "chowdhury", "larsen",
    "johansson", "dubois", "moreau", "rossi", "bianchi", "muller", "schmidt", "kowalski",
    "nowak", "yilmaz", "demir", "tran", "pham", "oconnor", "murphy",
]  # fmt: skip

_TRANSLITERATIONS = [
    ("mohammed", "muhammad"), ("muhammad", "mohammed"), ("mohamed", "mohammad"),
    ("ou", "u"), ("u", "ou"), ("y", "i"), ("i", "y"), ("kh", "h"), ("ph", "f"), ("f", "ph"),
    ("sh", "sch"), ("j", "dj"), ("dj", "j"), ("q", "k"), ("k", "q"), ("ee", "i"), ("oo", "u"),
    ("aa", "a"), ("w", "v"), ("v", "w"), ("ch", "tch"), ("x", "ks"), ("abd al", "abdul"),
    ("abdul", "abd al"), ("el-", "al-"), ("al-", "el-"), ("bin ", "ibn "), ("ibn ", "bin "),
    ("ei", "ey"), ("ey", "ei"), ("c", "k"),
]  # fmt: skip
_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _transliterate(name: str, rng: random.Random) -> str | None:
    options = [(a, b) for a, b in _TRANSLITERATIONS if a in name]
    if not options:
        return None
    a, b = rng.choice(options)
    positions = [i for i in range(len(name)) if name.startswith(a, i)]
    i = rng.choice(positions)
    return name[:i] + b + name[i + len(a) :]


def _typo(name: str, rng: random.Random) -> str | None:
    tokens = name.split()
    eligible = [i for i, t in enumerate(tokens) if len(t) >= 4 and t.isalpha()]
    if not eligible:
        return None
    ti = rng.choice(eligible)
    t = tokens[ti]
    # Not the first letter: real typos and OCR misreads land mid-word far more often.
    p = rng.randrange(1, len(t))
    op = rng.choice(["substitute", "delete", "insert", "transpose"])
    if op == "substitute":
        t = t[:p] + rng.choice(_LETTERS.replace(t[p], "")) + t[p + 1 :]
    elif op == "delete":
        t = t[:p] + t[p + 1 :]
    elif op == "insert":
        t = t[:p] + t[p] + t[p:]
    elif p < len(t) - 1:
        t = t[:p] + t[p + 1] + t[p] + t[p + 2 :]
    else:
        return None
    tokens[ti] = t
    return " ".join(tokens)


def _drop_token(name: str, rng: random.Random) -> str | None:
    tokens = name.split()
    if len(tokens) < 3:
        return None
    del tokens[rng.randrange(1, len(tokens) - 1)]
    return " ".join(tokens)


def _surname_first(name: str, rng: random.Random) -> str | None:
    tokens = name.split()
    if len(tokens) < 2:
        return None
    return " ".join([tokens[-1], *tokens[:-1]])


def _particle(name: str, rng: random.Random) -> str | None:
    if "-" in name:
        return name.replace("-", rng.choice(["", " "]), 1)
    tokens = name.split()
    for i, t in enumerate(tokens[:-1]):
        if t in ("al", "el", "bin", "ibn", "abu", "abd", "van", "de", "da"):
            return " ".join([*tokens[:i], t + tokens[i + 1], *tokens[i + 2 :]])
    return None


TRANSFORMS: dict[str, Callable[[str, random.Random], str | None]] = {
    "transliteration": _transliterate,
    "typo": _typo,
    "drop_token": _drop_token,
    "surname_first": _surname_first,
    "particle": _particle,
}


def build_rows() -> list[dict[str, Any]]:
    verify_files(LIST_DIR)
    entries = parse_sdn(*read_from_dir(LIST_DIR))
    names_by_entity: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        normalized = normalize_name(e.name)
        if normalized and normalized not in names_by_entity[e.entity_id]:
            names_by_entity[e.entity_id].append(normalized)
    listed_names = {n for names in names_by_entity.values() for n in names}
    listed_token_sets = {frozenset(n.split()) for n in listed_names}
    entity_ids = sorted(names_by_entity)

    rng = random.Random(SEED)
    rows: list[dict[str, Any]] = []

    seen_queries: set[str] = set()
    while sum(r["kind"] == "positive" for r in rows) < N_POSITIVE:
        entity_id = rng.choice(entity_ids)
        source = rng.choice(names_by_entity[entity_id])
        query, applied = source, list[str]()
        for name in rng.sample(list(TRANSFORMS), k=1 if rng.random() < 0.7 else 2):
            changed = TRANSFORMS[name](query, rng)
            if changed and changed != query:
                query, applied = normalize_name(changed), [*applied, name]
        # An unchanged name, or one that lands exactly on another list entry, tests nothing.
        if not applied or query in listed_names or query in seen_queries:
            continue
        seen_queries.add(query)
        rows.append(
            {
                "id": f"pos-{len(rows):05d}",
                "kind": "positive",
                "query": query,
                "entity_id": entity_id,
                "source_name": source,
                "transforms": applied,
            }
        )

    def is_clean(query: str) -> bool:
        return (
            query not in listed_names
            and frozenset(query.split()) not in listed_token_sets
            and query not in seen_queries
        )

    count = 0
    while count < N_NEGATIVE_SYNTHETIC:
        tokens = [rng.choice(GIVEN_NAMES)]
        if rng.random() < 0.3:
            tokens.append(rng.choice(GIVEN_NAMES))
        query = " ".join([*tokens, rng.choice(SURNAMES)])
        if not is_clean(query):
            continue
        seen_queries.add(query)
        rows.append(
            {
                "id": f"neg-s-{count:05d}",
                "kind": "negative_synthetic",
                "query": query,
                "entity_id": None,
                "source_name": None,
                "transforms": [],
            }
        )
        count += 1

    count = 0
    while count < N_NEGATIVE_HARD:
        entity_id = rng.choice(entity_ids)
        tokens = rng.choice(names_by_entity[entity_id]).split()
        if len(tokens) < 2:
            continue
        if rng.random() < 0.5:
            query = " ".join([*tokens[:-1], rng.choice(SURNAMES)])  # listed given name(s)
            kept = "given"
        else:
            query = " ".join([rng.choice(GIVEN_NAMES), tokens[-1]])  # listed surname
            kept = "surname"
        if not is_clean(query):
            continue
        seen_queries.add(query)
        rows.append(
            {
                "id": f"neg-h-{count:05d}",
                "kind": "negative_hard",
                "query": query,
                "entity_id": None,
                "source_name": None,
                "transforms": [f"kept_listed_{kept}"],
            }
        )
        count += 1

    return rows


def build_dataset() -> str:
    """Writes the dataset and returns its SHA-256, which services/eval/screening.py pins."""
    return write_jsonl(DATASET_PATH, build_rows())
