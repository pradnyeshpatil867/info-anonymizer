"""The five scenarios from the learning page (docs/learning/database-tables.html),
plus the given-names rule (b) and other matching cases. All data is invented.
"""

import pytest

from app.identity.matcher import Identity, find_person
from app.identity.records import (ApprovedDocument, DuplicateDocumentError, exact_copy_message, file_hashes,
                                  merged_document, save_document)


def id_card(given="Jonas", surname="Müller", dob="12.04.1990", number="L01X00T47", **fields):
    return ApprovedDocument(
        doc_type="DE_ID_CARD", issuing_country="DEU",
        identity=Identity(given, surname, dob, gender="M", document_numbers={"id_card_number": number}),
        fields={"nationality": "DEU", **fields})


def process(vault, store, gen, doc):
    """What the app does: match, then (as if the user approved) save."""
    m = find_person(vault, doc.identity)
    pid = m.person_id if m.kind in ("existing", "confirm") else None
    return m, save_document(vault, store, gen, doc, pid)


def test_five_scenarios(vault, store, gen):
    # 1. ID card, Jonas Müller -> new person
    m1, r1 = process(vault, store, gen, id_card(address="Hauptstr. 1, 10115 Berlin"))
    assert m1.kind == "new" and r1.created
    jonas = r1.person_id

    # 2. KYC form "MUELLER, Jonas" -> same person by match_key
    kyc = ApprovedDocument("FORM_KYC", "DEU", Identity("Jonas", "MUELLER", "12.04.1990"),
                           fields={"iban": "DE89 3704 0044 0532 0130 00", "phone": "+49 30 000000"})
    m2, r2 = process(vault, store, gen, kyc)
    assert m2.kind == "existing" and m2.person_id == jonas and not r2.created

    # 3. Passport, Anna Schmidt, same address -> new person, address allowed
    anna = ApprovedDocument("DE_PASSPORT", "DEU",
                            Identity("Anna", "Schmidt", "03.09.1985", gender="F",
                                     document_numbers={"passport_number": "C4F8K2L19"}),
                            fields={"address": "Hauptstr. 1, 10115 Berlin"})
    m3, r3 = process(vault, store, gen, anna)
    assert m3.kind == "new" and r3.person_id != jonas

    # 4. Rental form "Jonas Muller" (dots missing), shows the ID card number -> user decides
    rental = ApprovedDocument("FORM_RENTAL", "DEU",
                              Identity("Jonas", "Muller", "12.04.1990",
                                       document_numbers={"id_card_number": "L01X00T47"}),
                              fields={"employer": "Example GmbH"})
    m4 = find_person(vault, rental.identity)
    assert m4.kind == "confirm" and m4.person_id == jonas
    r4 = save_document(vault, store, gen, rental, m4.person_id)  # user said "same person"
    assert any("name" in w for w in r4.warnings)

    # 5. Jonas Müller born 1975 -> new person (same name, other birth date)
    m5, r5 = process(vault, store, gen, id_card(dob="01.01.1975", number="T22Y99Q05"))
    assert m5.kind == "new" and r5.person_id not in (jonas, r3.person_id)

    # The JSON for Jonas: 3 documents, no group A values (personal data) in the file.
    data = store.load(jonas)
    assert len(data["documents"]) == 3
    text = store.path(jonas).read_text(encoding="utf-8")
    for real in ("Jonas", "Müller", "L01X00T47", "Hauptstr", "DE89", "1990-04-12"):
        assert real not in text, real
    # Layout B: no person block; each block holds only what was new at that time.
    assert set(data) == {"person_id", "pseudonym", "documents"}
    card, kyc_block, rental_block = data["documents"]
    assert card["nationality"] == "DEU"                     # group B: clear text
    fake_address = card["address"]                          # group A: a fake address
    assert vault.real_from_fake(jonas, "address", fake_address) == "Hauptstr. 1, 10115 Berlin"
    assert "date_of_birth" in card and "date_of_birth" not in kyc_block   # known from the ID card
    assert set(kyc_block["anonymized_fields"]) == {"iban", "phone"}
    assert "id_card_number" not in rental_block             # already saved with the ID card
    assert rental_block["employer"] == "Example GmbH"       # group B, not ticked: clear text
    assert rental_block["anonymized_fields"] == []

    # Anna's same address got her own, different fake address.
    assert store.load(r3.person_id)["documents"][0]["address"] != fake_address


