from __future__ import annotations

import unittest
from types import SimpleNamespace

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.providers.youtube_connect_provider import (
    extract_youtube_video_id,
    fetch_youtube_connect_transcript,
    material_from_youtube_connect,
)


class FakeApi:
    def fetch(self, video_id, languages):
        self.video_id = video_id
        self.languages = languages
        return SimpleNamespace(
            language_code="en",
            is_generated=False,
            snippets=[
                SimpleNamespace(text=" first line ", start=0.1, duration=1.4),
                SimpleNamespace(text="second line", start=1.5, duration=2.2),
            ],
        )


class YouTubeConnectProviderTest(unittest.TestCase):
    def test_extract_youtube_video_id(self) -> None:
        self.assertEqual(extract_youtube_video_id("https://www.youtube.com/watch?v=Er2s-CFoZSo"), "Er2s-CFoZSo")
        self.assertEqual(extract_youtube_video_id("https://youtu.be/Er2s-CFoZSo?si=abc"), "Er2s-CFoZSo")

    def test_fetch_transcript_returns_watchbrief_material_without_audio_boundary(self) -> None:
        api = FakeApi()

        transcript = fetch_youtube_connect_transcript(
            "https://www.youtube.com/watch?v=Er2s-CFoZSo",
            api_factory=lambda: api,
        )
        material = material_from_youtube_connect(transcript)

        self.assertEqual(api.video_id, "Er2s-CFoZSo")
        self.assertEqual(api.languages[0], "en")
        self.assertEqual(transcript.provider, "youtube-connect")
        self.assertEqual(transcript.subtitle_lang, "en")
        self.assertEqual(transcript.subtitle_format, "transcript")
        self.assertEqual(len(transcript.segments), 2)
        self.assertEqual(material["material_version"], "watchbrief_v5.transcript_material.v1")
        self.assertEqual(material["source"]["provider"], "youtube-connect")
        self.assertEqual(material["source"]["source_platform"], "youtube")
        self.assertTrue(material["adapter_boundary"]["no_subtitle_download"])
        self.assertTrue(material["adapter_boundary"]["no_audio_download"])
        self.assertTrue(material["adapter_boundary"]["no_transcription"])
        self.assertTrue(material["adapter_boundary"]["no_model_call"])


if __name__ == "__main__":
    unittest.main()
