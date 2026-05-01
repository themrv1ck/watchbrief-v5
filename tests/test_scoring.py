from __future__ import annotations

import copy
import unittest

from helpers import ROOT, assert_invalid, load_golden
from scripts.analyzer.codex_review import parse_review_response
from scripts.scoring import SCORING_FORMULA, SCORING_FORMULA_VERSION, apply_deterministic_scoring, compute_replacement_score
from scripts.validator import validate_normalized_report_payload
from scripts.watch_order import render_watch_order_html


class ScoringStabilityTest(unittest.TestCase):
    def raw_model_payload(self, replacement_score: float) -> dict:
        payload = load_golden("sample_payload_heartflow.json")
        payload.pop("score_trace", None)
        payload["replacement_score"] = replacement_score
        return payload

    def test_same_structured_assessment_scores_identically_across_runs(self) -> None:
        first = parse_review_response(self.raw_model_payload(7.9))
        second = parse_review_response(self.raw_model_payload(8.4))

        self.assertEqual(first["replacement_score"], second["replacement_score"])
        self.assertEqual(first["score_trace"]["computed_replacement_score"], second["score_trace"]["computed_replacement_score"])

    def test_model_score_changes_do_not_change_final_score_when_dimensions_same(self) -> None:
        low_model = parse_review_response(self.raw_model_payload(1.2))
        high_model = parse_review_response(self.raw_model_payload(9.8))

        self.assertEqual(low_model["replacement_score"], high_model["replacement_score"])
        self.assertEqual(low_model["tag"], high_model["tag"])
        self.assertNotEqual(low_model["score_trace"]["model_suggested_score"], high_model["score_trace"]["model_suggested_score"])

    def test_score_trace_exists_and_uses_fixed_formula(self) -> None:
        payload = parse_review_response(self.raw_model_payload(9.8))

        self.assertIn("score_trace", payload)
        self.assertEqual(payload["score_trace"]["formula"], SCORING_FORMULA)
        self.assertEqual(payload["score_trace"]["final_score_source"], "deterministic_formula")
        self.assertEqual(payload["scoring_formula_version"], SCORING_FORMULA_VERSION)
        self.assertEqual(payload["watchbrief_version"], "watchbrief_v5")

    def test_formula_weights_are_locked(self) -> None:
        score = compute_replacement_score({
            "information_density": 10.0,
            "evidence_quality": 0.0,
            "originality": 5.0,
            "watch_value": 8.0,
        })

        self.assertEqual(score, 5.4)

    def test_large_model_score_difference_records_warning(self) -> None:
        payload = parse_review_response(self.raw_model_payload(9.8))

        self.assertTrue(payload["score_trace"]["warnings"])
        self.assertEqual(payload["score_trace"]["model_suggested_score"], 9.8)
        self.assertEqual(payload["score_trace"]["computed_replacement_score"], 4.7)

    def test_stability_metadata_exists(self) -> None:
        payload = parse_review_response(self.raw_model_payload(4.7))

        for key in ("transcript_hash", "qwen_prompt_fingerprint", "codex_prompt_fingerprint"):
            self.assertRegex(payload[key], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["qwen_model_id"], "mock-qwen")
        self.assertEqual(payload["codex_model"], "gpt-5.4")

    def test_high_score_cannot_keep_report_enough_tag(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 9.0, "论据质量": 9.0, "独创性": 9.0, "观看性价比": 9.0}
        payload["replacement_score"] = 9.0
        payload["tag"] = "报告足够替代"
        payload["score_trace"] = {
            "information_density": 9.0,
            "evidence_quality": 9.0,
            "originality": 9.0,
            "watch_value": 9.0,
            "formula": SCORING_FORMULA,
            "computed_replacement_score": 9.0,
            "model_suggested_score": 9.0,
            "final_score_source": "deterministic_formula",
            "warnings": [],
        }

        assert_invalid(self, payload, "tag_score_conflict")

    def test_low_score_cannot_keep_complete_watch_tag(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 2.0, "论据质量": 2.0, "独创性": 2.0, "观看性价比": 2.0}
        payload["replacement_score"] = 2.0
        payload["tag"] = "建议完整看"
        payload["score_trace"] = {
            "information_density": 2.0,
            "evidence_quality": 2.0,
            "originality": 2.0,
            "watch_value": 2.0,
            "formula": SCORING_FORMULA,
            "computed_replacement_score": 2.0,
            "model_suggested_score": 2.0,
            "final_score_source": "deterministic_formula",
            "warnings": [],
        }

        assert_invalid(self, payload, "tag_score_conflict")

    def test_apply_deterministic_scoring_fixes_conflicting_tag(self) -> None:
        payload = load_golden("sample_payload_heartflow.json")
        payload["structured_assessment"] = {"信息密度": 9.0, "论据质量": 9.0, "独创性": 9.0, "观看性价比": 9.0}
        payload["replacement_score"] = 1.0
        payload["tag"] = "报告足够替代"
        payload["watch_verdict"] = "报告不能完全替代，原视频建议完整看；如果时间有限，先看 10:51 | 15:08。"

        adapted = apply_deterministic_scoring(payload)

        self.assertEqual(adapted["replacement_score"], 9.0)
        self.assertEqual(adapted["tag"], "建议完整看")
        validate_normalized_report_payload(adapted)

    def test_watch_order_keeps_playlist_order_after_deterministic_scoring(self) -> None:
        low = parse_review_response(self.raw_model_payload(9.8))
        low["page_file"] = "low.html"
        high = copy.deepcopy(low)
        high["title"] = "High deterministic score"
        high["page_file"] = "high.html"
        high["structured_assessment"] = {"信息密度": 9.0, "论据质量": 9.0, "独创性": 9.0, "观看性价比": 9.0}
        high = apply_deterministic_scoring(high)

        html = render_watch_order_html({
            "job_name": "scoring",
            "generated_at": "2026-04-27 10:00",
            "requested_count": 2,
            "completed_count": 2,
            "failed_count": 0,
            "failures": [],
            "videos": [low, high],
        })

        self.assertLess(html.index("low.html"), html.index("high.html"))
        self.assertIn('"score": 9.0', html)

    def test_love_boredom_pain_preference_sample_exists(self) -> None:
        sample = load_golden("love_boredom_pain_preference.json")

        self.assertIn("preferred_highest_compression", sample)
        self.assertIn("preferred_path_table", sample)
        self.assertIn("preferred_final_conclusion", sample)
        self.assertIn("不完美", sample["preferred_highest_compression"])


if __name__ == "__main__":
    unittest.main()
