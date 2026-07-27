"""Point the store at a throwaway SQLite file and set a fixed API key, before any app module
is imported (both app.store and app.web read these at import time)."""

import os
import pathlib
import tempfile

_tmp_dir = tempfile.mkdtemp(prefix="intake-tests-")
os.environ["INTAKE_DB_URL"] = f"sqlite:///{pathlib.Path(_tmp_dir) / 'test.db'}"
os.environ.setdefault("API_KEY", "test-only-api-key")
