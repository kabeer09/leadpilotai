"""Shared fixtures: isolated test DB + rate-limit reset.

Tests NEVER touch the dev database: DATABASE_URL is pointed at a scratch
SQLite file before the app is imported, and tables are created fresh.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///./test_trial.db"

import pytest  # noqa: E402
from app.database import Base, engine  # noqa: E402
import app.models  # noqa: F401,E402
import app.deps as deps  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_db():
    if os.path.exists("test_trial.db"):
        os.remove("test_trial.db")
    Base.metadata.create_all(bind=engine)
    yield
    try:
        os.remove("test_trial.db")
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    deps._hits.clear()
    yield
    deps._hits.clear()
