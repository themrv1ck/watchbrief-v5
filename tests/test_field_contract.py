from __future__ import annotations

import json
import unittest

from helpers import ROOT
from scripts.validator import LEGACY_FIELDS, REQUIRED_REPORT_FIELDS, VALID_TAGS


class FieldContractTest(unittest.TestCase):
    def test_schema_required_fields_match_validator_contract(self) -> None:
        schema = json.loads((ROOT / "schemas" / "single_video_report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(schema["required"]), REQUIRED_REPORT_FIELDS)

    def test_schema_tag_enum_matches_validator_contract(self) -> None:
        schema = json.loads((ROOT / "schemas" / "single_video_report.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(tuple(schema["properties"]["tag"]["enum"]), VALID_TAGS)

    def test_single_video_schema_rejects_legacy_fields_by_shape(self) -> None:
        schema = json.loads((ROOT / "schemas" / "single_video_report.schema.json").read_text(encoding="utf-8"))
        self.assertIs(schema["additionalProperties"], False)
        for field in LEGACY_FIELDS:
            self.assertNotIn(field, schema["properties"])

    def test_phase2_contract_forbids_live_video_and_model_work(self) -> None:
        text = (ROOT / "CONTRACT_V5.md").read_text(encoding="utf-8")
        self.assertIn("real URL resolving", text)
        self.assertIn("subtitle fetching", text)
        self.assertIn("audio downloading", text)
        self.assertIn("transcription", text)
        self.assertIn("model calls", text)
        self.assertIn("pipeline edits", text)


if __name__ == "__main__":
    unittest.main()
