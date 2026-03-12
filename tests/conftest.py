import os
import tempfile
from pathlib import Path

import pytest

from src.storage.database import Database


@pytest.fixture
def tmp_db():
    """Provides a temporary database that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = Database(db_path)
        db.connect()
        yield db
        db.close()


@pytest.fixture(autouse=True)
def mock_env_keys(monkeypatch):
    """Set dummy Alpaca keys so config module doesn't fail on import."""
    monkeypatch.setenv("ALPACA_API_KEY", "test_key_123")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret_456")
