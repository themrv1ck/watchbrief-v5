from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.acquisition_errors import (
    BilibiliSubtitleError,
    LOGIN_REQUIRED_FOR_SUBTITLE,
    NO_SUBTITLE_AVAILABLE,
    SubtitleUnavailableError,
)
from scripts.bilibili_content_provider import (
    extract_aid,
    extract_bvid,
    fetch_bilibili_subtitle,
    parse_bcc_body,
    parse_metadata_payload,
)
from helpers import ROOT


def metadata_payload(*, bvid: str = "BV15qQwB4EZ9", aid: int = 114, cid: int = 514, pages: list[dict[str, Any]] | None = None) -> dict:
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "bvid": bvid,
            "aid": aid,
            "cid": cid,
            "title": "B 站字幕测试",
            "duration": 120,
            "owner": {"name": "测试 UP"},
            "pages": pages or [{"page": 1, "cid": cid, "part": "正片", "duration": 120}],
        },
    }


def player_payload(*, subtitles: list[dict[str, Any]] | None = None, need_login: bool = False) -> dict:
    return {
        "code": 0,
        "message": "OK",
        "data": {
            "need_login_subtitle": need_login,
            "subtitle": {
                "subtitles": subtitles or [],
            },
        },
    }


def track(language: str = "zh-Hans", url: str = "//i0.hdslb.com/bfs/subtitle/test.json", *, ai_status: int = 0) -> dict:
    return {
        "id": 1,
        "id_str": f"sub-{language}",
        "lan": language,
        "lan_doc": language,
        "subtitle_url": url,
        "ai_type": 0,
        "ai_status": ai_status,
    }


BCC_PAYLOAD = {
    "body": [
        {"from": 0.366, "to": 0.766, "location": 0, "content": "第一句"},
        {"from": 1.2, "to": 3.0, "location": 0, "content": "second line"},
    ],
}


class FakeResponse:
    def __init__(self, payload: dict | bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def fake_urlopen(routes: dict[str, dict], seen: list[dict[str, Any]] | None = None):
    def open_request(request, timeout):
        url = request.full_url
        headers = dict(request.header_items())
        if seen is not None:
            seen.append({"url": url, "headers": headers})
        for marker, payload in routes.items():
            if marker in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)
        raise AssertionError(f"unexpected url: {url}")

    return open_request


@dataclass
class Cookie:
    name: str
    value: str