def test_rule_b_missing_middle_name_asks_user(vault, store, gen):
    _, r = process(vault, store, gen, id_card(given="Jonas Peter"))
    form = ApprovedDocument("FORM_KYC", "DEU", Identity("Jonas", "Müller", "12.04.1990"))
    m = find_person(vault, form.identity)
    assert m.kind == "confirm" and m.person_id == r.person_id


def test_name_change_found_by_birth_name(vault, store, gen):
    _, r = process(vault, store, gen, ApprovedDocument(
        "DE_ID_CARD", "DEU", Identity("Anna", "Schmidt", "03.09.1985", gender="F")))
    married = Identity("Anna", "Weber", "03.09.1985", birth_name="Schmidt")
    m = find_person(vault, married)
    assert m.kind == "confirm" and m.person_id == r.person_id


def test_conflict_when_number_belongs_to_someone_else(vault, store, gen):
    process(vault, store, gen, id_card())                                           # Jonas owns L01X00T47
    _, anna = process(vault, store, gen, ApprovedDocument(
        "DE_PASSPORT", "DEU", Identity("Anna", "Schmidt", "03.09.1985")))
    weird = Identity("Anna", "Schmidt", "03.09.1985", document_numbers={"id_card_number": "L01X00T47"})
    m = find_person(vault, weird)
    assert m.kind == "conflict" and anna.person_id in m.candidates


def test_different_value_is_kept_with_warning(vault, store, gen):
    _, r = process(vault, store, gen, id_card())
    doc = id_card(nationality="AUT")
    res = save_document(vault, store, gen, doc, r.person_id)
    docs = store.load(r.person_id)["documents"]
    assert docs[0]["nationality"] == "DEU" and docs[1]["nationality"] == "AUT"
    assert any("nationality" in w for w in res.warnings)


def test_same_file_twice_is_refused(vault, store, gen):
    """Rule 4: the exact same file again -> refused, nothing changes."""
    doc = id_card()
    doc.file_hashes = file_hashes([b"fake image bytes"])
    _, r = process(vault, store, gen, doc)
    before = store.load(r.person_id)
    with pytest.raises(DuplicateDocumentError, match="already saved"):
        save_document(vault, store, gen, doc, r.person_id)
    assert store.load(r.person_id) == before
    assert exact_copy_message(vault, doc.file_hashes) is not None


def test_one_known_file_and_one_new_file_is_not_an_exact_copy(vault, store, gen):
    doc = id_card()
    doc.file_hashes = file_hashes([b"front"])
    process(vault, store, gen, doc)
    assert exact_copy_message(vault, file_hashes([b"front", b"back"])) is None


def test_same_document_new_scan_nothing_new_is_refused(vault, store, gen):
    """Rule 5: same type + same number, all values the same -> refused."""
    first = id_card(address="Hauptstr. 1, 10115 Berlin")
    first.file_hashes = file_hashes([b"scan 1"])
    _, r = process(vault, store, gen, first)
    again = id_card(address="Hauptstr. 1, 10115 Berlin")
    again.file_hashes = file_hashes([b"scan 2"])
    with pytest.raises(DuplicateDocumentError, match="Nothing new.*DOC_0001"):
        save_document(vault, store, gen, again, r.person_id)
    assert len(store.load(r.person_id)["documents"]) == 1
    # The refused scan was not remembered either (the transaction rolled back).
    assert exact_copy_message(vault, again.file_hashes) is None


