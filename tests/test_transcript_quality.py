from __future__ import annotations

import unittest

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.transcript_quality import validate_transcript_coverage


class TranscriptQualityTest(unittest.TestCase):
    def test_platform_subtitle_needs_stricter_coverage_than_audio_transcript(self) -> None:
        segments = [
            {"start": "00:00", "end": "08:00", "text": "平台字幕正文" * 50},
            {"start": "08:00", "end": "17:43", "text": "平台字幕正文" * 50},
        ]

        subtitle_debug = validate_transcript_coverage(
            video_duration="50分58秒",
            segments=segments,
            transcript_source="subtitle_bcc",
        )
        audio_debug = validate_transcript_coverage(
            video_duration="50分58秒",
            segments=segments,
            transcript_source="asr_wav",
        )

        self.assertEqual(subtitle_debug["transcript_quality_reason"], "coverage_below_threshold")
        self.assertFalse(subtitle_debug["transcript_quality_passed"])
        self.assertEqual(subtitle_debug["coverage_threshold"], 0.60)
        self.assertTrue(audio_debug["transcript_quality_passed"])
        self.assertEqual(audio_debug["coverage_threshold"], 0.30)

    def test_bilibili_platform_subtitle_title_transcript_mismatch_fails_quality_gate(self) -> None:
        segments = [
            {
                "start": "00:00",
                "end": "08:00",
                "text": "蔡徐坤品牌代言粉丝消费力内娱偶像经济商业价值" * 30,
            },
            {
                "start": "08:00",
                "end": "16:30",
                "text": "明星塌房之后品牌合作和粉丝市场发生变化" * 30,
            },
        ]

        debug = validate_transcript_coverage(
            video_duration="20分00秒",
            segments=segments,
            transcript_source="subtitle_bcc",
            title="养成这5个习惯，你的成长速度会超越99%的人（顶尖的1%靠的不是自律）",
        )

        self.assertEqual(debug["transcript_quality_reason"], "title_transcript_mismatch")
        self.assertFalse(debug["transcript_quality_passed"])
        self.assertTrue(debug["title_transcript_consistency_checked"])
        self.assertEqual(debug["title_transcript_match_count"], 0)

    def test_verified_part_identity_does_not_require_abstract_title_words(self) -> None:
        identity={"page_index":4,"page_count":12,"expected_bvid":"BV1Me41137rw","expected_cid":"824336900","source_bvid":"BV1Me41137rw","source_cid":"824336900","provider":"bilibili_content_provider","part_title":"与设备合二为一"}
        segments=[{"start":"00:00","end":"04:00","text":"驾驶舱、赛车、引擎与方向盘的反馈，以及高速比赛时的感官体验。"*15},{"start":"04:00","end":"09:00","text":"车手如何感知抓地力、赛车状态和制动点。"*15}]
        result=validate_transcript_coverage(video_duration=545,segments=segments,transcript_source="subtitle_bcc",title="与设备合二为一",episode_identity=identity)
        self.assertTrue(result["transcript_quality_passed"])
        self.assertTrue(result["subtitle_episode_identity_verified"])
        self.assertEqual(result["title_transcript_match_count"],0)
        self.assertTrue(result["title_transcript_mismatch_warning"])
        short=validate_transcript_coverage(video_duration=545,segments=segments[:1],transcript_source="subtitle_bcc",title="与设备合二为一",episode_identity=identity)
        self.assertFalse(short["transcript_quality_passed"])
        wrong=dict(identity,source_cid="824337319")
        result=validate_transcript_coverage(video_duration=545,segments=segments,transcript_source="subtitle_bcc",title="与设备合二为一",episode_identity=wrong)
        self.assertFalse(result["transcript_quality_passed"])
        self.assertEqual(result["transcript_quality_reason"],"subtitle_episode_identity_mismatch")


if __name__ == "__main__":
    unittest.main()
