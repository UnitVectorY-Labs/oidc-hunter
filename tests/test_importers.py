from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from oidc_hunter.db import database
from oidc_hunter.importers import import_catalog_yaml, load_candidates


class ImporterTests(unittest.TestCase):
    def test_catalog_import_tolerates_field_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            with database(Path(tmp) / "test.db") as conn:
                count = import_catalog_yaml(
                    conn,
                    "run-1",
                    """
services:
  example:
    name: Example
    openid-configuration: https://example.com/.well-known/openid-configuration
    jwks_uri: https://example.com/jwks.json
    aliases:
      - login.example.com
""",
                )
                self.assertEqual(count, 1)
                row = conn.execute("SELECT * FROM catalog_entries").fetchone()
                self.assertEqual(row["service_id"], "example")

    def test_missing_candidates_file_is_initialized(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.yaml"
            with database(Path(tmp) / "test.db") as conn:
                count = load_candidates(conn, path)
                self.assertEqual(count, 0)
                self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
