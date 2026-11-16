"""Tests for the LLM field extractor.

Most tests use a FAKE Ollama client: a small class that answers like Ollama,
but without network. So the tests are fast and do not need any model.
One slow test talks to the real local Ollama.
"""

import io
import json

import pytest
from PIL import Image

from app.identity.fields import KNOWN_FIELDS
from app.llm import extractor
from app.llm.extractor import ExtractionError, extract_fields, join_pages
from app.llm.schema import ExtractedFields, llm_json_schema

# Invented values only (no real person).
OCR_TEXT = """\
BUNDESREPUBLIK DEUTSCHLAND
PERSONALAUSWEIS
T22000129
Name/Surname
MUSTERMANN
GEB.GABLER
Vornamen/Given names
ERIKA
Geburtstag/Date of birth
12.08.1964
Geburtsort/Place of birth
BERLIN
Staatsangehörigkeit/Nationality
DEUTSCH
Gültig bis/Date of expiry
31.10.2020
"""


class FakeClient:
    """Pretends to be ollama.Client. Remembers the chat call so tests can look at it."""

    def __init__(self, answer: str, capabilities=("completion",)):
        self.answer = answer
        self.capabilities = list(capabilities)
        self.chat_kwargs = None
        self.show_calls = 0

    def show(self, model):
        self.show_calls += 1
        return {"capabilities": self.capabilities}

    def chat(self, **kwargs):
        self.chat_kwargs = kwargs
        return {"message": {"content": self.answer}}


@pytest.fixture(autouse=True)
def clear_vision_cache():
    """The vision answer is cached per model name. Clear it so tests do not affect each other."""
    extractor._vision_cache.clear()
    yield
    extractor._vision_cache.clear()


def answer(**fields) -> str:
    return json.dumps({"doc_type": "DE_ID_CARD", **fields})


# The app sets issuing_country itself (from the MRZ or the document type),
# so the LLM does not fill it (user decision, milestone 4).
NOT_FROM_LLM = {"issuing_country"}


def test_schema_contains_all_known_fields():
    props = ExtractedFields.model_json_schema()["properties"]
    for name in (KNOWN_FIELDS | {"surname", "given_names", "birth_name"}) - NOT_FROM_LLM:
        assert name in props, name


def test_schema_has_no_issuing_country():
    assert "issuing_country" not in ExtractedFields.model_json_schema()["properties"]


def test_doc_type_is_a_fixed_list():
    schema = ExtractedFields.model_json_schema()
    assert "DE_DRIVING_LICENCE" in json.dumps(schema["properties"]["doc_type"])
    with pytest.raises(Exception):
        ExtractedFields(doc_type="SOMETHING_ELSE")


def test_text_only_model_gets_no_images():
    client = FakeClient(answer(surname="MUSTERMANN"))
    result = extract_fields([Image.new("RGB", (100, 50))], OCR_TEXT, model="text-model", client=client)
    user = client.chat_kwargs["messages"][-1]
    assert "images" not in user
    assert "MUSTERMANN" in user["content"]
    assert result.used_images is False
    assert result.fields.surname == "MUSTERMANN"


def test_vision_model_gets_resized_images():
    client = FakeClient(answer(surname="MUSTERMANN"), capabilities=["completion", "vision"])
    big = Image.new("RGB", (4000, 2000), "white")
    small = Image.new("RGB", (300, 200), "white")
    result = extract_fields([big, small], OCR_TEXT, model="vision-model", client=client)
    images = client.chat_kwargs["messages"][-1]["images"]
    sizes = [Image.open(io.BytesIO(data)).size for data in images]
    assert sizes == [(1280, 640), (300, 200)]  # big one shrunk, small one unchanged
    assert result.used_images is True


def test_vision_check_is_cached():
    client = FakeClient(answer(), capabilities=["vision"])
    page = [Image.new("RGB", (10, 10))]
    extract_fields(page, OCR_TEXT, model="m", client=client)
    extract_fields(page, OCR_TEXT, model="m", client=client)
    assert client.show_calls == 1


def test_empty_strings_become_none_and_spaces_are_stripped():
    client = FakeClient(answer(surname="  MUSTERMANN ", given_names="", birth_name="   "))
    fields = extract_fields([], OCR_TEXT, model="m", client=client).fields
    assert fields.surname == "MUSTERMANN"
    assert fields.given_names is None
    assert fields.birth_name is None


def test_invalid_json_raises_extraction_error():
    client = FakeClient("this is not json")
    with pytest.raises(ExtractionError):
        extract_fields([], OCR_TEXT, model="m", client=client)


def test_temperature_and_format_are_passed():
    client = FakeClient(answer())
    result = extract_fields([], OCR_TEXT, model="my-model", client=client)
    kw = client.chat_kwargs
    assert kw["model"] == "my-model" == result.model
    assert kw["format"] == llm_json_schema()
    assert kw["format"]["required"] == list(ExtractedFields.model_fields)  # every key required
    assert kw["options"]["temperature"] == 0
    assert kw["messages"][0]["role"] == "system"


def test_join_pages():
    assert join_pages(["a", "b"]) == "--- page 1 ---\na\n--- page 2 ---\nb"


@pytest.mark.slow
def test_real_ollama_small_model():
    """Real call to the local Ollama. Skipped if Ollama or the model is missing."""
    ollama = pytest.importorskip("ollama")
    model = "qwen2.5:1.5b-instruct"
    client = ollama.Client(host="http://localhost:11434", timeout=120)
    try:
        installed = [m.model for m in client.list().models]
    except Exception:
        pytest.skip("Ollama is not reachable")
    if model not in installed:
        pytest.skip(f"{model} is not installed")
    result = extract_fields([], OCR_TEXT, model=model, client=client)
    assert result.fields.surname == "MUSTERMANN"
    assert result.fields.date_of_birth == "12.08.1964"


def test_thinking_is_switched_off_for_thinking_models():
    client = FakeClient(answer(surname="X"), capabilities=("completion", "vision", "thinking"))
    extract_fields([], "OCR", model="thinker", client=client)
    assert client.chat_kwargs["think"] is False


def test_no_think_setting_for_other_models():
    client = FakeClient(answer(surname="X"), capabilities=("completion",))
    extract_fields([], "OCR", model="plain", client=client)
    assert "think" not in client.chat_kwargs
