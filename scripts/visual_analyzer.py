#!/usr/bin/env python3
"""Deterministic full-timeline visual evidence for WatchBefore using MiMo-VL."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import select
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from .cookie_strategy import cookie_fallback_reason_from_text, normalize_cookie_browser_attempts
    from .stability import stable_sha256
except ImportError:  # pragma: no cover
    from cookie_strategy import cookie_fallback_reason_from_text, normalize_cookie_browser_attempts
    from stability import stable_sha256


MIMO_VL_REQUIRED_VERSION = "0.5.0"
MIMO_VL_REVISION = "4bfb270765825d2fa059011deb4c96fdd579be6f"
MIMO_VL_MODEL_SUBDIR = Path("models/MiMo-VL-7B-RL-2508")
MIMO_VL_EXPECTED_SHARD_SIZES = {
    "model-00001-of-00004.safetensors": 4_612_695_408,
    "model-00002-of-00004.safetensors": 4_937_303_136,
    "model-00003-of-00004.safetensors": 4_982_109_888,
    "model-00004-of-00004.safetensors": 2_080_418_376,
}
MIMO_VL_REQUIRED_MODEL_FILES = (
    "config.json",
    "model.safetensors.index.json",
    *MIMO_VL_EXPECTED_SHARD_SIZES,
)
VISUAL_EVIDENCE_VERSION = "watchbrief_v6.visual_evidence.v2"
VISUAL_PROMPT_VERSION = "watchbrief_v5.mimo_visual_prompt.v1"
DEFAULT_MAX_FRAME_GAP_SECONDS = 10.0
DEFAULT_MAX_FRAMES_PER_REQUEST = 6
DEFAULT_MAX_TOKENS = 4096
DEFAULT_INFERENCE_TIMEOUT = 900
DEFAULT_STARTUP_TIMEOUT = 1800
DEFAULT_DOWNLOAD_TIMEOUT = 1800
DEFAULT_WORKER = Path(__file__).with_name("mimo_vl_worker.py")
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


class VisualAnalysisError(RuntimeError):
    def __init__(
        self,
        stage: str,
        reason_code: str,
        message: str,
        *,
        debug: Optional[dict[str, Any]] = None,
    ) -> None:
        self.stage = stage
        self.reason_code = reason_code
        self.message = message
        self.debug = dict(debug or {})
        super().__init__(f"{stage}:{reason_code}: {message}")


@dataclass
class VisualDownloadResult:
    video_path: Path
    command: list[str]
    method: str
    cookies_browser_attempts: list[str]
    selected_cookies_browser: str
    cookies_fallback_reason: str


def resolve_mimo_vl_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("WATCHBRIEF_MIMO_VL_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "MiMo-VL-7B-RL-2508"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _run_version_check(python_bin: Path) -> str:
    try:
        result = subprocess.run(
            [
                str(python_bin),
                "-c",
                "from importlib.metadata import version; print(version('mlx-vlm'))",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def verify_mimo_vl_installation(root: str | Path | None = None) -> dict[str, Any]:
    resolved = resolve_mimo_vl_root(root).resolve()
    python_bin = resolved / ".venv" / "bin" / "python"
    model_dir = resolved / MIMO_VL_MODEL_SUBDIR
    missing_files = [name for name in MIMO_VL_REQUIRED_MODEL_FILES if not (model_dir / name).is_file()]
    wrong_shard_sizes = {
        name: (model_dir / name).stat().st_size
        for name, expected in MIMO_VL_EXPECTED_SHARD_SIZES.items()
        if (model_dir / name).is_file() and (model_dir / name).stat().st_size != expected
    }
    version = _run_version_check(python_bin) if python_bin.is_file() else ""
    errors: list[str] = []
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        errors.append("MiMo-VL MLX requires an Apple Silicon Mac")
    if not python_bin.is_file() or not os.access(python_bin, os.X_OK):
        errors.append(f"missing executable Python: {python_bin}")
    if missing_files:
        errors.append("missing official BF16 model files: " + ", ".join(missing_files))
    if wrong_shard_sizes:
        errors.append("official BF16 shard sizes do not match")
    if version != MIMO_VL_REQUIRED_VERSION:
        errors.append(
            f"mlx-vlm must be exactly {MIMO_VL_REQUIRED_VERSION}; current={version or 'unavailable'}"
        )
    return {
        "ok": not errors,
        "root": str(resolved),
        "python": str(python_bin),
        "model": str(model_dir),
        "mlx_vlm_version": version,
        "required_mlx_vlm_version": MIMO_VL_REQUIRED_VERSION,
        "official_revision": MIMO_VL_REVISION,
        "missing_files": missing_files,
        "wrong_shard_sizes": wrong_shard_sizes,
        "errors": errors,
    }


def _cookie_args(
    *,
    browser: str = "",
    cookie_file: Path | None = None,
) -> list[str]:
    if browser:
        return ["--cookies-from-browser", browser]
    if cookie_file:
        return ["--cookies", str(cookie_file.expanduser())]
    return []


def _pick_downloaded_video(output_dir: Path) -> Path:
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS and path.stat().st_size > 0
    ]
    if len(candidates) != 1:
        raise VisualAnalysisError(
            "visual_acquisition",
            "visual_video_output_invalid",
            f"expected exactly one downloaded video, found {len(candidates)}",
        )
    return candidates[0]


def download_visual_video(
    source_url: str,
    output_dir: Path,
    *,
    cookies_from_browser: str | None = None,
    cookie_file: Path | None = None,
    cookie_browser_attempts: Iterable[str] | None = None,
    yt_dlp_bin: str = "yt-dlp",
    timeout: int = DEFAULT_DOWNLOAD_TIMEOUT,
) -> VisualDownloadResult:
    source_path = Path(str(source_url or "")).expanduser()
    if source_path.is_file():
        return VisualDownloadResult(source_path.resolve(), [], "local_file", [], "", "")
    if not str(source_url or "").strip():
        raise VisualAnalysisError(
            "visual_acquisition",
            "visual_source_missing",
            "visual analysis requires a video source URL or local file",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    explicit_browser = str(cookies_from_browser or "").strip()
    attempts = (
        (explicit_browser,)
        if explicit_browser
        else normalize_cookie_browser_attempts(cookie_browser_attempts)
    )
    browser_attempts = list(attempts) if attempts else [""]
    fallback_reason = ""
    final_command: list[str] = []
    final_stderr = ""
    for browser in browser_attempts:
        for path in output_dir.iterdir():
            if path.is_file() and path.name.startswith("visual-source."):
                path.unlink()
        command = [
            yt_dlp_bin,
            "--no-playlist",
            "--no-write-info-json",
            "--no-write-thumbnail",
            "-f",
            "bestvideo[height<=720]/best[height<=720]/bestvideo/best",
            "--merge-output-format",
            "mp4",
            "-o",
            str(output_dir / "visual-source.%(ext)s"),
            *_cookie_args(browser=browser, cookie_file=cookie_file if not browser else None),
            str(source_url),
        ]
        final_command = command
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise VisualAnalysisError(
                "visual_acquisition",
                "visual_video_download_timeout",
                "visual video acquisition timed out",
                debug={"timeout": timeout, "selected_cookies_browser": browser},
            ) from exc
        except OSError as exc:
            raise VisualAnalysisError(
                "visual_acquisition",
                "visual_video_downloader_unavailable",
                f"cannot start yt-dlp: {exc}",
            ) from exc
        final_stderr = (completed.stderr or completed.stdout or "").strip()
        if completed.returncode == 0:
            video_path = _pick_downloaded_video(output_dir)
            return VisualDownloadResult(
                video_path,
                command,
                "yt_dlp",
                [value for value in browser_attempts if value],
                browser,
                fallback_reason,
            )
        current_reason = cookie_fallback_reason_from_text("", final_stderr)
        if browser and current_reason and browser != browser_attempts[-1]:
            fallback_reason = current_reason
            continue
        break
    raise VisualAnalysisError(
        "visual_acquisition",
        "visual_video_download_failed",
        "yt-dlp did not produce a usable visual source",
        debug={
            "returncode": None,
            "stderr": " ".join(final_stderr.split())[:500],
            "command": final_command,
            "cookies_browser_attempts": [value for value in browser_attempts if value],
            "cookies_fallback_reason": fallback_reason,
        },
    )


def video_path_from_result(result: Any) -> Path:
    if isinstance(result, VisualDownloadResult):
        path = result.video_path
    elif isinstance(result, (str, Path)):
        path = Path(result)
    elif isinstance(result, dict):
        path = Path(str(result.get("video_path") or ""))
    else:
        path = Path(str(getattr(result, "video_path", "") or ""))
    if not path.is_file() or path.stat().st_size <= 0:
        raise VisualAnalysisError(
            "visual_acquisition",
            "visual_video_output_invalid",
            "visual acquisition result has no usable video file",
        )
    return path


def visual_download_debug(result: Any) -> dict[str, Any]:
    if isinstance(result, VisualDownloadResult):
        return {
            "method": result.method,
            "video_path": str(result.video_path),
            "cookies_browser_attempts": result.cookies_browser_attempts,
            "selected_cookies_browser": result.selected_cookies_browser,
            "cookies_fallback_reason": result.cookies_fallback_reason,
        }
    path = video_path_from_result(result)
    return {"method": "dependency", "video_path": str(path)}


def deterministic_sample_timestamps(duration_seconds: float, max_gap_seconds: float) -> list[float]:
    duration = float(duration_seconds)
    max_gap = float(max_gap_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("video duration must be a positive finite number")
    if not math.isfinite(max_gap) or max_gap <= 0:
        raise ValueError("max frame gap must be a positive finite number")
    interval_count = max(4, int(math.ceil(duration / max_gap)))
    timestamps = [round(duration * index / interval_count, 6) for index in range(interval_count + 1)]
    timestamps[0] = 0.0
    timestamps[-1] = round(duration, 6)
    return timestamps


def observed_max_gap(timestamps: Iterable[float]) -> float:
    values = list(timestamps)
    if len(values) < 2:
        return 0.0
    return round(max(right - left for left, right in zip(values, values[1:])), 6)


def probe_duration(
    video_path: Path,
    *,
    ffprobe_bin: str = "ffprobe",
    timeout: int = 120,
) -> float:
    try:
        result = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_video_probe_failed",
            f"ffprobe failed: {type(exc).__name__}: {exc}",
        ) from exc
    if result.returncode != 0:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_video_probe_failed",
            "ffprobe did not return a valid duration",
            debug={"stderr": " ".join((result.stderr or result.stdout or "").split())[:500]},
        )
    try:
        duration = float((json.loads(result.stdout).get("format") or {}).get("duration"))
    except Exception as exc:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_video_duration_invalid",
            "ffprobe returned an invalid duration",
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_video_duration_invalid",
            "video duration must be a positive finite number",
        )
    return duration


def extract_frame(
    video_path: Path,
    destination: Path,
    *,
    timestamp_seconds: float,
    is_last: bool,
    duration_seconds: float,
    ffmpeg_bin: str = "ffmpeg",
    timeout: int = 180,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    scale_filter = "scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease"
    if is_last:
        seek = ["-sseof", f"-{min(1.0, duration_seconds):.6f}"]
        video_filter = ["-vf", f"reverse,{scale_filter}"]
    else:
        seek = ["-ss", f"{max(0.0, timestamp_seconds):.6f}"]
        video_filter = ["-vf", scale_filter]
    command = [
        ffmpeg_bin,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *seek,
        "-i",
        str(video_path),
        "-map",
        "0:v:0",
        *video_filter,
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuvj420p",
        "-q:v",
        "2",
        str(destination),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_frame_extraction_failed",
            f"ffmpeg failed: {type(exc).__name__}: {exc}",
        ) from exc
    if result.returncode != 0 or not destination.is_file() or destination.stat().st_size <= 0:
        raise VisualAnalysisError(
            "visual_analysis",
            "visual_frame_extraction_failed",
            "ffmpeg did not produce a usable frame",
            debug={"stderr": " ".join((result.stderr or result.stdout or "").split())[:500]},
        )


def build_frame_manifest(
    video_path: Path,
    work_dir: Path,
    *,
    max_gap_seconds: float,
    ffmpeg_bin: str = "ffmpeg",
    ffprobe_bin: str = "ffprobe",
) -> tuple[dict[str, Any], list[Path]]:
    duration = probe_duration(video_path, ffprobe_bin=ffprobe_bin)
    timestamps = deterministic_sample_timestamps(duration, max_gap_seconds)
    frames_dir = work_dir / "frames"
    frame_rows: list[dict[str, Any]] = []
    frame_paths: list[Path] = []
    for index, timestamp in enumerate(timestamps):
        filename = f"frame_{index:04d}_{int(round(timestamp * 1000)):012d}ms.jpg"
        path = frames_dir / filename
        extract_frame(
            video_path,
            path,
            timestamp_seconds=timestamp,
            is_last=index == len(timestamps) - 1,
            duration_seconds=duration,
            ffmpeg_bin=ffmpeg_bin,
        )
        frame_rows.append(
            {
                "index": index,
                "timestamp_seconds": timestamp,
                "endpoint": "start" if index == 0 else "end" if index == len(timestamps) - 1 else "",
                "sha256": sha256_file(path),
            }
        )
        frame_paths.append(path)
    manifest = {
        "video_sha256": sha256_file(video_path),
        "duration_seconds": round(duration, 6),
        "sampling": {
            "method": "uniform_full_timeline_endpoints_v1",
            "requested_max_gap_seconds": float(max_gap_seconds),
            "observed_max_gap_seconds": observed_max_gap(timestamps),
            "includes_start": True,
            "includes_end": True,
            "timestamps_seconds": timestamps,
        },
        "frames": frame_rows,
    }
    return manifest, frame_paths


def build_visual_prompt(frame_manifest: dict[str, Any]) -> str:
    image_contract = [
        {
            "slot": slot,
            "observation": "该帧明确可见的主体、环境、操作或图表；禁止推断声音",
            "visible_text": ["画面中实际可读的文字"],
            "actions": ["画面中实际可见的动作或操作"],
            "uncertainty": "证据不足或画面歧义；没有则空字符串",
        }
        for slot, _frame in enumerate(frame_manifest["frames"])
    ]
    batch = frame_manifest.get("batch") if isinstance(frame_manifest.get("batch"), dict) else {}
    batch_index = int(batch.get("index") or 1)
    batch_count = int(batch.get("count") or 1)
    scope_text = (
        "覆盖视频完整时轴，并包含首帧和末帧"
        if batch_count == 1
        else f"是完整时轴的第 {batch_index}/{batch_count} 批"
    )
    return (
        "你是 WatchBefore 的视频画面证据提取器。附件图片按传入顺序组成一个固定图片批次，"
        f"{scope_text}。\n"
        "你只能描述附件中直接可见的事实：主体、场景、屏幕操作、图表、字幕和动作。"
        "不得根据标题、作者、简介、音轨或常识猜测；不得把画面推断写成视频说过的话。\n"
        f"本批正好有 {len(image_contract)} 张附件图片。items 必须按附件顺序逐项一一对应，"
        f"slot 必须严格等于 {list(range(len(image_contract)))}，不能删除、合并、新增或改号。\n"
        "不要把表格行、OCR 行、画面里的列表项或你推测的视频片段拆成 item；"
        "一个 item 只能对应一张附件图片。\n"
        "batch_summary 只总结本批附件共同展示的主要视觉内容。"
        "caveats 记录仅靠这些附件无法确认的事项。\n"
        "严格输出这个 JSON 结构：\n"
        + json.dumps(
            {
                "batch_summary": "本批附件的主要视觉内容",
                "items": image_contract,
                "caveats": ["仅靠这些附件无法确认的事项"],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise VisualAnalysisError(
            "visual_analysis",
            "mimo_visual_invalid_output",
            f"{path} must be a string array",
        )
    return [item.strip() for item in value if item.strip()]


def validate_visual_result(
    payload: dict[str, Any],
    frame_manifest: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise VisualAnalysisError(
            "visual_analysis",
            "mimo_visual_invalid_output",
            "MiMo-VL result must be a JSON object",
        )
    summary = payload.get("batch_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise VisualAnalysisError(
            "visual_analysis",
            "mimo_visual_invalid_output",
            "batch_summary must be a non-empty string",
        )
    rows = payload.get("items")
    expected = frame_manifest["frames"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise VisualAnalysisError(
            "visual_analysis",
            "mimo_visual_frame_coverage_mismatch",
            f"MiMo-VL returned {len(rows) if isinstance(rows, list) else 0} image rows; expected {len(expected)}",
        )
    normalized_rows: list[dict[str, Any]] = []
    for slot, (expected_row, row) in enumerate(zip(expected, rows)):
        if not isinstance(row, dict):
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_visual_invalid_output",
                "every items entry must be an object",
            )
        if row.get("slot") != slot:
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_visual_frame_coverage_mismatch",
                "MiMo-VL image slots do not match attachment order",
            )
        index = expected_row["index"]
        observation = row.get("observation")
        uncertainty = row.get("uncertainty")
        if not isinstance(observation, str) or not observation.strip():
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_visual_invalid_output",
                f"items[{slot}].observation must be non-empty",
            )
        if not isinstance(uncertainty, str):
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_visual_invalid_output",
                f"items[{slot}].uncertainty must be a string",
            )
        normalized_rows.append(
            {
                "index": index,
                "timestamp_seconds": round(float(expected_row["timestamp_seconds"]), 6),
                "observation": observation.strip(),
                "visible_text": _string_list(row.get("visible_text"), f"items[{slot}].visible_text"),
                "actions": _string_list(row.get("actions"), f"items[{slot}].actions"),
                "uncertainty": uncertainty.strip(),
            }
        )
    caveats = _string_list(payload.get("caveats"), "caveats")
    return {
        "overall_visual_summary": summary.strip(),
        "frames": normalized_rows,
        "visual_caveats": caveats,
    }


class MimoVLVisualAnalyzer:
    """Keep one MiMo-VL process alive for a complete WatchBefore run."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_frames_per_request: int = DEFAULT_MAX_FRAMES_PER_REQUEST,
        timeout: int = DEFAULT_INFERENCE_TIMEOUT,
        startup_timeout: int = DEFAULT_STARTUP_TIMEOUT,
        worker_script: str | Path = DEFAULT_WORKER,
        working_directory: str | Path | None = None,
    ) -> None:
        check = verify_mimo_vl_installation(root)
        if not check["ok"]:
            raise VisualAnalysisError(
                "visual_preflight",
                "mimo_vl_unavailable",
                "; ".join(check["errors"]),
                debug=check,
            )
        if max_tokens <= 0 or max_frames_per_request <= 0 or timeout <= 0 or startup_timeout <= 0:
            raise ValueError("MiMo-VL token and timeout values must be positive")
        self.root = Path(check["root"])
        self.python_bin = Path(check["python"])
        self.model = Path(check["model"])
        self.max_tokens = int(max_tokens)
        self.max_frames_per_request = int(max_frames_per_request)
        self.timeout = int(timeout)
        self.startup_timeout = int(startup_timeout)
        self.worker_script = Path(worker_script).expanduser().resolve()
        self.working_directory = Path(working_directory or Path(__file__).resolve().parents[1]).resolve()
        self.process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._closed = False

    def identity(self) -> dict[str, str]:
        return {
            "provider": "mimo-vl-mlx",
            "model": str(self.model),
            "version": f"mlx-vlm-{MIMO_VL_REQUIRED_VERSION}",
        }

    def _start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        command = [
            str(self.python_bin),
            "-u",
            str(self.worker_script),
            "--model",
            str(self.model),
            "--max-tokens",
            str(self.max_tokens),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                cwd=str(self.working_directory),
            )
        except OSError as exc:
            raise VisualAnalysisError(
                "visual_preflight",
                "mimo_vl_startup_failed",
                f"cannot start MiMo-VL worker: {exc}",
            ) from exc
        ready = self._read(self.startup_timeout)
        if ready.get("type") != "ready" or ready.get("ok") is not True:
            self._terminate()
            raise VisualAnalysisError(
                "visual_preflight",
                str(ready.get("reason_code") or "mimo_vl_startup_failed"),
                str(ready.get("message") or "MiMo-VL worker startup failed"),
                debug=ready.get("metadata") if isinstance(ready.get("metadata"), dict) else {},
            )
        if ready.get("identity") != self.identity():
            self._terminate()
            raise VisualAnalysisError(
                "visual_preflight",
                "mimo_vl_identity_mismatch",
                "MiMo-VL worker identity does not match the configured runtime",
            )

    def _read(self, timeout: int) -> dict[str, Any]:
        process = self.process
        if process is None or process.stdout is None:
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_process_unavailable",
                "MiMo-VL worker is unavailable",
            )
        readable, _, _ = select.select([process.stdout], [], [], timeout)
        if not readable:
            self._terminate()
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_timeout",
                "MiMo-VL worker response timed out",
                debug={"timeout": timeout},
            )
        line = process.stdout.readline()
        if not line:
            returncode = process.poll()
            self._terminate()
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_process_failed",
                "MiMo-VL worker exited unexpectedly",
                debug={"returncode": returncode},
            )
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            self._terminate()
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_protocol_error",
                "MiMo-VL worker returned non-JSON protocol data",
            ) from exc
        if not isinstance(payload, dict):
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_protocol_error",
                "MiMo-VL worker protocol response must be an object",
            )
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.stdin is None or process.poll() is not None:
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_process_unavailable",
                "MiMo-VL worker has exited",
            )
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._terminate()
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_process_failed",
                "cannot send a request to MiMo-VL worker",
            ) from exc

    def analyze_video(
        self,
        video_path: Path,
        work_dir: Path,
        *,
        max_gap_seconds: float = DEFAULT_MAX_FRAME_GAP_SECONDS,
        ffmpeg_bin: str = "ffmpeg",
        ffprobe_bin: str = "ffprobe",
    ) -> dict[str, Any]:
        if self._closed:
            raise VisualAnalysisError(
                "visual_analysis",
                "mimo_vl_process_closed",
                "MiMo-VL analyzer is already closed",
            )
        frame_manifest, frame_paths = build_frame_manifest(
            video_path,
            work_dir,
            max_gap_seconds=max_gap_seconds,
            ffmpeg_bin=ffmpeg_bin,
            ffprobe_bin=ffprobe_bin,
        )
        frame_rows = frame_manifest["frames"]
        batches = [
            (
                frame_rows[start:start + self.max_frames_per_request],
                frame_paths[start:start + self.max_frames_per_request],
            )
            for start in range(0, len(frame_rows), self.max_frames_per_request)
        ]
        batch_analyses: list[dict[str, Any]] = []
        request_count = 0
        adaptive_split_count = 0
        single_frame_retry_count = 0

        def analyze_batch(
            batch_rows: list[dict[str, Any]],
            batch_paths: list[Path],
            batch_index: int,
            split_depth: int = 0,
            single_frame_retried: bool = False,
        ) -> list[dict[str, Any]]:
            nonlocal request_count, adaptive_split_count, single_frame_retry_count
            batch_manifest = {
                **frame_manifest,
                "frames": batch_rows,
                "batch": {"index": batch_index, "count": len(batches)},
            }
            with self._lock:
                self._start()
                self._write(
                    {
                        "action": "analyze",
                        "prompt": build_visual_prompt(batch_manifest),
                        "image_paths": [str(path) for path in batch_paths],
                    }
                )
                response = self._read(self.timeout)
            request_count += 1
            if response.get("ok") is not True:
                reason = str(response.get("reason_code") or "mimo_vl_generation_failed")
                # A truncated verbose response can fail JSON parsing even when
                # every frame is usable. Retry with fewer images; each accepted
                # result still passes the same strict per-frame validation.
                if reason == "mimo_vl_invalid_json" and len(batch_rows) > 1:
                    midpoint = len(batch_rows) // 2
                    adaptive_split_count += 1
                    return (
                        analyze_batch(batch_rows[:midpoint], batch_paths[:midpoint], batch_index, split_depth + 1)
                        + analyze_batch(batch_rows[midpoint:], batch_paths[midpoint:], batch_index, split_depth + 1)
                    )
                if reason == "mimo_vl_invalid_json" and not single_frame_retried:
                    single_frame_retry_count += 1
                    return analyze_batch(batch_rows, batch_paths, batch_index, split_depth, True)
                raise VisualAnalysisError(
                    "visual_analysis",
                    reason,
                    str(response.get("message") or "MiMo-VL visual analysis failed"),
                    debug={
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "split_depth": split_depth,
                        "frame_indexes": [row["index"] for row in batch_rows],
                        **(
                            response.get("metadata")
                            if isinstance(response.get("metadata"), dict)
                            else {}
                        ),
                    },
                )
            return [validate_visual_result(response.get("result"), batch_manifest)]

        for batch_offset, (batch_rows, batch_paths) in enumerate(batches):
            batch_analyses.extend(analyze_batch(batch_rows, batch_paths, batch_offset + 1))

        if len(batch_analyses) == 1:
            analysis = batch_analyses[0]
        else:
            batch_summaries = []
            combined_frames = []
            combined_caveats = []
            for batch_index, batch_analysis in enumerate(batch_analyses, start=1):
                rows = batch_analysis["frames"]
                batch_summaries.append(
                    {
                        "batch_index": batch_index,
                        "start_timestamp_seconds": rows[0]["timestamp_seconds"],
                        "end_timestamp_seconds": rows[-1]["timestamp_seconds"],
                        "summary": batch_analysis["overall_visual_summary"],
                    }
                )
                combined_frames.extend(rows)
                combined_caveats.extend(
                    f"第 {batch_index} 批：{caveat}"
                    for caveat in batch_analysis["visual_caveats"]
                )
            analysis = {
                "overall_visual_summary": (
                    f"完整时轴已分 {len(batch_analyses)} 批分析；"
                    "批次摘要按时间顺序保存在 batch_summaries，逐帧事实保存在 frames。"
                ),
                "batch_summaries": batch_summaries,
                "frames": combined_frames,
                "visual_caveats": combined_caveats,
            }
        evidence = {
            "evidence_version": VISUAL_EVIDENCE_VERSION,
            "prompt_version": VISUAL_PROMPT_VERSION,
            "provider": self.identity(),
            "inference": {
                "batch_count": len(batch_analyses),
                "requested_batch_count": len(batches),
                "request_count": request_count,
                "adaptive_split_count": adaptive_split_count,
                "single_frame_retry_count": single_frame_retry_count,
                "max_frames_per_request": self.max_frames_per_request,
            },
            **frame_manifest,
            "analysis": analysis,
        }
        evidence["visual_evidence_hash"] = stable_sha256(evidence)
        return evidence

    def _terminate(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    def close(self) -> None:
        if self._closed:
            return
        process = self.process
        if process is not None and process.poll() is None:
            with self._lock:
                try:
                    self._write({"action": "close"})
                    self._read(min(self.timeout, 30))
                except VisualAnalysisError:
                    pass
        self._terminate()
        if process is not None:
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
        self.process = None
        self._closed = True


__all__ = [
    "DEFAULT_MAX_FRAME_GAP_SECONDS",
    "DEFAULT_MAX_FRAMES_PER_REQUEST",
    "MIMO_VL_REQUIRED_VERSION",
    "MimoVLVisualAnalyzer",
    "VISUAL_EVIDENCE_VERSION",
    "VISUAL_PROMPT_VERSION",
    "VisualAnalysisError",
    "VisualDownloadResult",
    "build_frame_manifest",
    "build_visual_prompt",
    "deterministic_sample_timestamps",
    "download_visual_video",
    "observed_max_gap",
    "resolve_mimo_vl_root",
    "validate_visual_result",
    "verify_mimo_vl_installation",
    "video_path_from_result",
    "visual_download_debug",
]
