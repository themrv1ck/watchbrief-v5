from __future__ import annotations

import json
import subprocess
import unittest
import urllib.error

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from scripts.acquisition_errors import (
    BILIBILI_LIST_EXPANSION_FAILED,
    XIAOHONGSHU_BOARD_EMPTY,
    XIAOHONGSHU_BOARD_RESOLVER_FAILED,
    BilibiliResolverError,
    PlatformRestrictionError,
    ResolverError,
    XiaohongshuBoardResolverError,
)
from scripts.resolver import (
    bilibili_list_id_from_url,
    build_bilibili_list_resolution,
    drop_internal_download_fields,
    is_bilibili_list_url,
    is_xiaohongshu_board_url,
    refresh_xiaohongshu_media_url_from_browser,
    resolve_url,
    xiaohongshu_board_id_from_url,
    xiaohongshu_dom_board_notes,
)


def runner_with_payload(payload: dict):
    def run(command, capture_output, text, timeout):
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")
    return run


class FakeResponse:
    def __init__(self, body: bytes, url: str = "") -> None:
        self.body = body
        self.url = url

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None


def xiaohongshu_board_html(*, title: str = "体态纠正与康复", total: int = 2, notes: list[dict] | None = None) -> bytes:
    state = {
        "board": {
            "boardDetails": {"id": "69e0e0930000000016034f8a", "name": title, "total": total},
            "boardFeedsMap": {
                "69e0e0930000000016034f8a": {
                    "cursor": "",
                    "hasMore": False,
                    "notes": notes or [],
                }
            },
        }
    }
    html = "<html><head><title>{}</title></head><body><script>window.__INITIAL_STATE__={}</script></body></html>".format(
        title,
        json.dumps(state, ensure_ascii=False),
    )
    return html.encode("utf-8")


def xiaohongshu_board_api_payload(notes: list[dict]) -> bytes:
    return json.dumps({"data": {"notes": notes, "cursor": "", "hasMore": False}}, ensure_ascii=False).encode("utf-8")


def xiaohongshu_video_note() -> dict:
    return {
        "noteId": "video-note-1",
        "xsecToken": "token-video",
        "noteCard": {
            "displayTitle": "小红书视频笔记",
            "type": "video",
            "user": {"nickname": "作者A"},
            "cover": {"url": "https://example.com/video-cover.jpg"},
            "video": {"duration": 42, "url": "https://example.com/video.mp4"},
        },
    }


def xiaohongshu_image_note() -> dict:
    return {
        "noteId": "image-note-1",
        "xsecToken": "token-image",
        "noteCard": {
            "displayTitle": "小红书图文笔记",
            "type": "normal",
            "user": {"nickname": "作者B"},
            "images": [{"url": "https://example.com/image.jpg"}],
            "desc": "图文内容",
        },
    }