def test_same_document_with_new_values_adds_update_entry(vault, store, gen):
    """Rule 5, option (b): only the new or changed values, linked with "updates"."""
    first = id_card(address="Hauptstr. 1, 10115 Berlin", expiry_date="01.01.2030")
    first.file_hashes = file_hashes([b"scan 1"])
    _, r = process(vault, store, gen, first)
    moved = id_card(address="Ringstr. 5, 80331 München", expiry_date="01.01.2030", issuing_authority="Stadt München")
    moved.file_hashes = file_hashes([b"scan 2"])
    res = save_document(vault, store, gen, moved, r.person_id)
    data = store.load(r.person_id)
    assert [d["doc_id"] for d in data["documents"]] == ["DOC_0001", "DOC_0002"]
    upd = data["documents"][1]
    assert upd["updates"] == "DOC_0001"
    assert set(upd) == {"doc_id", "doc_type", "issuing_country", "updates", "file_sha256", "anonymized_fields",
                        "address", "issuing_authority"}  # number and expiry date are unchanged
    assert upd["anonymized_fields"] == ["address"]
    assert any("only the new or changed values" in w.lower() for w in res.warnings)
    # The merged view of DOC_0001 now has the new address.
    assert merged_document(data["documents"], "DOC_0001")["address"] == upd["address"]
    # The same update again brings nothing new.
    moved.file_hashes = file_hashes([b"scan 3"])
    with pytest.raises(DuplicateDocumentError):
        save_document(vault, store, gen, moved, r.person_id)


def test_new_person_level_value_counts_as_new(vault, store, gen):
    _, r = process(vault, store, gen, id_card())
    res = save_document(vault, store, gen, id_card(place_of_birth="Köln"), r.person_id)
    upd = store.load(r.person_id)["documents"][1]
    assert upd["updates"] == "DOC_0001" and "place_of_birth" in upd
    assert res.person_id == r.person_id


def test_form_without_number_same_values_is_refused(vault, store, gen):
    """Rule 6: a form with no document number, same type and same values -> refused."""
    def kyc():
        return ApprovedDocument("FORM_KYC", "DEU", Identity("Jonas", "Müller", "12.04.1990"),
                                fields={"phone": "+49 30 000000"})
    _, r = process(vault, store, gen, kyc())
    with pytest.raises(DuplicateDocumentError):
        save_document(vault, store, gen, kyc(), r.person_id)
    other = kyc()
    other.fields["email"] = "j@example.org"
    save_document(vault, store, gen, other, r.person_id)
    docs = store.load(r.person_id)["documents"]
    assert len(docs) == 2 and "updates" not in docs[1]  # a different form: its own entry


def test_file_name_is_never_stored(vault, store, gen):
    doc = id_card()
    doc.file_hashes = file_hashes([b"abc"])
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    assert set(d) == {"doc_id", "doc_type", "issuing_country", "date_of_birth", "gender", "nationality",
                      "id_card_number", "anonymized_fields", "file_sha256"}
    # Values first (fixed order), then anonymized_fields, then file_sha256.
    assert list(d)[-2:] == ["anonymized_fields", "file_sha256"]
    assert list(d)[3:7] == ["date_of_birth", "gender", "nationality", "id_card_number"]


def test_same_date_in_two_spellings_gets_one_token(vault, store, gen):
    """Real bug: "25.02.2020" and "25 02 2020" got two tokens, so a new scan looked "changed"."""
    first = id_card(issue_date="25.02.2020", expiry_date="24.02.2030")
    first.file_hashes = file_hashes([b"scan 1"])
    _, r = process(vault, store, gen, first)
    again = id_card(issue_date="25 02 2020", expiry_date="24 02 2030")
    again.file_hashes = file_hashes([b"scan 2"])
    with pytest.raises(DuplicateDocumentError, match="Nothing new"):
        save_document(vault, store, gen, again, r.person_id)
    # Group B, not ticked: stored in clear text, in ISO form.
    assert store.load(r.person_id)["documents"][0]["issue_date"] == "2020-02-25"


def test_same_ticked_date_in_two_spellings_gets_one_fake(vault, store, gen):
    first = id_card(issue_date="25.02.2020")
    first.anonymize = {"issue_date"}
    _, r = process(vault, store, gen, first)
    fake = store.load(r.person_id)["documents"][0]["issue_date"]
    assert fake != "2020-02-25" and vault.real_from_fake(r.person_id, "issue_date", fake) == "2020-02-25"
    again = id_card(issue_date="25 02 2020")
    again.anonymize = {"issue_date"}
    with pytest.raises(DuplicateDocumentError, match="Nothing new"):
        save_document(vault, store, gen, again, r.person_id)


