"""Draw simple synthetic test documents with INVENTED data.

These are plain drawings for OCR tests, not copies of official designs.
Each image is marked "SYNTHETIC TEST DOCUMENT".

Output (git-ignored):
    samples/synthetic/de_residence_permit_front.png
    samples/synthetic/de_residence_permit_back.png
    samples/synthetic/de_meldebescheinigung.pdf
    samples/synthetic/form_kyc_krishnan.pdf
    samples/synthetic/form_kyc_mustermann.pdf
    samples/synthetic/truth.json      <- the correct values for every document

Usage:
    uv run python scripts/make_synthetic.py
"""

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.checks.mrz import check_digit, parse_mrz  # noqa: E402
from app.checks.validate import check_iban, check_steuer_id, steuer_id_check_digit  # noqa: E402

OUT = ROOT / "samples" / "synthetic"

# Fonts: macOS paths first, then common Linux paths. Monospace for the MRZ.
FONT_CANDIDATES = {
    "sans": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "mono": ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES[kind]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


# ---------- invented data with valid check digits ----------

def make_iban(bank_code: str, account: str) -> str:
    """German IBAN: DE + 2 check digits + 8-digit bank code + 10-digit account (ISO 13616)."""
    bban = bank_code + account
    check = 98 - int(bban + "131400") % 97  # "DE" -> D=13, E=14, then "00"
    raw = f"DE{check:02d}{bban}"
    return " ".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def make_steuer_id(first_ten: str) -> str:
    return first_ten + str(steuer_id_check_digit(first_ten))


def td1_mrz(code: str, number: str, birth: str, sex: str, expiry: str, nationality: str,
            surname: str, given: str) -> str:
    """Build a valid 3 x 30 MRZ (ICAO 9303) with all check digits."""
    l1 = f"{code:<2}D<<{number}{check_digit(number)}".ljust(30, "<").replace(" ", "<")
    l2_body = f"{birth}{check_digit(birth)}{sex}{expiry}{check_digit(expiry)}{nationality:<3}".replace(" ", "<")
    l2_body = l2_body.ljust(29, "<")
    composite = l1[5:30] + l2_body[0:7] + l2_body[8:15] + l2_body[18:29]
    l2 = l2_body + str(check_digit(composite))
    l3 = f"{surname}<<{given.replace(' ', '<')}".ljust(30, "<")
    return "\n".join([l1, l2, l3])


ANANYA = {
    "surname": "KRISHNAN", "given_names": "ANANYA", "date_of_birth": "23.11.1992",
    "place_of_birth": "CHENNAI", "nationality": "IND", "gender": "F",
    "residence_permit_number": "Y8A2B3C4D", "expiry_date": "30.06.2028", "issue_date": "01.07.2024",
    "permit_type": "BLAUE KARTE EU", "issuing_authority": "STADT KÖLN",
    "address": "Lindenstraße 5, 50667 Köln",
    "iban": make_iban("37040044", "0532013099"), "steuer_id": make_steuer_id("4719283651"),
    "phone": "+49 221 0000000", "email": "ananya.krishnan@example.com", "employer": "Beispiel Software GmbH",
}

ERIKA = {  # matches the official ID card specimen (Erika Mustermann)
    "surname": "Mustermann", "given_names": "Erika", "birth_name": "Gabler", "date_of_birth": "12.08.1964",
    "address": "Heidestraße 17, 51147 Köln", "id_card_number": "T22000129",
    "iban": make_iban("10010010", "0123456789"), "steuer_id": make_steuer_id("8609574271"),
    "phone": "+49 221 1111111", "email": "erika.mustermann@example.com", "employer": "Musterfirma AG",
}


# ---------- drawing helpers ----------

def watermark(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    f = font("bold", max(18, img.width // 40))
    d.text((img.width - 20, img.height - 20), "SYNTHETIC TEST DOCUMENT", font=f, fill=(200, 60, 60), anchor="rd")


def card(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (1600, 1010), (232, 240, 236))  # ID-1 card ratio (85.6 x 54 mm)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1599, 110), fill=(30, 70, 110))
    d.text((40, 22), "BUNDESREPUBLIK DEUTSCHLAND", font=font("bold", 44), fill="white")
    d.text((40, 120), title, font=font("bold", 34), fill=(30, 70, 110))
    return img, d


def field(d: ImageDraw.ImageDraw, x: int, y: int, label: str, value: str, size: int = 40) -> None:
    d.text((x, y), label, font=font("sans", 22), fill=(80, 80, 80))
    d.text((x, y + 28), value, font=font("sans", size), fill="black")


# ---------- documents ----------

def residence_permit() -> dict:
    p = ANANYA
    front, d = card("AUFENTHALTSTITEL / RESIDENCE PERMIT")
    d.rectangle((40, 200, 440, 720), outline=(120, 120, 120), width=3)
    d.text((240, 460), "PHOTO", font=font("sans", 36), fill=(150, 150, 150), anchor="mm")
    d.text((1560, 30), p["residence_permit_number"], font=font("mono", 48), fill="white", anchor="ra")
    x = 500
    field(d, x, 200, "Name/Surname", p["surname"])
    field(d, x, 300, "Vornamen/Given names", p["given_names"])
    field(d, x, 400, "Geburtsdatum/Date of birth", p["date_of_birth"])
    field(d, 1060, 400, "Staatsangehörigkeit/Nationality", p["nationality"])
    field(d, x, 500, "Geschlecht/Sex", p["gender"])
    field(d, 1060, 500, "Gültig bis/Valid until", p["expiry_date"])
    field(d, x, 600, "Art des Titels/Type of permit", p["permit_type"])
    field(d, x, 700, "Anmerkungen/Remarks", "ERWERBSTÄTIGKEIT GESTATTET", 34)
    watermark(front)

    back, d = card("")
    field(d, 40, 140, "Anschrift/Address", p["address"])
    field(d, 40, 250, "Geburtsort/Place of birth", p["place_of_birth"])
    field(d, 40, 350, "Ausstellungsdatum/Date of issue", p["issue_date"])
    field(d, 800, 350, "Behörde/Authority", p["issuing_authority"])
    dd, mm, yy = p["date_of_birth"].split(".")
    ed, em, ey = p["expiry_date"].split(".")
    mrz = td1_mrz("AR", p["residence_permit_number"], yy[2:] + mm + dd, p["gender"], ey[2:] + em + ed,
                  p["nationality"], p["surname"], p["given_names"])
    for i, line in enumerate(mrz.splitlines()):
        d.text((60, 640 + i * 95), line, font=font("mono", 64), fill="black")
    watermark(back)

    front.save(OUT / "de_residence_permit_front.png")
    back.save(OUT / "de_residence_permit_back.png")
    assert parse_mrz(mrz).valid, "generated MRZ must pass its own check digits"
    return {"doc_type": "DE_RESIDENCE_PERMIT", "issuing_country": "DEU", "mrz": mrz,
            **{k: p[k] for k in ("surname", "given_names", "date_of_birth", "place_of_birth", "nationality",
                                 "gender", "residence_permit_number", "expiry_date", "issue_date", "permit_type",
                                 "issuing_authority", "address")}}


def a4_page(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (1240, 1754), "white")  # A4 at 150 dpi
    d = ImageDraw.Draw(img)
    d.text((80, 80), title, font=font("bold", 44), fill="black")
    d.line((80, 145, 1160, 145), fill="black", width=3)
    return img, d


def form_row(d: ImageDraw.ImageDraw, y: int, label: str, value: str) -> None:
    d.text((80, y), label, font=font("sans", 26), fill=(60, 60, 60))
    d.rectangle((460, y - 8, 1160, y + 40), outline=(120, 120, 120), width=2)
    d.text((475, y), value, font=font("sans", 28), fill="black")


def meldebescheinigung() -> dict:
    p = ANANYA
    img, d = a4_page("Meldebescheinigung")
    d.text((80, 170), "Einfache Meldebescheinigung nach § 18 Abs. 1 BMG", font=font("sans", 28), fill="black")
    d.text((80, 215), "Stadt Köln – Bürgeramt Innenstadt", font=font("sans", 28), fill="black")
    rows = [
        ("Familienname", p["surname"].title()),
        ("Vornamen", p["given_names"].title()),
        ("Geburtsdatum", p["date_of_birth"]),
        ("Geburtsort", p["place_of_birth"].title()),
        ("Staatsangehörigkeit", "indisch"),
        ("Anschrift", p["address"]),
        ("Einzugsdatum", "15.07.2024"),
        ("Wohnungsstatus", "Hauptwohnung"),
    ]
    for i, (label, value) in enumerate(rows):
        form_row(d, 320 + i * 80, label, value)
    d.text((80, 1050), "Köln, den 20.07.2024", font=font("sans", 28), fill="black")
    watermark(img)
    img.save(OUT / "de_meldebescheinigung.pdf", resolution=150)
    return {"doc_type": "DE_MELDEBESCHEINIGUNG", "issuing_country": "DEU",
            "surname": p["surname"].title(), "given_names": p["given_names"].title(),
            "date_of_birth": p["date_of_birth"], "place_of_birth": p["place_of_birth"].title(),
            "address": p["address"]}


def kyc_form(p: dict, filename: str, doc_label: str, doc_field: str) -> dict:
    img, d = a4_page("Kontoeröffnung – Angaben zur Person (KYC)")
    d.text((80, 170), "Beispielbank AG · Bitte in Druckbuchstaben ausfüllen", font=font("sans", 26), fill="black")
    name = f"{p['surname'].upper()}, {p['given_names'].title()}"
    rows = [
        ("Name, Vorname", name),
        ("Geburtsname", p.get("birth_name", "")),
        ("Geburtsdatum", p["date_of_birth"]),
        ("Anschrift", p["address"]),
        ("Telefon", p["phone"]),
        ("E-Mail", p["email"]),
        ("Steuer-ID", p["steuer_id"]),
        ("IBAN", p["iban"]),
        ("Arbeitgeber", p["employer"]),
        (doc_label, p[doc_field]),
    ]
    for i, (label, value) in enumerate(rows):
        form_row(d, 280 + i * 80, label, value)
    d.text((80, 1150), "Ort, Datum: Köln, 01.10.2024        Unterschrift: ______________________",
           font=font("sans", 26), fill="black")
    watermark(img)
    img.save(OUT / filename, resolution=150)
    truth = {"doc_type": "FORM_KYC", "issuing_country": "DEU", "name_as_printed": name,
             "surname": p["surname"], "given_names": p["given_names"], "date_of_birth": p["date_of_birth"],
             "address": p["address"], "phone": p["phone"], "email": p["email"], "steuer_id": p["steuer_id"],
             "iban": p["iban"], "employer": p["employer"], doc_field: p[doc_field]}
    if p.get("birth_name"):
        truth["birth_name"] = p["birth_name"]
    return truth


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for p in (ANANYA, ERIKA):
        assert check_iban(p["iban"]).ok and check_steuer_id(p["steuer_id"]).ok
    truth = {
        "de_residence_permit_front.png + de_residence_permit_back.png": residence_permit(),
        "de_meldebescheinigung.pdf": meldebescheinigung(),
        "form_kyc_krishnan.pdf": kyc_form(ANANYA, "form_kyc_krishnan.pdf", "Aufenthaltstitel-Nr.",
                                          "residence_permit_number"),
        "form_kyc_mustermann.pdf": kyc_form(ERIKA, "form_kyc_mustermann.pdf", "Personalausweis-Nr.",
                                            "id_card_number"),
    }
    (OUT / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:40} {f.stat().st_size // 1024:>5} KB")


if __name__ == "__main__":
    main()
