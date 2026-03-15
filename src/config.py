from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv


load_dotenv()

_PROJECT_ROOT = Path(__file__).parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "default.yaml"


def load_config(config_path: Path = _CONFIG_PATH) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_alpaca_keys() -> tuple[str, str]:
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    if not api_key or not secret_key:
        raise EnvironmentError(
            "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env"
        )
    return api_key, secret_key


def get_fmp_key() -> str:
    key = os.environ.get("FMP_API_KEY", "")
    if not key:
        raise EnvironmentError("FMP_API_KEY must be set in .env")
    return key


CONFIG = load_config()