class AcquisitionResolverTest(unittest.TestCase):
    def test_bilibili_list_resolution_prefers_real_playlist_title_over_current_video_title(self) -> None:
        videos = [
            {"title": "视频 1", "url": "https://example.com/1"},
            {"title": "视频 2", "url": "https://example.com/2"},
            {"title": "当前视频标题", "url": "https://example.com/3"},
        ]

        resolved = build_bilibili_list_resolution(
            "https://www.bilibili.com/list/ml123",
            videos=videos,
            title="当前视频标题",
            fallback_title="真正的列表名",
            list_id="ml123",
            resolver_debug={},
        )

        self.assertEqual(resolved["title"], "真正的列表名")
        self.assertEqual(resolved["playlist_title"], "真正的列表名")
        self.assertEqual(resolved["list_title"], "真正的列表名")

    def test_netease_short_program_resolves_to_audio_item_without_exposing_media_url(self) -> None:
        def fake_urlopen(request, timeout):
            url = request.full_url
            if "163cn.tv" in url:
                return FakeResponse(b"", "https://y.music.163.com/m/program?id=2543999391")
            if "program/detail" in url:
                payload = {
                    "code": 200,
                    "program": {
                        "id": 2543999391,
                        "name": "E35 知识的缝隙",
                        "duration": 4558373,
                        "scheduledPublishTime": 1715644800000,
                        "mainSong": {"id": 2155899477, "name": "E35 知识的缝隙"},
                        "radio": {"name": "无人知晓"},
                    },
                }
                return FakeResponse(json.dumps(payload).encode("utf-8"), url)
            if "player/url" in url:
                payload = {"code": 200, "data": [{"id": 2155899477, "url": "https://media.example/audio.mp3?sig=SECRET"}]}
                return FakeResponse(json.dumps(payload).encode("utf-8"), url)
            raise AssertionError(url)

        result = resolve_url(
            "https://163cn.tv/6wXAydN",
            urlopen_func=fake_urlopen,
            runner=runner_with_payload({}),
        )

        self.assertEqual(result["source_platform"], "netease_music_podcast")
        self.assertEqual(result["title"], "E35 知识的缝隙")
        self.assertEqual(result["channel"], "无人知晓")
        self.assertEqual(result["videos"][0]["duration"], "4558")
        self.assertEqual(result["videos"][0]["date"], "20240514")
        self.assertEqual(result["videos"][0]["resolver_debug"]["media_url_available"], True)
        public = drop_internal_download_fields(result)
        self.assertNotIn("SECRET", json.dumps(public, ensure_ascii=False))

    def test_xiaohongshu_dom_board_notes_uses_returning_javascript_expression_for_safari(self) -> None:
        def safari_expression_runner(command, capture_output, text, timeout):
            js_source = command[5]
            if "(function()" not in js_source or "return JSON.stringify" not in js_source:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            payload = {
                "provider": "safari-dom",
                "title": "体态纠正与康复",
                "declared_total_count": 1,
                "notes": [{"noteId": "0123456789abcdef01234567", "title": "可见视频", "type": "video"}],
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload, ensure_ascii=False), stderr="")

        result = xiaohongshu_dom_board_notes(
            board_id="69e0e0930000000016034f8a",
            url="https://www.xiaohongshu.com/board/69e0e0930000000016034f8a?source=web_user_page",
            browser="safari",
            runner=safari_expression_runner,
        )

        self.assertEqual(result["provider"], "safari-dom")
        self.assertEqual(len(result["notes"]), 1)

    def test_xiaohongshu_media_refresh_does_not_place_tokenized_note_url_in_process_args(self) -> None:
        tokenized_url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=SECRET_XSEC"

        def safe_runner(command, capture_output, text, timeout):
            self.assertNotIn("SECRET_XSEC", " ".join(command))
            self.assertNotIn(tokenized_url, command)
            self.assertTrue(command[4].endswith(".txt"))
            with open(command[4], encoding="utf-8") as handle:
                self.assertEqual(handle.read(), tokenized_url)
            payload = {
                "media_url": "https://example.com/video.mp4?sig=SECRET_MEDIA",
                "provider": "safari-performance",
                "browser": "safari",
                "media_url_available": True,
                "media_url_type": "media_or_external_url_with_query",
                "resource_count": 1,
                "video_element_count": 1,
            }
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        result = refresh_xiaohongshu_media_url_from_browser(tokenized_url, runner=safe_runner)

        self.assertTrue(result["media_url_available"])
        self.assertEqual(result["provider"], "safari-performance")

    def test_resolve_single_video_url(self) -> None:
        payload = {
            "id": "abc123",
            "title": "Single Video",
            "webpage_url": "https://example.com/watch/abc123",
            "duration_string": "12:34",
            "channel": "Example Channel",
        }

        resolved = resolve_url("https://example.com/watch/abc123", runner=runner_with_payload(payload))

        self.assertEqual(resolved["source_kind"], "single")
        self.assertEqual(resolved["video_count"] if "video_count" in resolved else len(resolved["videos"]), 1)
        self.assertEqual(resolved["videos"][0]["url"], "https://example.com/watch/abc123")
        self.assertEqual(resolved["videos"][0]["title"], "Single Video")

    def test_resolver_preserves_generic_publish_metadata(self) -> None:
        payload = {
            "title": "Dated Video",
            "webpage_url": "https://example.com/watch/dated",
            "duration": 42,
            "upload_date": "20260429",
            "timestamp": 1777464000,
        }

        resolved = resolve_url("https://example.com/watch/dated", runner=runner_with_payload(payload))

        video = resolved["videos"][0]
        self.assertEqual(video["upload_date"], "20260429")
        self.assertEqual(video["timestamp"], 1777464000)

    def test_resolve_list_url(self) -> None:
        payload = {
            "title": "Playlist",
            "entries": [
                {
                    "id": "a1",
                    "title": "Video A",
                    "webpage_url": "https://example.com/watch/a1",
                    "duration": 42,
                    "uploader": "Uploader A",
                    "upload_date": "20260429",
                },
                {
                    "id": "b2",
                    "title": "Video B",
                    "url": "https://example.com/watch/b2",
                    "timestamp": 1777464000,
                },
            ],
        }

        resolved = resolve_url("https://example.com/playlist/mock", runner=runner_with_payload(payload))

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["video_count"], 2)
        self.assertEqual(resolved["videos"][0]["url"], "https://example.com/watch/a1")
        self.assertEqual(resolved["videos"][1]["url"], "https://example.com/watch/b2")
        self.assertEqual(resolved["videos"][0]["upload_date"], "20260429")
        self.assertEqual(resolved["videos"][1]["timestamp"], 1777464000)

    def test_bilibili_multipart_entries_are_enriched_with_page_metadata(self) -> None:
        url = "https://www.bilibili.com/video/BV1KZo5BFEAn/"
        payload = {
            "title": "Udemy - Codex - The Practical Guide",
            "entries": [
                {"id": "BV1KZo5BFEAn", "title": "Video 1", "url": "https://www.bilibili.com/video/BV1KZo5BFEAn?p=1"},
                {"id": "BV1KZo5BFEAn", "title": "Video 2", "url": "https://www.bilibili.com/video/BV1KZo5BFEAn?p=2"},
            ],
        }

        def urlopen(request, timeout):
            self.assertIn("x/web-interface/view", request.full_url)
            view_payload = {
                "code": 0,
                "data": {
                    "bvid": "BV1KZo5BFEAn",
                    "title": "Udemy - Codex - The Practical Guide",
                    "duration": 3600,
                    "pubdate": 1777464000,
                    "owner": {"name": "lmt831"},
                    "pages": [
                        {"page": 1, "cid": 111, "part": "1. Welcome To This Course!", "duration": 37},
                        {"page": 2, "cid": 222, "part": "2. Course Setup", "duration": 95},
                    ],
                },
            }
            return FakeResponse(json.dumps(view_payload).encode("utf-8"))

        resolved = resolve_url(url, runner=runner_with_payload(payload), urlopen_func=urlopen)

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["video_count"], 2)
        self.assertEqual(resolved["videos"][0]["title"], "1. Welcome To This Course!")
        self.assertEqual(resolved["videos"][0]["duration"], 37)
        self.assertEqual(resolved["videos"][0]["cid"], "111")
        self.assertEqual(resolved["videos"][1]["title"], "2. Course Setup")
        self.assertEqual(resolved["videos"][1]["duration"], 95)
        self.assertEqual(resolved["videos"][1]["cid"], "222")
        self.assertEqual(resolved["videos"][0]["channel"], "lmt831")
        self.assertEqual(resolved["videos"][0]["pubdate"], 1777464000)

    def test_bilibili_list_url_is_identified_as_list(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"

        self.assertTrue(is_bilibili_list_url(url))
        self.assertEqual(bilibili_list_id_from_url(url), "ml3621337310")

    def test_bilibili_list_url_expands_multiple_bvids_with_ytdlp(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
        payload = {
            "title": "纳瓦尔",
            "playlist_count": 2,
            "entries": [
                {"id": "BV1GRRXYEEGn", "title": "视频 A", "url": "https://www.bilibili.com/video/BV1GRRXYEEGn"},
                {"id": "BV1yKZLYTEzV", "title": "视频 B", "url": "https://www.bilibili.com/video/BV1yKZLYTEzV"},
            ],
        }

        resolved = resolve_url(url, runner=runner_with_payload(payload), urlopen_func=lambda request, timeout: FakeResponse(b"{}"))

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["title"], "纳瓦尔")
        self.assertEqual(resolved["list_id"], "ml3621337310")
        self.assertEqual(resolved["video_count"], 2)
        self.assertEqual([video["bvid"] for video in resolved["videos"]], ["BV1GRRXYEEGn", "BV1yKZLYTEzV"])
        self.assertEqual(resolved["videos"][0]["url"], "https://www.bilibili.com/video/BV1GRRXYEEGn")
        self.assertEqual(resolved["resolver_debug"]["expansion_provider"], "yt-dlp-flat-playlist")

    def test_bilibili_list_url_does_not_treat_query_bvid_as_single_video(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
        single_payload = {
            "id": "BV1GRRXYEEGn",
            "title": "Only Current Video",
            "webpage_url": "https://www.bilibili.com/video/BV1GRRXYEEGn",
        }

        def urlopen(request, timeout):
            self.assertIn("x/v3/fav/resource/list", request.full_url)
            payload = {
                "code": 0,
                "data": {
                    "info": {"title": "纳瓦尔", "media_count": 2},
                    "medias": [
                        {"bvid": "BV1GRRXYEEGn", "title": "Video A", "duration": 10, "pubtime": 1733389510, "upper": {"name": "UP"}},
                        {"bvid": "BV1yKZLYTEzV", "title": "Video B", "duration": 20, "ctime": 1733389520, "upper": {"name": "UP"}},
                    ],
                },
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        resolved = resolve_url(url, runner=runner_with_payload(single_payload), urlopen_func=urlopen)

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["title"], "纳瓦尔")
        self.assertEqual(resolved["video_count"], 2)
        self.assertEqual([video["bvid"] for video in resolved["videos"]], ["BV1GRRXYEEGn", "BV1yKZLYTEzV"])
        self.assertEqual(resolved["videos"][0]["pubdate"], 1733389510)
        self.assertEqual(resolved["videos"][1]["pubdate"], 1733389520)
        self.assertEqual(resolved["resolver_debug"]["expansion_provider"], "bilibili-fav-list-api")

    def test_bilibili_space_favlist_url_uses_fid_as_list_id_and_real_titles(self) -> None:
        url = "https://space.bilibili.com/163343210/favlist?fid=3958254310&ftype=create"
        single_payload = {
            "id": "BVcurrent",
            "title": "Only Current Video",
            "webpage_url": "https://www.bilibili.com/video/BVcurrent",
        }

        def urlopen(request, timeout):
            self.assertIn("x/v3/fav/resource/list", request.full_url)
            self.assertIn("media_id=3958254310", request.full_url)
            payload = {
                "code": 0,
                "data": {
                    "info": {"title": "AI 收藏夹", "media_count": 2},
                    "medias": [
                        {"bvid": "BV1AAAAAAA11", "title": "真实标题 A", "duration": 10, "upper": {"name": "UP"}},
                        {"bvid": "BV1BBBBBBB22", "title": "真实标题 B", "duration": 20, "upper": {"name": "UP"}},
                    ],
                },
            }
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        resolved = resolve_url(url, runner=runner_with_payload(single_payload), urlopen_func=urlopen)

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["list_id"], "ml3958254310")
        self.assertEqual(resolved["title"], "AI 收藏夹")
        self.assertEqual([video["title"] for video in resolved["videos"]], ["真实标题 A", "真实标题 B"])
        self.assertFalse(any(video["title"].startswith("Video ") for video in resolved["videos"]))

    def test_bilibili_list_placeholder_titles_are_rejected(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
        payload = {
            "title": "纳瓦尔",
            "playlist_count": 2,
            "entries": [
                {"id": "BV1GRRXYEEGn", "title": "Video 1", "url": "https://www.bilibili.com/video/BV1GRRXYEEGn"},
                {"id": "BV1yKZLYTEzV", "title": "Video 2", "url": "https://www.bilibili.com/video/BV1yKZLYTEzV"},
            ],
        }

        with self.assertRaises(BilibiliResolverError) as context:
            resolve_url(url, runner=runner_with_payload(payload), urlopen_func=lambda request, timeout: FakeResponse(b"{}"))

        self.assertEqual(context.exception.reason_code, BILIBILI_LIST_EXPANSION_FAILED)
        self.assertIn("placeholder Video N", str(context.exception))

    def test_bilibili_list_api_title_replaces_ytdlp_placeholder_title(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
        payload = {
            "title": "纳瓦尔",
            "playlist_count": 2,
            "entries": [
                {"id": "BV1GRRXYEEGn", "title": "Video 1", "url": "https://www.bilibili.com/video/BV1GRRXYEEGn"},
                {"id": "BV1yKZLYTEzV", "title": "Video 2", "url": "https://www.bilibili.com/video/BV1yKZLYTEzV"},
            ],
        }

        def urlopen(request, timeout):
            if "x/v3/fav/resource/list" in request.full_url:
                api_payload = {
                    "code": 0,
                    "data": {
                        "info": {"title": "纳瓦尔", "media_count": 2},
                        "medias": [
                            {"bvid": "BV1GRRXYEEGn", "title": "真实标题 A", "duration": 10, "upper": {"name": "UP"}},
                            {"bvid": "BV1yKZLYTEzV", "title": "真实标题 B", "duration": 20, "upper": {"name": "UP"}},
                        ],
                    },
                }
                return FakeResponse(json.dumps(api_payload).encode("utf-8"))
            return FakeResponse(b"{}")

        resolved = resolve_url(url, runner=runner_with_payload(payload), urlopen_func=urlopen)

        self.assertEqual([video["title"] for video in resolved["videos"]], ["真实标题 A", "真实标题 B"])

    def test_bilibili_list_expansion_failure_is_not_single_video_success(self) -> None:
        url = "https://www.bilibili.com/list/ml3621337310?oid=114282922511207&bvid=BV1GRRXYEEGn"
        single_payload = {
            "id": "BV1GRRXYEEGn",
            "title": "Only Current Video",
            "webpage_url": "https://www.bilibili.com/video/BV1GRRXYEEGn",
        }

        def urlopen(request, timeout):
            if "x/v3/fav/resource/list" in request.full_url:
                return FakeResponse(json.dumps({"code": -404, "message": "not found"}).encode("utf-8"))
            return FakeResponse(b"<html><title>empty</title></html>")

        with self.assertRaises(BilibiliResolverError) as context:
            resolve_url(url, runner=runner_with_payload(single_payload), urlopen_func=urlopen)

        self.assertEqual(context.exception.reason_code, BILIBILI_LIST_EXPANSION_FAILED)

    def test_no_playlist_expansion_uses_ytdlp_no_playlist_and_returns_single(self) -> None:
        payload = {
            "id": "BV1single",
            "title": "当前视频",
            "webpage_url": "https://www.bilibili.com/video/BV1single",
            "duration": 120,
        }
        seen_command: list[str] = []

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url("https://www.bilibili.com/video/BV1single?p=2", runner=run, allow_playlist_expansion=False)

        self.assertIn("--no-playlist", seen_command)
        self.assertNotIn("--flat-playlist", seen_command)
        self.assertEqual(resolved["source_kind"], "single")
        self.assertEqual(resolved["title"], "当前视频")

    def test_xiaohongshu_board_url_is_identified_without_ytdlp(self) -> None:
        url = "https://www.xiaohongshu.com/user/profile/abc/board/69e0e0930000000016034f8a?source=web_user_page"

        self.assertTrue(is_xiaohongshu_board_url(url))
        self.assertEqual(xiaohongshu_board_id_from_url(url), "69e0e0930000000016034f8a")
        self.assertEqual(
            xiaohongshu_board_id_from_url("https://www.xiaohongshu.com/share?redirect=%2Fboard%2F69e0e0930000000016034f8a"),
            "69e0e0930000000016034f8a",
        )

    def test_xiaohongshu_board_resolver_parses_video_and_image_notes(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a?source=web_user_page"
        notes = [xiaohongshu_video_note(), xiaohongshu_image_note()]

        def run(command, capture_output, text, timeout):
            raise AssertionError("xiaohongshu board URL must not be sent to yt-dlp")

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                return FakeResponse(xiaohongshu_board_api_payload(notes))
            return FakeResponse(xiaohongshu_board_html(total=2))

        resolved = resolve_url(
            url,
            runner=run,
            urlopen_func=urlopen,
            cookie_header="a1=test-cookie",
        )

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["source_subkind"], "xiaohongshu_board")
        self.assertEqual(resolved["title"], "体态纠正与康复")
        self.assertEqual(resolved["note_count"], 2)
        self.assertEqual(resolved["video_note_count"], 1)
        self.assertEqual(resolved["non_video_count"], 1)
        video, image = resolved["videos"]
        self.assertTrue(video["is_video_note"])
        self.assertEqual(video["note_media_kind"], "video")
        self.assertEqual(video["url"], "https://www.xiaohongshu.com/explore/video-note-1")
        self.assertIn("xsec_token=", video["_download_url"])
        self.assertEqual(video["_media_url"], "https://example.com/video.mp4")
        self.assertTrue(video["resolver_debug"]["media_url_available"])
        self.assertNotIn("https://example.com/video.mp4", json.dumps(video["resolver_debug"], ensure_ascii=False))
        self.assertFalse(image["is_video_note"])
        self.assertTrue(image["is_image_text_note"])
        self.assertEqual(image["skip_reason_code"], "non_video_note")
        self.assertEqual(image["note_media_kind"], "image_text_note")
        public_resolution = json.dumps(drop_internal_download_fields(resolved), ensure_ascii=False)
        self.assertNotIn("token-video", public_resolution)
        self.assertNotIn("token-image", public_resolution)
        self.assertNotIn("xsec_token=", public_resolution)
        self.assertNotIn("test-cookie", json.dumps(resolved, ensure_ascii=False))

    def test_xiaohongshu_board_initial_state_notes_are_used_when_api_unavailable(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"
        notes = [xiaohongshu_video_note()]

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "Internal Error", hdrs=None, fp=None)
            return FakeResponse(xiaohongshu_board_html(total=1, notes=notes))

        resolved = resolve_url(url, runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")), urlopen_func=urlopen, cookie_header="a1=test-cookie")

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(resolved["videos"][0]["note_id"], "video-note-1")
        self.assertEqual(resolved["resolver_debug"]["note_list_provider"], "page-initial-state")

    def test_xiaohongshu_board_initial_state_scans_non_feeds_note_data_when_board_feeds_empty(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"
        notes = [xiaohongshu_video_note(), xiaohongshu_image_note()]
        state = {
            "board": {
                "boardDetails": {"id": "69e0e0930000000016034f8a", "name": "体态纠正与康复", "total": 2},
                "boardFeedsMap": {"69e0e0930000000016034f8a": {"notes": []}},
            },
            "note": {"boardNotes": notes},
        }
        html = "<html><head><title>体态纠正与康复</title></head><body><script>window.__INITIAL_STATE__={}</script></body></html>".format(
            json.dumps(state, ensure_ascii=False)
        ).encode("utf-8")

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "Internal Error", hdrs=None, fp=None)
            return FakeResponse(html)

        resolved = resolve_url(url, runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")), urlopen_func=urlopen, cookie_header="a1=test-cookie")

        self.assertEqual(resolved["note_count"], 2)
        self.assertEqual(resolved["video_note_count"], 1)
        self.assertEqual(resolved["non_video_count"], 1)
        self.assertEqual(resolved["resolver_debug"]["note_list_provider"], "page-initial-state-deep-scan")

    def test_xiaohongshu_board_dom_fallback_is_used_when_api_and_initial_state_have_no_notes(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"
        notes = [xiaohongshu_video_note(), xiaohongshu_image_note()]
        provider_calls: list[dict] = []

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "Internal Error", hdrs=None, fp=None)
            return FakeResponse(xiaohongshu_board_html(total=2))

        def dom_fallback(**kwargs):
            provider_calls.append(kwargs)
            return {"notes": notes, "provider": "chrome-dom", "error": ""}

        resolved = resolve_url(
            url,
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")),
            urlopen_func=urlopen,
            cookie_header="a1=test-cookie",
            xiaohongshu_dom_fallback_func=dom_fallback,
        )

        self.assertEqual(resolved["note_count"], 2)
        self.assertEqual(resolved["video_note_count"], 1)
        self.assertEqual(resolved["non_video_count"], 1)
        self.assertEqual(resolved["resolver_debug"]["note_list_provider"], "chrome-dom")
        self.assertEqual(resolved["resolver_debug"]["provider_attempts"], ["api", "initial_state", "dom"])
        self.assertEqual(provider_calls[0]["board_id"], "69e0e0930000000016034f8a")

    def test_xiaohongshu_board_failure_records_all_realtime_provider_errors_without_historical_cache(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "Internal Error", hdrs=None, fp=None)
            return FakeResponse(xiaohongshu_board_html(total=14))

        def dom_fallback(**kwargs):
            return {"notes": [], "provider": "chrome-dom", "error": "NO_MATCHING_TAB"}

        with self.assertRaises(XiaohongshuBoardResolverError) as context:
            resolve_url(
                url,
                runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")),
                urlopen_func=urlopen,
                cookie_header="a1=test-cookie",
                xiaohongshu_dom_fallback_func=dom_fallback,
            )

        self.assertEqual(context.exception.reason_code, XIAOHONGSHU_BOARD_RESOLVER_FAILED)
        debug = context.exception.debug
        self.assertEqual(debug["provider_attempts"], ["api", "initial_state", "dom"])
        self.assertIn("HTTP Error 500", debug["provider_errors"]["api"])
        self.assertEqual(debug["provider_errors"]["initial_state"], "no notes in initial state")
        self.assertEqual(debug["provider_errors"]["dom"], "NO_MATCHING_TAB")
        self.assertFalse(debug.get("used_historical_sample_cache", True))

    def test_xiaohongshu_board_empty_is_specific_reason(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                return FakeResponse(xiaohongshu_board_api_payload([]))
            return FakeResponse(xiaohongshu_board_html(total=0))

        def dom_fallback(**kwargs):
            return {"notes": [], "provider": "chrome-dom", "error": "NO_MATCHING_TAB"}

        with self.assertRaises(XiaohongshuBoardResolverError) as context:
            resolve_url(
                url,
                runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")),
                urlopen_func=urlopen,
                cookie_header="a1=test-cookie",
                xiaohongshu_dom_fallback_func=dom_fallback,
            )

        self.assertEqual(context.exception.reason_code, XIAOHONGSHU_BOARD_EMPTY)

    def test_xiaohongshu_board_failure_is_specific_reason(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"

        def urlopen(request, timeout):
            if "/api/sns/web/v1/board/note" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 500, "Internal Error", hdrs=None, fp=None)
            return FakeResponse(xiaohongshu_board_html(total=3))

        def dom_fallback(**kwargs):
            return {"notes": [], "provider": "chrome-dom", "error": "NO_MATCHING_TAB"}

        with self.assertRaises(XiaohongshuBoardResolverError) as context:
            resolve_url(
                url,
                runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")),
                urlopen_func=urlopen,
                cookie_header="a1=test-cookie",
                xiaohongshu_dom_fallback_func=dom_fallback,
            )

        self.assertEqual(context.exception.reason_code, XIAOHONGSHU_BOARD_RESOLVER_FAILED)

    def test_xiaohongshu_single_video_still_uses_ytdlp(self) -> None:
        url = "https://www.xiaohongshu.com/explore/66ef836500000000250326ce"
        payload = {
            "id": "66ef836500000000250326ce",
            "title": "小红书单视频",
            "webpage_url": url,
            "duration": 196,
            "uploader": "作者",
        }
        seen_command: list[str] = []

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url(url, runner=run, allow_browser_auth=False)

        self.assertEqual(resolved["source_kind"], "single")
        self.assertIn("yt-dlp", seen_command)

    def test_xiaohongshu_board_browser_cookie_loader_is_used_without_exposing_values(self) -> None:
        url = "https://www.xiaohongshu.com/board/69e0e0930000000016034f8a"
        notes = [xiaohongshu_video_note()]
        seen_cookie_header = ""

        class Cookie:
            name = "a1"
            value = "secret-cookie-value"

        def loader(domain_name):
            self.assertEqual(domain_name, "xiaohongshu.com")
            return [Cookie()]

        def urlopen(request, timeout):
            nonlocal seen_cookie_header
            seen_cookie_header = request.headers.get("Cookie", "")
            if "/api/sns/web/v1/board/note" in request.full_url:
                return FakeResponse(xiaohongshu_board_api_payload(notes))
            return FakeResponse(xiaohongshu_board_html(total=1))

        resolved = resolve_url(
            url,
            runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no yt-dlp")),
            urlopen_func=urlopen,
            cookies_from_browser="safari",
            browser_cookie_loader=loader,
        )

        self.assertEqual(resolved["source_kind"], "list")
        self.assertEqual(seen_cookie_header, "a1=secret-cookie-value")
        self.assertNotIn("secret-cookie-value", json.dumps(resolved, ensure_ascii=False))

    def test_rejects_non_http_url(self) -> None:
        with self.assertRaises(ResolverError):
            resolve_url("/tmp/local-file.txt", runner=runner_with_payload({}))

    def test_platform_restriction_is_classified(self) -> None:
        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Private video. Sign in if you have permission.")

        with self.assertRaises(PlatformRestrictionError) as context:
            resolve_url("https://example.com/private", runner=run)
        self.assertEqual(context.exception.reason_code, "platform_restriction")

    def test_youtube_ip_block_is_platform_restriction_with_debug_summary(self) -> None:
        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="ERROR: YouTube is blocking requests from your IP. Sign in to confirm you're not a bot.",
            )

        with self.assertRaises(PlatformRestrictionError) as context:
            resolve_url("https://www.youtube.com/watch?v=LSQgoNraoVo", runner=run)
        self.assertEqual(context.exception.reason_code, "platform_restriction")
        self.assertEqual(context.exception.debug["reason_code"], "platform_restriction")
        self.assertIn("blocking requests from your IP", context.exception.debug["stderr_summary"])

    def test_bilibili_cookies_from_browser_is_passed_to_metadata_ytdlp(self) -> None:
        seen_command: list[str] = []
        payload = {
            "id": "BV1xx411c7mD",
            "title": "Bili Video",
            "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
            "duration": 12,
            "uploader": "UP主",
        }

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url(
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            runner=run,
            cookies_from_browser="chrome",
        )

        self.assertEqual(resolved["source_kind"], "single")
        self.assertIn("--cookies-from-browser", seen_command)
        self.assertIn("chrome", seen_command)
        self.assertIn("Referer: https://www.bilibili.com/", seen_command)
        self.assertTrue(any("User-Agent:" in item for item in seen_command))
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_source"], "browser:chrome")

    def test_bilibili_default_browser_auth_starts_with_chrome(self) -> None:
        seen_command: list[str] = []
        payload = {
            "id": "BV1xx411c7mD",
            "title": "Bili Video",
            "webpage_url": "https://www.bilibili.com/video/BV1xx411c7mD/",
            "duration": 12,
            "uploader": "UP主",
        }

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url("https://www.bilibili.com/video/BV1xx411c7mD/", runner=run)

        self.assertIn("--cookies-from-browser", seen_command)
        self.assertIn("chrome", seen_command)
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_source"], "browser:chrome")
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_browser_attempts"], ["chrome"])
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["selected_cookies_browser"], "chrome")

    def test_default_browser_auth_falls_back_from_chrome_to_safari(self) -> None:
        seen_commands: list[list[str]] = []
        payload = {
            "id": "y_uQHoVhhcw",
            "title": "YouTube Video",
            "webpage_url": "https://www.youtube.com/watch?v=y_uQHoVhhcw",
            "duration": 12,
            "uploader": "YouTube Channel",
        }

        def run(command, capture_output, text, timeout):
            seen_commands.append(list(command))
            if "chrome" in command:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="Extracted 0 cookies from chrome")
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url("https://www.youtube.com/watch?v=y_uQHoVhhcw", runner=run)

        self.assertEqual(len(seen_commands), 2)
        self.assertIn("chrome", seen_commands[0])
        self.assertIn("safari", seen_commands[1])
        debug = resolved["videos"][0]["resolver_debug"]
        self.assertEqual(debug["cookies_browser_attempts"], ["chrome", "safari"])
        self.assertEqual(debug["selected_cookies_browser"], "safari")
        self.assertEqual(debug["cookies_fallback_reason"], "extracted_0_cookies")
        self.assertEqual(debug["cookies_source"], "browser:safari")

    def test_explicit_chrome_does_not_fallback_to_safari(self) -> None:
        seen_commands: list[list[str]] = []

        def run(command, capture_output, text, timeout):
            seen_commands.append(list(command))
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Extracted 0 cookies from chrome")

        with self.assertRaises(ResolverError):
            resolve_url("https://www.youtube.com/watch?v=y_uQHoVhhcw", runner=run, cookies_from_browser="chrome")

        self.assertEqual(len(seen_commands), 1)
        self.assertIn("chrome", seen_commands[0])
        self.assertNotIn("safari", seen_commands[0])

    def test_explicit_browser_cookies_are_passed_to_non_bilibili_resolver(self) -> None:
        seen_command: list[str] = []
        payload = {
            "id": "y_uQHoVhhcw",
            "title": "YouTube Video",
            "webpage_url": "https://www.youtube.com/watch?v=y_uQHoVhhcw",
            "duration": 12,
            "uploader": "YouTube Channel",
        }

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url(
            "https://www.youtube.com/watch?v=y_uQHoVhhcw",
            runner=run,
            cookies_from_browser="chrome",
        )

        self.assertEqual(resolved["source_kind"], "single")
        self.assertIn("--cookies-from-browser", seen_command)
        self.assertIn("chrome", seen_command)
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_source"], "browser:chrome")

    def test_explicit_safari_cookies_are_passed_to_non_bilibili_resolver(self) -> None:
        seen_command: list[str] = []
        payload = {
            "id": "y_uQHoVhhcw",
            "title": "YouTube Video",
            "webpage_url": "https://www.youtube.com/watch?v=y_uQHoVhhcw",
            "duration": 12,
            "uploader": "YouTube Channel",
        }

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

        resolved = resolve_url(
            "https://www.youtube.com/watch?v=y_uQHoVhhcw",
            runner=run,
            cookies_from_browser="safari",
        )

        self.assertEqual(resolved["source_kind"], "single")
        self.assertIn("--cookies-from-browser", seen_command)
        self.assertIn("safari", seen_command)
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["cookies_source"], "browser:safari")

    def test_bilibili_412_without_browser_auth_is_specific_reason(self) -> None:
        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

        with self.assertRaises(BilibiliResolverError) as context:
            resolve_url(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                runner=run,
                allow_browser_auth=False,
            )

        self.assertEqual(context.exception.reason_code, "bilibili_412_blocked")
        self.assertEqual(context.exception.debug["reason_code"], "bilibili_412_blocked")

    def test_bilibili_412_with_browser_auth_enters_metadata_fallback(self) -> None:
        calls: list[str] = []

        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if "bilibili.com/video" in request.full_url:
                html = (
                    "<script>window.__INITIAL_STATE__ = "
                    + json.dumps({
                        "videoData": {
                            "bvid": "BV1xx411c7mD",
                            "cid": 12345,
                            "title": "Fallback Title",
                            "duration": 88,
                            "pubdate": 1733389510,
                            "owner": {"name": "Fallback UP"},
                        }
                    })
                    + ";</script>"
                )
                return FakeResponse(html.encode("utf-8"))
            return FakeResponse(json.dumps({"data": {}}).encode("utf-8"))

        resolved = resolve_url(
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            runner=run,
            cookies_from_browser="chrome",
            urlopen_func=urlopen,
        )

        self.assertEqual(resolved["source_kind"], "single")
        self.assertEqual(resolved["videos"][0]["title"], "Fallback Title")
        self.assertEqual(resolved["videos"][0]["cid"], "12345")
        self.assertEqual(resolved["videos"][0]["channel"], "Fallback UP")
        self.assertEqual(resolved["videos"][0]["pubdate"], 1733389510)
        self.assertEqual(resolved["pubdate"], 1733389510)
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["resolver_method"], "bilibili_metadata_fallback")
        self.assertTrue(resolved["videos"][0]["resolver_debug"]["fallback_success"])
        self.assertTrue(any("bilibili.com/video" in url for url in calls))

    def test_bilibili_metadata_fallback_uses_view_api_when_page_markers_missing(self) -> None:
        calls: list[str] = []

        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if "bilibili.com/video" in request.full_url:
                return FakeResponse(b"<html>risk control page</html>")
            if "x/web-interface/view" in request.full_url:
                payload = {
                    "data": {
                        "bvid": "BV1xx411c7mD",
                        "cid": 45678,
                        "title": "View API Title",
                        "duration": 66,
                        "pubdate": 1733389510,
                        "owner": {"name": "View API UP"},
                    }
                }
                return FakeResponse(json.dumps(payload).encode("utf-8"))
            return FakeResponse(json.dumps({"data": {}}).encode("utf-8"))

        resolved = resolve_url(
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            runner=run,
            cookies_from_browser="chrome",
            urlopen_func=urlopen,
        )

        self.assertEqual(resolved["videos"][0]["title"], "View API Title")
        self.assertEqual(resolved["videos"][0]["cid"], "45678")
        self.assertEqual(resolved["videos"][0]["pubdate"], 1733389510)
        self.assertEqual(resolved["videos"][0]["resolver_debug"]["fallback_method"], "bilibili_view_api")
        self.assertTrue(any("x/web-interface/view" in url for url in calls))

    def test_bilibili_metadata_fallback_failure_is_specific(self) -> None:
        def run(command, capture_output, text, timeout):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

        def urlopen(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 412, "Precondition Failed", hdrs=None, fp=None)

        with self.assertRaises(BilibiliResolverError) as context:
            resolve_url(
                "https://www.bilibili.com/video/BV1xx411c7mD/",
                runner=run,
                cookies_from_browser="chrome",
                urlopen_func=urlopen,
            )

        self.assertEqual(context.exception.reason_code, "bilibili_412_blocked")
        self.assertEqual(context.exception.debug["fallback_method"], "bilibili_page_metadata")
        self.assertFalse(context.exception.debug["fallback_success"])

    def test_bilibili_cookie_header_is_not_put_into_ytdlp_command(self) -> None:
        seen_command: list[str] = []

        def run(command, capture_output, text, timeout):
            seen_command.extend(command)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="HTTP Error 412: Precondition Failed")

        def urlopen(request, timeout):
            html = (
                "<script>window.__INITIAL_STATE__ = "
                + json.dumps({
                    "videoData": {
                        "bvid": "BV1xx411c7mD",
                        "cid": 12345,
                        "title": "Fallback Title",
                        "duration": 88,
                    }
                })
                + ";</script>"
            )
            return FakeResponse(html.encode("utf-8"))

        resolve_url(
            "https://www.bilibili.com/video/BV1xx411c7mD/",
            runner=run,
            cookie_header="SESSDATA=secret-cookie",
            urlopen_func=urlopen,
        )

        self.assertNotIn("SESSDATA=secret-cookie", " ".join(seen_command))


if __name__ == "__main__":
    unittest.main()
