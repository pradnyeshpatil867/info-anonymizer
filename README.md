# info-anonymizer

Local web app that reads documents issued in Germany (ID cards, passports, residence permits, driving licences, address registrations, KYC and other forms) and stores the result as one JSON file per person in `people/`.

Everything runs on your own machine. Only local Ollama models are used.

## What the JSON files contain

- Personal data is replaced by **fake values of the same kind**: a fake name (pseudonym), a fake address, a fake birth date, fake document numbers in the original format.
- Name, birth date, document numbers, address, phone, email, IBAN and tax ID are always replaced. Other fields (for example height or issuing authority) stay real unless you tick "Anonymize" in the review screen.
- The list `anonymized_fields` in each document block says which values are fake. **All other values are real.** A fake value looks real, so keep this list with the data.
- The real values and the link "real value ↔ fake value" are stored only in the local vault `data/vault.db`.

> Phase 1: the vault (`data/vault.db`) is **not encrypted**. It contains real names, birth dates and document numbers. Use test documents only.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Tesseract 5 with the `deu` and `eng` language files
- Ollama

## Install

```bash
uv sync
```

## Start the app

Ollama must be running, with the model from `config.yaml` (`ollama pull qwen3-vl:4b-instruct`).

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000 (API description: http://127.0.0.1:8000/docs).
`--host 127.0.0.1` means only this computer can reach the app.

## Run the tests

```bash
uv run pytest -m "not slow"   # fast tests, no models needed (about 2 seconds)
uv run pytest -m slow         # tests with the real OCR engines and LLM (about 5 minutes)
```