def test_group_a_always_fake_group_b_clear_unless_ticked(vault, store, gen):
    doc = id_card(address="Hauptstr. 1, 10115 Berlin", height="180 cm", eye_colour="BRAUN",
                  issuing_authority="Stadt Köln", place_of_birth="Köln")
    doc.anonymize = {"height", "place_of_birth"}           # the user ticked two group B boxes
    _, r = process(vault, store, gen, doc)
    data = store.load(r.person_id)
    d = data["documents"][0]
    pid = r.person_id
    real = lambda f, v: vault.real_from_fake(pid, f, v)  # noqa: E731
    # Group A: fake values of the same kind.
    assert d["id_card_number"] != "L01X00T47" and real("id_card_number", d["id_card_number"]) == "L01X00T47"
    assert len(d["id_card_number"]) == 9
    assert real("address", d["address"]) == "Hauptstr. 1, 10115 Berlin"
    assert real("date_of_birth", d["date_of_birth"]) == "1990-04-12"
    # Group B, not ticked: clear text.
    assert d["issuing_authority"] == "Stadt Köln" and d["eye_colour"] == "BRAUN"
    # Group B, ticked: fake values.
    assert d["height"].endswith(" cm") and real("height", d["height"]) == "180 cm"
    assert real("place_of_birth", d["place_of_birth"]) == "Köln"
    # The marker says which values are fake.
    assert d["anonymized_fields"] == ["date_of_birth", "place_of_birth", "height", "address", "id_card_number"]


def test_group_a_field_cannot_be_left_in_clear_text(vault, store, gen):
    doc = id_card(address="Hauptstr. 1")
    doc.anonymize = set()                                   # nothing ticked
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    assert d["address"] != "Hauptstr. 1" and "address" in d["anonymized_fields"]


def test_known_value_is_not_repeated_even_when_ticked(vault, store, gen):
    """Layout B: "180 cm" is already saved (clear) with the ID card. The passport shows the same
    height, ticked: it is NOT repeated and NOT a conflict. Known limit: the ID card block keeps
    its clear value (older blocks are not rewritten)."""
    _, r = process(vault, store, gen, id_card(height="180 cm"))
    assert store.load(r.person_id)["documents"][0]["height"] == "180 cm"
    passport = ApprovedDocument("DE_PASSPORT", "DEU",
                                Identity("Jonas", "Müller", "12.04.1990", gender="M",
                                         document_numbers={"passport_number": "C4F8K2L19"}),
                                fields={"height": "180 cm"}, anonymize={"height"})
    res = save_document(vault, store, gen, passport, r.person_id)
    block = store.load(r.person_id)["documents"][1]
    assert "height" not in block and set(block) >= {"passport_number"}
    assert not any("height" in w for w in res.warnings)


def test_config_switch_anonymizes_all_group_b(vault, store, gen):
    m = find_person(vault, id_card().identity)
    r = save_document(vault, store, gen, id_card(height="180 cm"), None, tokenize_low_risk=True)
    d = store.load(r.person_id)["documents"][0]
    assert d["height"] != "180 cm" and d["nationality"] != "DEU"
    assert {"height", "nationality", "issuing_country"} <= set(d["anonymized_fields"])
    assert d["doc_type"] == "DE_ID_CARD"           # the document type always stays readable
    assert m.kind == "new"


def residence_permit(remarks, **fields):
    return ApprovedDocument(
        "DE_RESIDENCE_PERMIT", "DEU",
        Identity("Jonas", "Müller", "12.04.1990", gender="M",
                 document_numbers={"residence_permit_number": "Y00X9999T", "passport_number": "X1234567"}),
        fields={"remarks": remarks, **fields})


def test_passport_number_inside_remarks_is_hidden(vault, store, gen):
    """Decision 2026-11-16, option (b): the real passport number stayed readable in the remarks."""
    doc = residence_permit("20 ABS. 1 NR. 1 AUFENTHG PASS(ERSATZ) X1234567 BIS 01 01 2031")
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    text = store.path(r.person_id).read_text(encoding="utf-8")
    assert "X1234567" not in text
    # The remarks stay readable; the number is replaced by the SAME fake as the passport_number field.
    assert d["remarks"] == f"20 ABS. 1 NR. 1 AUFENTHG PASS(ERSATZ) {d['passport_number']} BIS 01 01 2031"
    assert d["partly_anonymized_fields"] == ["remarks"] and "remarks" not in d["anonymized_fields"]
    assert list(d)[-3:] == ["anonymized_fields", "partly_anonymized_fields", "file_sha256"]
    assert any("inside remarks" in w for w in r.warnings)


