from __future__ import annotations

import copy
import threading
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts import visual_analyzer
from scripts.mimo_vl_worker import ensure_no_think, parse_model_output


def frame_manifest(count: int) -> dict:
    rows = [
        {
            "index": index,
            "timestamp_seconds": float(index * 10),
            "endpoint": "start" if index == 0 else "end" if index == count - 1 else "",
            "sha256": f"{index:064x}",
        }
        for index in range(count)
    ]
    return {
        "video_sha256": "a" * 64,
        "duration_seconds": float((count - 1) * 10),
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_seconds": 10.0,
            "observed_max_gap_seconds": 10.0,
            "includes_start": True,
            "includes_end": True,
            "timestamps_seconds": [row["timestamp_seconds"] for row in rows],
        },
        "frames": rows,
    }


def visual_result(rows: list[dict], batch_number: int = 1) -> dict:
    return {
        "batch_summary": f"第 {batch_number} 批画面摘要",
        "items": [
            {
                "slot": slot,
                "observation": f"第 {row['index']} 帧可见内容",
                "visible_text": [],
                "actions": [],
                "uncertainty": "",
            }
            for slot, row in enumerate(rows)
        ],
        "caveats": [f"第 {batch_number} 批只能确认抽帧时刻"],
    }


class VisualAnalyzerTest(unittest.TestCase):
    def test_deterministic_timestamps_cover_both_endpoints_and_max_gap(self) -> None:
        timestamps = visual_analyzer.deterministic_sample_timestamps(47.0, 10.0)

        self.assertEqual(timestamps[0], 0.0)
        self.assertEqual(timestamps[-1], 47.0)
        self.assertLessEqual(visual_analyzer.observed_max_gap(timestamps), 10.0)
        self.assertEqual(timestamps, visual_analyzer.deterministic_sample_timestamps(47.0, 10.0))

    def test_validate_visual_result_requires_exact_frame_coverage(self) -> None:
        manifest = frame_manifest(5)
        valid = visual_result(manifest["frames"])

        normalized = visual_analyzer.validate_visual_result(valid, manifest)
        self.assertEqual([row["index"] for row in normalized["frames"]], list(range(5)))

        missing = copy.deepcopy(valid)
        missing["items"].pop()
        with self.assertRaisesRegex(
            visual_analyzer.VisualAnalysisError,
            "mimo_visual_frame_coverage_mismatch",
        ):
            visual_analyzer.validate_visual_result(missing, manifest)

    def test_worker_parser_accepts_only_json_and_exact_mimo_envelopes(self) -> None:
        self.assertEqual(parse_model_output('{"ok":true}'), {"ok": True})
        self.assertEqual(
            parse_model_output('<think>internal</think>\n{"ok":true}'),
            {"ok": True},
        )
        with self.assertRaisesRegex(ValueError, "not strict JSON"):
            parse_model_output('说明文字\n{"ok":true}')
        prompt = ensure_no_think("输出 JSON")
        self.assertTrue(prompt.endswith("/no_think"))
        self.assertEqual(prompt.count("/no_think"), 1)

    def test_analyzer_batches_long_timeline_without_losing_frames(self) -> None:
        manifest = frame_manifest(25)
        paths = [Path(f"/tmp/frame-{index}.jpg") for index in range(25)]
        analyzer = object.__new__(visual_analyzer.MimoVLVisualAnalyzer)
        analyzer._closed = False
        analyzer.max_frames_per_request = 6
        analyzer.timeout = 10
        analyzer._lock = threading.Lock()
        analyzer.model = Path("/mock/model")
        analyzer.process = None
        requests: list[dict] = []

        analyzer._start = lambda: None
        analyzer._write = lambda payload: requests.append(payload)

        def read_response(_timeout: int) -> dict:
            request = requests[-1]
            batch_rows = manifest["frames"][
                sum(len(item["image_paths"]) for item in requests[:-1]):
                sum(len(item["image_paths"]) for item in requests)
            ]
            return {
                "ok": True,
                "result": visual_result(batch_rows, len(requests)),
            }

        analyzer._read = read_response
        with mock.patch.object(
            visual_analyzer,
            "build_frame_manifest",
            return_value=(manifest, paths),
        ):
            evidence = analyzer.analyze_video(Path("/tmp/mock.mp4"), Path("/tmp/work"))

        self.assertEqual([len(request["image_paths"]) for request in requests], [6, 6, 6, 6, 1])
        self.assertEqual(evidence["inference"]["batch_count"], 5)
        self.assertEqual(evidence["inference"]["max_frames_per_request"], 6)
        self.assertEqual(
            [row["index"] for row in evidence["analysis"]["frames"]],
            list(range(25)),
        )
        self.assertEqual(len(evidence["analysis"]["batch_summaries"]), 5)
        self.assertRegex(evidence["visual_evidence_hash"], r"^[0-9a-f]{64}$")

    def test_invalid_json_batch_is_split_without_losing_strict_frame_coverage(self) -> None:
        manifest = frame_manifest(4)
        paths = [Path(f"/tmp/frame-{index}.jpg") for index in range(4)]
        analyzer = object.__new__(visual_analyzer.MimoVLVisualAnalyzer)
        analyzer._closed = False
        analyzer.max_frames_per_request = 4
        analyzer.timeout = 10
        analyzer._lock = threading.Lock()
        analyzer.model = Path("/mock/model")
        analyzer.process = None
        requests: list[dict] = []
        analyzer._start = lambda: None
        analyzer._write = lambda payload: requests.append(payload)

        def read_response(_timeout: int) -> dict:
            indices = [int(Path(path).stem.split("-")[-1]) for path in requests[-1]["image_paths"]]
            if len(indices) == 4:
                return {"ok": False, "reason_code": "mimo_vl_invalid_json", "message": "invalid JSON"}
            return {"ok": True, "result": visual_result([manifest["frames"][index] for index in indices])}

        analyzer._read = read_response
        with mock.patch.object(visual_analyzer, "build_frame_manifest", return_value=(manifest, paths)):
            evidence = analyzer.analyze_video(Path("/tmp/mock.mp4"), Path("/tmp/work"))

        self.assertEqual([len(request["image_paths"]) for request in requests], [4, 2, 2])
        self.assertEqual([row["index"] for row in evidence["analysis"]["frames"]], list(range(4)))
        self.assertEqual(evidence["inference"]["adaptive_split_count"], 1)
        self.assertEqual(evidence["inference"]["request_count"], 3)

    def test_invalid_json_single_frame_still_fails(self) -> None:
        manifest = frame_manifest(1)
        analyzer = object.__new__(visual_analyzer.MimoVLVisualAnalyzer)
        analyzer._closed = False
        analyzer.max_frames_per_request = 1
        analyzer.timeout = 10
        analyzer._lock = threading.Lock()
        analyzer.model = Path("/mock/model")
        analyzer.process = None
        analyzer._start = lambda: None
        requests = []
        analyzer._write = lambda payload: requests.append(payload)
        analyzer._read = lambda _timeout: {"ok": False, "reason_code": "mimo_vl_invalid_json", "message": "invalid JSON"}

        with mock.patch.object(
            visual_analyzer,
            "build_frame_manifest",
            return_value=(manifest, [Path("/tmp/frame-0.jpg")]),
        ), self.assertRaisesRegex(visual_analyzer.VisualAnalysisError, "mimo_vl_invalid_json"):
            analyzer.analyze_video(Path("/tmp/mock.mp4"), Path("/tmp/work"))
        self.assertEqual(len(requests), 2)

    def test_invalid_json_single_frame_recovers_on_bounded_retry(self) -> None:
        manifest = frame_manifest(1)
        analyzer = object.__new__(visual_analyzer.MimoVLVisualAnalyzer)
        analyzer._closed = False
        analyzer.max_frames_per_request = 1
        analyzer.timeout = 10
        analyzer._lock = threading.Lock()
        analyzer.model = Path("/mock/model")
        analyzer.process = None
        requests = []
        analyzer._start = lambda: None
        analyzer._write = lambda payload: requests.append(payload)
        analyzer._read = lambda _timeout: (
            {"ok": False, "reason_code": "mimo_vl_invalid_json", "message": "invalid JSON"}
            if len(requests) == 1
            else {"ok": True, "result": visual_result(manifest["frames"])}
        )

        with mock.patch.object(
            visual_analyzer,
            "build_frame_manifest",
            return_value=(manifest, [Path("/tmp/frame-0.jpg")]),
        ):
            evidence = analyzer.analyze_video(Path("/tmp/mock.mp4"), Path("/tmp/work"))

        self.assertEqual(len(requests), 2)
        self.assertEqual(evidence["inference"]["single_frame_retry_count"], 1)
        self.assertEqual(evidence["inference"]["request_count"], 2)


if __name__ == "__main__":
    unittest.main()
