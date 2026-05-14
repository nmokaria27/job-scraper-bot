import json
import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock
import sys

# Mock dependencies
sys.modules["httpx"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

import discover_companies
from companies import COMPANIES, get_companies


class DiscoveryParsingTests(unittest.TestCase):
    def test_extract_candidates_from_text(self) -> None:
        blob = """
        https://boards.greenhouse.io/stripe/jobs/123
        https://jobs.lever.co/anyscale/abc
        https://jobs.ashbyhq.com/Perplexity/role
        https://boards-api.greenhouse.io/v1/boards/figma/jobs
        """
        candidates = discover_companies._extract_candidates_from_text(blob)
        self.assertIn("stripe", candidates["greenhouse"])
        self.assertIn("figma", candidates["greenhouse"])
        self.assertIn("anyscale", candidates["lever"])
        self.assertIn("Perplexity", candidates["ashby"])

    def test_merge_existing_dedupes(self) -> None:
        existing = {"greenhouse": ["stripe"], "lever": [], "ashby": ["Ashby"]}
        found = {"greenhouse": ["stripe", "figma"], "lever": ["anyscale"], "ashby": ["Ashby"]}
        merged = discover_companies._merge_existing(existing, found)
        self.assertEqual(merged["greenhouse"], ["figma", "stripe"])
        self.assertEqual(merged["lever"], ["anyscale"])
        self.assertEqual(merged["ashby"], ["Ashby"])


class CompanyMergeTests(unittest.TestCase):
    def test_get_companies_merges_discovered_file(self) -> None:
        payload = {
            "greenhouse": ["newco-gh"],
            "lever": ["newco-lever"],
            "ashby": ["newco-ashby"],
        }
        with tempfile.NamedTemporaryFile("w", delete=False) as f:
            json.dump(payload, f)
            path = f.name

        try:
            with patch.dict(
                os.environ,
                {
                    "INCLUDE_DISCOVERED_COMPANIES": "true",
                    "DISCOVERED_COMPANIES_PATH": path,
                },
                clear=False,
            ):
                merged = get_companies()
        finally:
            os.remove(path)

        self.assertIn("newco-gh", merged["greenhouse"])
        self.assertIn("newco-lever", merged["lever"])
        self.assertIn("newco-ashby", merged["ashby"])

    def test_get_companies_can_skip_discovered_merge(self) -> None:
        with patch.dict(
            os.environ,
            {"INCLUDE_DISCOVERED_COMPANIES": "false"},
            clear=False,
        ):
            merged = get_companies()
        self.assertEqual(merged["greenhouse"], COMPANIES["greenhouse"])


if __name__ == "__main__":
    unittest.main()
