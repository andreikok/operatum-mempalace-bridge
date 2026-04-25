"""Shared pytest fixtures.

We boot a fresh palace + KG per test under a tempdir so test runs
never collide. ChromaDB needs to write a few hundred KB of segment
files, so we don't try to mock it — we let it do its thing in
isolation.
"""

import os
import sys
import tempfile
from pathlib import Path
import pytest

# Ensure the test process can import `src.*` without an editable
# install. The Dockerfile does `pip install .` which puts src on
# the path properly; tests run pre-install.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def palace_dir(tmp_path):
    """Per-test palace under a tempdir. Covers both the chroma palace
    and the KG sqlite file."""
    palace = tmp_path / "palace"
    palace.mkdir()
    kg = tmp_path / "kg.sqlite3"
    os.environ["MEMPALACE_PALACE_PATH"] = str(palace)
    os.environ["OPERATUM_BRIDGE_KG_PATH"] = str(kg)
    yield palace
    # Cleanup happens automatically via tmp_path. Drop the env so
    # other tests pick up their own.
    os.environ.pop("MEMPALACE_PALACE_PATH", None)
    os.environ.pop("OPERATUM_BRIDGE_KG_PATH", None)


@pytest.fixture
def client(palace_dir):
    """FastAPI test client with adapters booted via the lifespan."""
    from fastapi.testclient import TestClient
    # Importing main triggers FastAPI's app construction; the
    # adapters boot lazily via the lifespan when TestClient runs.
    from src.main import app as fastapi_app
    with TestClient(fastapi_app) as c:
        yield c
