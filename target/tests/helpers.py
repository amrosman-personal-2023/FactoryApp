"""Shared test harness for the migrated app.

Same isolation pattern as tests/test_characterization.py: every test gets a
throwaway database seeded with the exact legacy seed data, so both systems
always answer the same questions about the same inputs.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_DIR = os.path.dirname(TESTS_DIR)          # target/
sys.path.insert(0, TARGET_DIR)

from app import db as appdb   # noqa: E402  (target/app/db.py)
from fastapi.testclient import TestClient   # noqa: E402

from app.main import app      # noqa: E402


class WebTestCase(unittest.TestCase):
    """Boots the FastAPI app against a fresh temp copy of the seed database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(
            appdb, "DB_PATH", os.path.join(self.tmp.name, "t.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        conn = appdb.init(reset=True)
        conn.close()
        self.client = TestClient(app)