def test_hidden_date_and_spaced_number_inside_text(vault, store, gen):
    doc = residence_permit("GEB. 12 04 1990, PASS X 123 4567")
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    assert "1990" not in d["remarks"] and "123" not in d["remarks"]
    dob_fake = d["date_of_birth"]                       # ISO, for example "1971-03-05"
    y, m, dd = dob_fake.split("-")
    assert d["remarks"] == f"GEB. {dd} {m} {y}, PASS {d['passport_number']}"


def test_ticked_remarks_is_fully_fake_and_not_partly(vault, store, gen):
    doc = residence_permit("PASS X1234567")
    doc.anonymize = {"remarks"}
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    assert "remarks" in d["anonymized_fields"] and "partly_anonymized_fields" not in d


def test_same_permit_again_with_hidden_remarks_is_nothing_new(vault, store, gen):
    first = residence_permit("PASS(ERSATZ) X1234567")
    first.file_hashes = file_hashes([b"scan 1"])
    _, r = process(vault, store, gen, first)
    again = residence_permit("PASS(ERSATZ) X1234567")
    again.file_hashes = file_hashes([b"scan 2"])
    with pytest.raises(DuplicateDocumentError, match="Nothing new"):
        save_document(vault, store, gen, again, r.person_id)


def test_ticked_group_b_value_is_not_searched_inside_texts(vault, store, gen):
    """Only group A values are hidden inside texts. Place of birth "Köln" (ticked) must not change
    the issuing authority "Stadt Köln" (a different fact that only looks the same)."""
    doc = id_card(place_of_birth="Köln", issuing_authority="Stadt Köln")
    doc.anonymize = {"place_of_birth"}
    _, r = process(vault, store, gen, doc)
    d = store.load(r.person_id)["documents"][0]
    assert d["issuing_authority"] == "Stadt Köln" and d["place_of_birth"] != "Köln"


def test_same_value_in_other_spelling_is_not_new(vault, store, gen):
    """Real bug (milestone 8): "CHENNAI" on the residence permit and "Chennai" on the
    Meldebescheinigung counted as two places of birth."""
    _, r = process(vault, store, gen, residence_permit("X", place_of_birth="CHENNAI", height="175cm",
                                                       issuing_authority="STADT KÖLN"))
    melde = ApprovedDocument("DE_MELDEBESCHEINIGUNG", "DEU", Identity("Jonas", "Müller", "12.04.1990"),
                             fields={"place_of_birth": "Chennai", "height": "175 cm",
                                     "issuing_authority": "Stadt Koeln", "phone": "+49 30 1"})
    res = save_document(vault, store, gen, melde, r.person_id)
    block = store.load(r.person_id)["documents"][1]
    assert set(block) & {"place_of_birth", "height", "issuing_authority"} == set()   # nothing repeated
    assert not any("different value" in w for w in res.warnings)
    assert "phone" in block                                                           # really new


def test_same_value_in_other_spelling_gets_the_same_fake(vault, store, gen):
    first = id_card(place_of_birth="CHENNAI")
    first.anonymize = {"place_of_birth"}
    _, r = process(vault, store, gen, first)
    fake = store.load(r.person_id)["documents"][0]["place_of_birth"]
    from app.identity.fakes import fake_for
    with vault.transaction():
        assert fake_for(vault, gen.fake, r.person_id, "place_of_birth", "Chennai") == fake


def test_compare_key_rules():
    from app.identity.fields import compare_key, same_value
    assert same_value("KÖLN", "Koeln") and same_value("Straße", "STRASSE") and same_value("175cm", "175 cm")
    assert same_value("Land  Berlin", "land berlin")
    assert not same_value("Hauptstr.", "Hauptstraße") and not same_value("DEUTSCH", "DEU")
    assert compare_key("Chennai") == "chennai"
