"""Tests for private per-process OpenPets diagnostic logs."""

import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpets_linux.logging_setup import configure_logging, log_process_memory


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_openpets_file_handler", False):
                root.removeHandler(handler)
                handler.close()

    def test_info_warning_error_and_process_context_are_written_privately(self):
        with tempfile.TemporaryDirectory(prefix="openpets-log-test-") as directory:
            path = configure_logging("gui/test", directory)
            logger = logging.getLogger("openpets.test")
            logger.info("startup marker")
            logger.warning("warning marker")
            logger.error("error marker")
            for handler in logging.getLogger().handlers:
                handler.flush()

            contents = path.read_text(encoding="utf-8")
            self.assertIn("INFO", contents)
            self.assertIn("WARNING", contents)
            self.assertIn("ERROR", contents)
            self.assertIn("pid=", contents)
            self.assertIn("startup marker", contents)
            self.assertIn("warning marker", contents)
            self.assertIn("error marker", contents)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(Path(directory).stat().st_mode & 0o777, 0o700)

    def test_memory_snapshot_is_logged(self):
        with tempfile.TemporaryDirectory(prefix="openpets-log-memory-") as directory:
            path = configure_logging("app", directory)
            log_process_memory(logging.getLogger("openpets.test"), role="test")
            for handler in logging.getLogger().handlers:
                handler.flush()
            contents = path.read_text(encoding="utf-8")
            self.assertIn("process memory role=test", contents)
            self.assertIn("VmRSS=", contents)


if __name__ == "__main__":
    unittest.main()
