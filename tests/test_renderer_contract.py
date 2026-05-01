from __future__ import annotations

import unittest

from helpers import load_golden
from scripts.validator import WatchBriefValidationError, validate_renderer_input


class RendererContractTest(unittest.TestCase):
    def test_renderer_input_missing_required_field_fails(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["final_conclusion"]

        with self.assertRaises(WatchBriefValidationError) as context:
            validate_renderer_input(payload)
        self.assertTrue(
            any(issue.code == "required" and issue.path == "final_conclusion" for issue in context.exception.issues)
        )

    def test_renderer_input_is_not_repaired_from_legacy_fields(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        del payload["final_conclusion"]
        payload["core_thesis"] = "旧字段不能被 renderer 用来补 final_conclusion。"

        with self.assertRaises(WatchBriefValidationError) as context:
            validate_renderer_input(payload)
        codes = {issue.code for issue in context.exception.issues}
        self.assertIn("required", codes)
        self.assertIn("legacy_field", codes)


if __name__ == "__main__":
    unittest.main()
