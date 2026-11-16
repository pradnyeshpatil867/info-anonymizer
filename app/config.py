"""Read settings from config.yaml (spec section K)."""

import os
from functools import lru_cache
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "tokenize_low_risk_fields": False,
    "pseudonym_locale": "en_US",
    "pseudonym_include_middle_name": True,
    "pseudonym_match_gender": True,
    "llm_model": "qwen3-vl:4b-instruct",
    "ollama_host": "http://localhost:11434",
    "llm_temperature": 0,
    "llm_max_image_side": 1280,
    "tesseract_languages": "deu+eng",
    "paddle_lang": "de",
    "ocr_min_width": 1600,
    "models_dir": "models",
    "vault_path": "data/vault.db",
    "people_dir": "people",
}


@lru_cache
def settings() -> dict:
    """All settings: defaults, overwritten by config.yaml. Read once, then cached."""
    path = PROJECT_ROOT / "config.yaml"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    result = {**DEFAULTS, **(loaded or {})}
    # Environment variables win. Used by tests to point the app at a temporary folder,
    # so a test never touches the real vault. Example: IA_VAULT_PATH=/tmp/x/vault.db
    for key in ("vault_path", "people_dir"):
        env = os.environ.get("IA_" + key.upper())
        if env:
            result[key] = env
    return result


def project_path(relative: str) -> Path:
    """Turn a path from config.yaml (relative to the project) into a full path."""
    p = Path(relative)
    return p if p.is_absolute() else PROJECT_ROOT / p
