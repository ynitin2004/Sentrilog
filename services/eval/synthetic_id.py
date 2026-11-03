"""Synthetic passport data page with known ground truth: an identity, its ICAO 9303 TD3
machine-readable zone, and a rendered image of it (D28). Every identity is invented; nothing
here resembles a real person's document.

The MRZ builder uses the production checksum function, so a generated MRZ is valid by the same
definition parse_td3() checks. That is deliberate rather than circular: checksum correctness is
proven separately, by hand, in tests/pipeline/extraction/test_mrz.py.
"""

import datetime as dt
import io
import random
from dataclasses import dataclass
from pathlib import Path

from services.pipeline.extraction.mrz import _check_digit

GIVEN = [
    "JOHN", "MARY", "ROBERT", "PRIYA", "RAHUL", "WEI", "MEI", "CARLOS", "LUCIA", "OLGA",
    "DMITRI", "FATIMA", "OMAR", "KWAME", "NGOZI", "LARS", "INGRID", "PIERRE", "GIULIA", "HANS",
    "PIOTR", "ELIF", "YUKI", "MINJUN", "SOFIA", "ARJUN", "AMARA", "SVEN", "LAYLA", "DIEGO",
]  # fmt: skip
SURNAMES = [
    "SMITH", "GARCIA", "SHARMA", "PATEL", "WANG", "CHEN", "TANAKA", "KIM", "RODRIGUEZ",
    "IVANOVA", "OKAFOR", "MENSAH", "HADDAD", "RAHMAN", "LARSEN", "DUBOIS", "ROSSI", "MULLER",
    "KOWALSKI", "YILMAZ", "NGUYEN", "OCONNOR", "SANTOS", "NAKAMURA", "PETROV", "ABUBAKAR",
]  # fmt: skip
COUNTRIES = ["USA", "GBR", "IND", "DEU", "FRA", "JPN", "KOR", "BRA", "NGA", "CAN", "ESP", "ITA"]

# Fonts tried in order. OCR-B is the MRZ standard but not freely installable everywhere; a
# monospace face is the closest common stand-in. The one used is recorded in every report,
# because OCR accuracy depends on it.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/consola.ttf",
    "/System/Library/Fonts/Menlo.ttc",
]


@dataclass(frozen=True)
class SyntheticIdentity:
    given_names: str
    surname: str
    document_number: str
    nationality: str
    date_of_birth: dt.date
    expiry_date: dt.date
    sex: str

    @property
    def full_name(self) -> str:
        """As parse_td3() reports it: given names, then surname."""
        return f"{self.given_names} {self.surname}"


def random_identity(rng: random.Random) -> SyntheticIdentity:
    given = rng.choice(GIVEN) if rng.random() < 0.7 else f"{rng.choice(GIVEN)} {rng.choice(GIVEN)}"
    dob = dt.date(1950, 1, 1) + dt.timedelta(days=rng.randrange(0, 365 * 55))
    expiry = dt.date(2027, 1, 1) + dt.timedelta(days=rng.randrange(0, 365 * 10))
    number = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(9))
    return SyntheticIdentity(
        given_names=given,
        surname=rng.choice(SURNAMES),
        document_number=number,
        nationality=rng.choice(COUNTRIES),
        date_of_birth=dob,
        expiry_date=expiry,
        sex=rng.choice("MF"),
    )


def build_td3(identity: SyntheticIdentity) -> tuple[str, str]:
    name_field = f"{identity.surname}<<{identity.given_names.replace(' ', '<')}"
    line1 = f"P<{identity.nationality}{name_field}".ljust(44, "<")[:44]

    doc_num = identity.document_number.ljust(9, "<")[:9]
    dob = identity.date_of_birth.strftime("%y%m%d")
    expiry = identity.expiry_date.strftime("%y%m%d")
    personal = "<" * 14
    line2_no_composite = (
        doc_num
        + str(_check_digit(doc_num))
        + identity.nationality
        + dob
        + str(_check_digit(dob))
        + identity.sex
        + expiry
        + str(_check_digit(expiry))
        + personal
        + "<"
    )
    composite = line2_no_composite[0:10] + line2_no_composite[13:20] + line2_no_composite[21:43]
    line2 = line2_no_composite + str(_check_digit(composite))
    return line1, line2


def find_font() -> str | None:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def render_data_page(identity: SyntheticIdentity, *, font_path: str | None) -> bytes:
    """A plain data page: printed fields above, the two MRZ lines at the bottom, JPEG-encoded
    the way an upload would be."""
    from PIL import Image, ImageDraw, ImageFont

    line1, line2 = build_td3(identity)
    width, height = 1250, 880
    image = Image.new("RGB", (width, height), (246, 244, 236))
    draw = ImageDraw.Draw(image)
    label: ImageFont.FreeTypeFont | ImageFont.ImageFont
    mrz: ImageFont.FreeTypeFont | ImageFont.ImageFont
    if font_path:
        label = ImageFont.truetype(font_path, 28)
        mrz = ImageFont.truetype(font_path, 34)
    else:
        label = ImageFont.load_default(size=28)
        mrz = ImageFont.load_default(size=34)
    draw.text(
        (40, 30), "PASSPORT  SPECIMEN - SYNTHETIC TEST DOCUMENT", fill=(30, 30, 90), font=label
    )
    fields = [
        ("Surname", identity.surname),
        ("Given names", identity.given_names),
        ("Nationality", identity.nationality),
        ("Date of birth", identity.date_of_birth.strftime("%d %b %Y").upper()),
        ("Date of expiry", identity.expiry_date.strftime("%d %b %Y").upper()),
        ("Passport No.", identity.document_number),
    ]
    for i, (k, v) in enumerate(fields):
        draw.text((360, 110 + i * 70), f"{k}: {v}", fill=(20, 20, 20), font=label)
    draw.rectangle((40, 110, 320, 470), outline=(120, 120, 120), width=3)
    draw.text((900 - 300, 720), line1, fill=(0, 0, 0), font=mrz, anchor="mm")
    draw.text((900 - 300, 790), line2, fill=(0, 0, 0), font=mrz, anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()
