from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_golden(name: str) -> dict:
    with (ROOT / "golden" / name).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def assert_invalid(testcase: unittest.TestCase, payload: dict, code: str) -> None:
    from scripts.validator import WatchBriefValidationError, validate_normalized_report_payload

    with testcase.assertRaises(WatchBriefValidationError) as context:
        validate_normalized_report_payload(payload)
    testcase.assertTrue(any(issue.code == code for issue in context.exception.issues))
