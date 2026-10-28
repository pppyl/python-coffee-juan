import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import private_paths


class PrivatePathsTests(unittest.TestCase):
    def test_private_location_can_be_explicitly_set_outside_source(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"GOODGIFTS_PRIVATE_DIR": directory}):
            self.assertEqual(private_paths.private_dir(), Path(directory))

    def test_configuration_override_has_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "custom.json"
            with patch.dict(os.environ, {"STARBUCKS_CLIENT_CONFIG": str(config)}):
                self.assertEqual(private_paths.client_config_path(), config)

    def test_default_uses_private_location_when_legacy_file_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            private = Path(directory) / "private"
            with patch.object(private_paths, "ROOT", root), patch.dict(os.environ, {"GOODGIFTS_PRIVATE_DIR": str(private)}):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("STARBUCKS_CLIENT_CONFIG", None)
                    self.assertEqual(private_paths.client_config_path(), private / "protocol/client.private.json")


if __name__ == "__main__":
    unittest.main()