class BilibiliContentProviderTest(unittest.TestCase):
    def test_extract_bvid_from_bilibili_url_and_raw_id(self) -> None:
        self.assertEqual(extract_bvid("https://www.bilibili.com/video/BV15qQwB4EZ9/?p=2"), "BV15qQwB4EZ9")
        self.assertEqual(extract_bvid("BV1xx411c7mD"), "BV1xx411c7mD")

    def test_extract_aid_from_bilibili_url_and_raw_id(self) -> None:
        self.assertEqual(extract_aid("https://www.bilibili.com/video/av116379755745719/"), "116379755745719")
        self.assertEqual(extract_aid("av116379755745719"), "116379755745719")
        self.assertEqual(extract_aid("116379755745719"), "116379755745719")

    def test_bvid_to_aid_cid_pages_metadata(self) -> None:
        payload = metadata_payload(
            pages=[
                {"page": 1, "cid": 111, "part": "P1", "duration": 10},
                {"page": 2, "cid": 222, "part": "P2 标题", "duration": 20},
            ]
        )

        metadata = parse_metadata_payload("https://www.bilibili.com/video/BV15qQwB4EZ9/?p=2", payload)

        self.assertEqual(metadata.bvid, "BV15qQwB4EZ9")
        self.assertEqual(metadata.aid, "114")
        self.assertEqual(metadata.cid, "222")
        self.assertEqual(metadata.page_index, 2)
        self.assertEqual(metadata.part_title, "P2 标题")
        self.assertEqual(len(metadata.pages), 2)

    def test_fetches_wbi_subtitle_list_and_bcc_segments(self) -> None:
        seen: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = fetch_bilibili_subtitle(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                Path(temp_dir),
                urlopen_func=fake_urlopen(
                    {
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(subtitles=[track("zh-Hans")]),
                        "i0.hdslb.com/bfs/subtitle": BCC_PAYLOAD,
                    },
                    seen,
                ),
            )

        self.assertEqual(result.transcript["provider"], "bilibili_content_provider")
        self.assertEqual(result.transcript["subtitle_lang"], "zh-Hans")
        self.assertEqual(result.transcript["subtitle_format"], "bcc")
        self.assertEqual(result.transcript["segments"][0], {"start": "00:00", "end": "00:01", "text": "第一句"})
        self.assertEqual(result.material["material_version"], "watchbrief_v5.transcript_material.v1")
        self.assertEqual(result.material["source"]["kind"], "subtitle_bcc")
        self.assertEqual(result.debug["source_api"], "player-wbi-v2")
        self.assertTrue(any("x/player/wbi/v2" in item["url"] for item in seen))

    def test_player_v2_fallback_is_used_when_wbi_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = fetch_bilibili_subtitle(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                Path(temp_dir),
                urlopen_func=fake_urlopen({
                    "x/web-interface/view": metadata_payload(),
                    "x/player/wbi/v2": urllib.error.URLError("wbi failed"),
                    "x/player/v2": player_payload(subtitles=[track("en-US")]),
                    "i0.hdslb.com/bfs/subtitle": BCC_PAYLOAD,
                }),
            )

        self.assertEqual(result.language, "en-US")
        self.assertEqual(result.debug["source_api"], "player-v2")
        self.assertEqual(result.debug["selected_subtitle_lang"], "en-US")

    def test_language_priority_covers_zh_zh_hans_en_and_en_us(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = fetch_bilibili_subtitle(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                Path(temp_dir),
                languages=("en-US", "en", "zh"),
                urlopen_func=fake_urlopen({
                    "x/web-interface/view": metadata_payload(),
                    "x/player/wbi/v2": player_payload(subtitles=[track("en-US"), track("en"), track("zh"), track("zh-Hans")]),
                    "i0.hdslb.com/bfs/subtitle": BCC_PAYLOAD,
                }),
            )

        self.assertEqual(result.language, "zh-Hans")
        self.assertIn(result.material["language"], {"zh", "en"})

    def test_need_login_subtitle_is_explicit_and_not_no_subtitle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(BilibiliSubtitleError) as context:
                fetch_bilibili_subtitle(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    urlopen_func=fake_urlopen({
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(need_login=True),
                        "x/player/v2": player_payload(need_login=True),
                    }),
                )

        self.assertEqual(context.exception.reason_code, LOGIN_REQUIRED_FOR_SUBTITLE)
        self.assertTrue(context.exception.debug["need_login_subtitle"])
        self.assertFalse(context.exception.debug["audio_fallback_allowed"])

    def test_empty_subtitle_url_track_is_login_required_not_no_subtitle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(BilibiliSubtitleError) as context:
                fetch_bilibili_subtitle(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    urlopen_func=fake_urlopen({
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(subtitles=[track("zh", url="")]),
                        "x/player/v2": player_payload(),
                    }),
                )

        self.assertEqual(context.exception.reason_code, LOGIN_REQUIRED_FOR_SUBTITLE)
        self.assertTrue(context.exception.debug["need_login_subtitle"])
        self.assertFalse(context.exception.debug["audio_fallback_allowed"])

    def test_no_subtitle_available_allows_audio_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(SubtitleUnavailableError) as context:
                fetch_bilibili_subtitle(
                    "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                    Path(temp_dir),
                    urlopen_func=fake_urlopen({
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(),
                        "x/player/v2": player_payload(),
                    }),
                )

        self.assertEqual(context.exception.reason_code, NO_SUBTITLE_AVAILABLE)
        self.assertTrue(context.exception.debug["audio_fallback_allowed"])

    def test_bcc_body_to_transcript_segments(self) -> None:
        segments = parse_bcc_body(BCC_PAYLOAD)

        self.assertEqual(segments[0]["start"], "00:00")
        self.assertEqual(segments[0]["end"], "00:01")
        self.assertEqual(segments[1]["start"], "00:01")
        self.assertEqual(segments[1]["end"], "00:03")

    def test_cookies_do_not_appear_in_debug_or_material(self) -> None:
        secret_cookie = "SESSDATA=SECRET_COOKIE_VALUE"
        seen: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = fetch_bilibili_subtitle(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                Path(temp_dir),
                cookie_header=secret_cookie,
                urlopen_func=fake_urlopen(
                    {
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(subtitles=[track("zh")]),
                        "i0.hdslb.com/bfs/subtitle": BCC_PAYLOAD,
                    },
                    seen,
                ),
            )

        self.assertTrue(any(item["headers"].get("Cookie") == secret_cookie for item in seen))
        public_json = json.dumps({"debug": result.debug, "material": result.material, "transcript": result.transcript}, ensure_ascii=False)
        self.assertNotIn("SECRET_COOKIE_VALUE", public_json)
        self.assertEqual(result.debug["cookies_source"], "cookie_header")

    def test_cookies_from_browser_loader_is_supported_without_exposing_cookie_value(self) -> None:
        def loader(domain_name: str):
            self.assertEqual(domain_name, "bilibili.com")
            return [Cookie("SESSDATA", "BROWSER_SECRET")]

        seen: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            result = fetch_bilibili_subtitle(
                "https://www.bilibili.com/video/BV15qQwB4EZ9/",
                Path(temp_dir),
                cookies_from_browser="chrome",
                browser_cookie_loader=loader,
                urlopen_func=fake_urlopen(
                    {
                        "x/web-interface/view": metadata_payload(),
                        "x/player/wbi/v2": player_payload(subtitles=[track("zh")]),
                        "i0.hdslb.com/bfs/subtitle": BCC_PAYLOAD,
                    },
                    seen,
                ),
            )

        self.assertTrue(any(item["headers"].get("Cookie") == "SESSDATA=BROWSER_SECRET" for item in seen))
        self.assertNotIn("BROWSER_SECRET", json.dumps(result.debug, ensure_ascii=False))
        self.assertEqual(result.debug["cookies_source"], "browser:chrome")

    def test_provider_is_independent_from_youtube_and_rendering_layers(self) -> None:
        source = (ROOT / "scripts" / "bilibili_content_provider.py").read_text(encoding="utf-8")

        forbidden = [
            "youtube",
            "renderer",
            "validator",
            "codex_review",
            "local_extract",
            "qwen",
            "mlx",
            "import yutto",
            "from yutto",
        ]
        lowered = source.lower()
        for marker in forbidden:
            self.assertNotIn(marker, lowered)

    def test_open_source_acknowledgements_are_recorded(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        acknowledgements = (ROOT / "ACKNOWLEDGEMENTS.md").read_text(encoding="utf-8")
        combined = f"{readme}\n{acknowledgements}"

        for marker in (
            "Bilibili Evolved",
            "Bilibili Obsidian Clipper",
            "BilibiliDown",
            "BBDown",
            "yutto",
            "GPL-3.0-only",
        ):
            self.assertIn(marker, combined)


if __name__ == "__main__":
    unittest.main()
