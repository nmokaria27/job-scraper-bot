import unittest
from unittest.mock import patch, mock_open, MagicMock
import json
import sys

# Mock dependencies before importing main
sys.modules["httpx"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

import main

class PersistenceTests(unittest.TestCase):

    def test_load_queued_jobs_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            data = main.load_queued_jobs()
            self.assertEqual(data["channels"], {})
            self.assertIn("last_run", data)

    def test_load_queued_jobs_corrupt_json(self):
        with patch("builtins.open", mock_open(read_data="corrupt json")):
            with patch("main.json.load", side_effect=json.JSONDecodeError("Expecting value", "", 0)):
                # Should not raise exception, should return fresh data
                data = main.load_queued_jobs()
                self.assertEqual(data["channels"], {})
                self.assertIn("last_run", data)

    def test_load_queued_jobs_value_error(self):
        with patch("builtins.open", mock_open(read_data="{}")):
            with patch("main.json.load", side_effect=ValueError("Some value error")):
                data = main.load_queued_jobs()
                self.assertEqual(data["channels"], {})
                self.assertIn("last_run", data)

    def test_load_queued_jobs_valid_json(self):
        valid_data = {"channels": {"test-ch": []}}
        with patch("builtins.open", mock_open(read_data=json.dumps(valid_data))):
            data = main.load_queued_jobs()
            self.assertEqual(data["channels"], {"test-ch": []})
            self.assertIn("last_run", data)

    def test_load_seen_jobs_file_not_found(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            data = main.load_seen_jobs()
            self.assertEqual(data["jobs"], [])
            self.assertEqual(data["channels"], {})
            self.assertEqual(data["total_notified"], 0)

    def test_load_seen_jobs_corrupt_json(self):
        with patch("builtins.open", mock_open(read_data="corrupt")):
            with patch("main.json.load", side_effect=json.JSONDecodeError("Error", "", 0)):
                data = main.load_seen_jobs()
                self.assertEqual(data["jobs"], [])
                self.assertEqual(data["channels"], {})

    def test_load_seen_jobs_migration(self):
        legacy_data = {"job_ids": ["job1", "job2"]}
        with patch("builtins.open", mock_open(read_data=json.dumps(legacy_data))):
            data = main.load_seen_jobs()
            self.assertIn("jobs", data)
            self.assertEqual(len(data["jobs"]), 2)
            self.assertEqual(data["jobs"][0]["id"], "job1")
            self.assertIn("seen_at", data["jobs"][0])
            self.assertNotIn("job_ids", data)

if __name__ == "__main__":
    unittest.main()
