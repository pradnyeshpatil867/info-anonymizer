"""Web API tests (milestone 6).

FastAPI's TestClient sends HTTP requests to the app without starting a real
server. The vault, the people folder and the name generator are replaced by
temporary ones ("dependency overrides"), and the OCR engines and the LLM by
the fakes from test_pipeline.py.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.test_pipeline import _td3, fake_engines, id_card_state, png  # noqa: F401  (fake_engines is a fixture)


@pytest.fixture
def client(tmp_path, fake_engines):  # noqa: F811
    from app.identity.pseudonym import PseudonymGenerator
    from app.identity.vault import Vault
    from app.storage.people import PeopleStore

    vault = Vault(tmp_path / "vault.db", shared_between_threads=True)
    store = PeopleStore(tmp_path / "people")
    main.app.dependency_overrides[main.get_vault] = lambda: vault
    main.app.dependency_overrides[main.get_store] = lambda: store
    main.app.dependency_overrides[main.get_generator] = lambda: PseudonymGenerator(seed=7)
    main._analyses.clear()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
    vault.close()


def upload(client, n_files=2, scan=0):
    """scan: a different number = a new photo of the document (different file bytes)."""
    return client.post("/api/analyse", files=[("files", (f"side{i}.png", png(scan * 10 + i + 1), "image/png"))
                                              for i in range(n_files)])


def values_of(analysis: dict) -> dict:
    return {f["name"]: f["value"] for f in analysis["fields"]}


def test_analyse_returns_a_proposal(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    r = upload(client)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["doc_type"]["doc_type"] == "DE_ID_CARD" and a["pages"] == 2
    assert a["match"]["kind"] == "new"
    assert values_of(a)["surname"] == "MUSTERMANN"


def test_page_image(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    r = client.get(f"/api/analyses/{a['analysis_id']}/pages/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert client.get(f"/api/analyses/{a['analysis_id']}/pages/9").status_code == 404


def test_wrong_file_type_is_refused(client):
    r = client.post("/api/analyse", files=[("files", ("x.png", b"not really an image", "image/png"))])
    assert r.status_code == 415


def test_too_many_files_are_refused(client):
    assert upload(client, n_files=5).status_code == 400


def test_approve_then_same_person_is_found(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    r = client.post(f"/api/analyses/{a['analysis_id']}/approve", json={"values": values_of(a), "person_id": None})
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["created"] and saved["person_id"].startswith("P_")

    # The upload is forgotten after Approve.
    assert client.get(f"/api/analyses/{a['analysis_id']}/pages/1").status_code == 404

    # A new photo of the same document: now it is an existing person, shown by pseudonym only.
    a2 = upload(client, scan=1).json()
    assert a2["match"]["kind"] == "existing" and a2["match"]["person_id"] == saved["person_id"]
    cand = a2["match"]["candidates"][0]
    assert cand["pseudonym"] and "MUSTERMANN" not in cand["pseudonym"].upper()


def test_new_person_twice_is_refused(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    client.post(f"/api/analyses/{a['analysis_id']}/approve", json={"values": values_of(a), "person_id": None})
    a2 = upload(client, scan=1).json()
    r = client.post(f"/api/analyses/{a2['analysis_id']}/approve", json={"values": values_of(a2), "person_id": None})
    assert r.status_code == 409


def test_exact_copy_is_refused_before_the_analysis(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    client.post(f"/api/analyses/{a['analysis_id']}/approve", json={"values": values_of(a), "person_id": None})
    r = upload(client)
    assert r.status_code == 409 and "already saved" in r.json()["detail"]


def test_new_scan_with_nothing_new_is_refused_on_save(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    pid = client.post(f"/api/analyses/{a['analysis_id']}/approve",
                      json={"values": values_of(a), "person_id": None}).json()["person_id"]
    a2 = upload(client, scan=1).json()
    r = client.post(f"/api/analyses/{a2['analysis_id']}/approve", json={"values": values_of(a2), "person_id": pid})
    assert r.status_code == 409 and "Nothing new" in r.json()["detail"]
    assert len(client.get(f"/api/persons/{pid}").json()["data"]["documents"]) == 1


def test_approve_checks_the_values(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    url = f"/api/analyses/{a['analysis_id']}/approve"
    v = values_of(a)
    assert client.post(url, json={"values": {**v, "surname": ""}}).status_code == 422          # required
    assert client.post(url, json={"values": {**v, "issue_date": "01.11.10"}}).status_code == 422  # two-digit year
    assert client.post(url, json={"values": {**v, "hacker": "x"}}).status_code == 422          # unknown field
    assert client.post(url, json={"values": v, "person_id": "P_ZZZZZZ"}).status_code == 404     # unknown person


def test_search_all_four_ways(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    pid = client.post(f"/api/analyses/{a['analysis_id']}/approve",
                      json={"values": values_of(a), "person_id": None}).json()["person_id"]
    person = client.get(f"/api/persons/{pid}").json()

    by_name = client.post("/api/persons/search", json={"name": "Erika Mustermann", "date_of_birth": "12.08.1964"})
    by_pseudo = client.post("/api/persons/search", json={"pseudonym": person["pseudonym"]})
    by_id = client.post("/api/persons/search", json={"person_id": pid.lower()})
    by_number = client.post("/api/persons/search", json={"document_number": "t2200 0129"})
    for r in (by_name, by_pseudo, by_id, by_number):
        assert r.status_code == 200, r.text
        assert [p["person_id"] for p in r.json()] == [pid]

    # The search result holds no real values.
    text = by_name.text
    for real in ("Mustermann", "MUSTERMANN", "T22000129", "1964", "HEIDESTRASSE"):
        assert real not in text


def test_search_needs_exactly_one_way(client):
    assert client.post("/api/persons/search", json={}).status_code == 422
    assert client.post("/api/persons/search", json={"name": "Erika Mustermann"}).status_code == 422  # no birth date
    assert client.post("/api/persons/search",
                       json={"person_id": "P_X", "pseudonym": "John Miller"}).status_code == 422


def test_unknown_person(client):
    assert client.get("/api/persons/P_ZZZZZZ").status_code == 404
    assert client.get("/api/persons/..%2Fsecret").status_code == 404


# --- Frontend files (milestone 7) ---

def test_frontend_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "info-anonymizer" in page.text and "/static/app.js" in page.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_frontend_never_stores_personal_data_in_the_browser():
    """Privacy rule: no localStorage, sessionStorage, cookies or innerHTML in app.js."""
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "app" / "web" / "app.js").read_text(encoding="utf-8")
    code = "\n".join(line for line in js.splitlines() if not line.strip().startswith("//"))
    for forbidden in ("localStorage", "sessionStorage", "document.cookie", "innerHTML"):
        assert forbidden not in code, forbidden


def test_search_name_in_both_orders(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    pid = client.post(f"/api/analyses/{a['analysis_id']}/approve",
                      json={"values": values_of(a), "person_id": None}).json()["person_id"]
    for name in ("Erika Mustermann", "MUSTERMANN Erika", "Mustermann, Erika"):
        for dob in ("12.08.1964", "12 08 1964", "12/08/1964"):
            r = client.post("/api/persons/search", json={"name": name, "date_of_birth": dob})
            assert [p["person_id"] for p in r.json()] == [pid], (name, dob)


def test_anonymize_choice_via_api(client, fake_engines):  # noqa: F811
    id_card_state(fake_engines)
    a = upload(client).json()
    assert "address" in a["always_anonymized"] and "height" in a["optional_anonymized"]
    url = f"/api/analyses/{a['analysis_id']}/approve"
    v = values_of(a)
    # A group A field cannot be "chosen": it is always anonymized.
    assert client.post(url, json={"values": v, "anonymize": ["surname"]}).status_code == 422
    r = client.post(url, json={"values": {**v, "height": "160 cm", "issuing_authority": "STADT KÖLN"},
                               "anonymize": ["height"], "person_id": None})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/persons/{r.json()['person_id']}").json()["data"]["documents"][0]
    assert d["height"] != "160 cm" and d["height"].endswith(" cm")   # a fake height
    assert "height" in d["anonymized_fields"]
    assert d["issuing_authority"] == "STADT KÖLN"


def test_frontend_is_never_taken_from_an_old_cache(client):
    for path in ("/", "/static/app.js", "/static/style.css"):
        r = client.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path


def foreign_passport_state(state):
    state["paddle"] = ["REPUBLIC OF INDIA\nPASSPORT\n" + _td3("IND", "MUSTERMANN", "ERIKA", "640812")]
    state["tess"] = [""]
    state["llm"] = {"surname": "MUSTERMANN", "given_names": "ERIKA", "date_of_birth": "12.08.1964",
                    "passport_number": "Z1234567", "issue_date": "22.10.2020"}


def test_foreign_passport_only_for_an_existing_person(client, fake_engines):  # noqa: F811
    # 1. Nobody saved yet: the foreign passport is analysed, but cannot create a new person.
    foreign_passport_state(fake_engines)
    a = upload(client, n_files=1, scan=5).json()
    assert a["doc_type"]["doc_type"] == "FOREIGN_PASSPORT" and a["rejected"] is None
    r = client.post(f"/api/analyses/{a['analysis_id']}/approve", json={"values": values_of(a), "person_id": None})
    assert r.status_code == 422 and "already exists" in r.json()["detail"]

    # 2. Save the German document first (here the ID card specimen) ...
    id_card_state(fake_engines)
    a1 = upload(client).json()
    pid = client.post(f"/api/analyses/{a1['analysis_id']}/approve",
                      json={"values": values_of(a1), "person_id": None}).json()["person_id"]

    # 3. ... then the foreign passport is matched to that person and can be saved for them.
    foreign_passport_state(fake_engines)
    a2 = upload(client, n_files=1, scan=6).json()
    assert a2["match"]["kind"] == "existing" and a2["match"]["person_id"] == pid
    r = client.post(f"/api/analyses/{a2['analysis_id']}/approve", json={"values": values_of(a2), "person_id": pid})
    assert r.status_code == 200, r.text
    block = client.get(f"/api/persons/{pid}").json()["data"]["documents"][-1]
    assert block["doc_type"] == "FOREIGN_PASSPORT" and block["issuing_country"] == "IND"
    assert block["passport_number"] != "Z1234567" and "passport_number" in block["anonymized_fields"]
