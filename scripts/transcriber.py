#!/usr/bin/env python3
"""Audio transcription wrapper for WatchBrief V5 acquisition layer."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .acquisition_errors import FormatConversionError, TranscriberUnavailableError, TranscriptionError
    from .transcript_source_adapter import TranscriptSourceError, load_transcript_source
except ImportError:  # pragma: no cover
    from acquisition_errors import FormatConversionError, TranscriberUnavailableError, TranscriptionError
    from transcript_source_adapter import TranscriptSourceError, load_transcript_source


SUPPORTED_TRANSCRIBERS = ("auto", "mlx_audio", "whisper")
MLX_AUDIO_CHUNK_SECONDS = 90
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_PROJECT_ROOT = Path("/Users/apple/Documents/New project/watchbrief_v5")
DEFAULT_MIMO_MLX_ROOT = Path(os.environ.get("WATCHBRIEF_MIMO_MLX_ROOT", "/Users/apple/Documents/MiMo-V2.5-ASR-MLX")).expanduser()
DEFAULT_MLX_AUDIO_MODEL = os.environ.get("WATCHBRIEF_MLX_AUDIO_MODEL", str(DEFAULT_MIMO_MLX_ROOT / "models" / "MiMo-V2.5-ASR-MLX"))
DEFAULT_MLX_AUDIO_TOKENIZER_DIR = os.environ.get(
    "WATCHBRIEF_MLX_AUDIO_TOKENIZER_DIR",
    str(DEFAULT_MIMO_MLX_ROOT / "models" / "MiMo-Audio-Tokenizer"),
)
MLX_AUDIO_RUNNER_SCRIPT = """
import json
import sys
from mlx_audio.stt.generate import generate_transcription

payload = json.loads(sys.argv[1])
generate_transcription(**payload)
""".strip()


@dataclass
class TranscriptionResult:
    transcript_path: Path
    material: dict[str, Any]
    command: list[str]
    provider: str


def expected_transcript_json_path(audio_path: Path, output_dir: Path) -> Path:
    return output_dir / f"{audio_path.stem}.json"


def mlx_audio_python_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = str(os.environ.get("WATCHBRIEF_MLX_AUDIO_PYTHON") or "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())
    candidates.append(DEFAULT_MIMO_MLX_ROOT / ".venv" / "bin" / "python")
    candidates.append(Path(sys.executable))

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


# Importing MLX can briefly block while macOS reclaims GPU resources.  This is a
# readiness probe, not the transcription deadline, so keep it comfortably above
# launchd scheduling jitter instead of misclassifying a healthy runtime.
MLX_AUDIO_PROBE_TIMEOUT_SECONDS = 60


def check_mlx_audio_python(candidate: Path, *, runner: Any = subprocess.run, timeout: int = MLX_AUDIO_PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
    if not candidate.exists() or not candidate.is_file():
        return False, "missing"
    result = runner(
        [str(candidate), "-c", "import mlx_audio"],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode == 0:
        return True, "ok"
    detail = " ".join(str(result.stderr or result.stdout or "").split())
    return False, f"import_failed: {detail[:240]}" if detail else "import_failed"


def mlx_audio_python_statuses(*, runner: Any = subprocess.run, timeout: int = MLX_AUDIO_PROBE_TIMEOUT_SECONDS) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    for candidate in mlx_audio_python_candidates():
        available, status = check_mlx_audio_python(candidate, runner=runner, timeout=timeout)
        statuses.append({
            "python": str(candidate),
            "status": status,
            "available": str(available).lower(),
        })
    return statuses


def resolve_mlx_audio_python(*, runner: Any = subprocess.run, timeout: int = MLX_AUDIO_PROBE_TIMEOUT_SECONDS) -> Optional[Path]:
    for candidate in mlx_audio_python_candidates():
        available, _status = check_mlx_audio_python(candidate, runner=runner, timeout=timeout)
        if available:
            return candidate
    return None


def resolve_existing_mlx_audio_python() -> Optional[Path]:
    for candidate in mlx_audio_python_candidates():
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def has_mlx_audio(runner: Any = subprocess.run, timeout: int = MLX_AUDIO_PROBE_TIMEOUT_SECONDS) -> bool:
    return resolve_mlx_audio_python(runner=runner, timeout=timeout) is not None


def mlx_audio_unavailable_message(*, runner: Any = subprocess.run, timeout: int = MLX_AUDIO_PROBE_TIMEOUT_SECONDS) -> str:
    details = "; ".join(
        f"{row['python']} [{row['status']}]"
        for row in mlx_audio_python_statuses(runner=runner, timeout=timeout)
    )
    return (
        "MiMo MLX unavailable. Checked Python candidates: "
        f"{details}. Set WATCHBRIEF_MLX_AUDIO_PYTHON or install the local MiMo MLX runtime."
    )


def has_whisper_cli() -> bool:
    return shutil.which("whisper") is not None


def choose_transcriber(
    provider: str,
    *,
    allow_whisper_fallback: bool,
    mlx_available: bool,
    mlx_unavailable_message: str = "MLX-Audio missing",
) -> str:
    if provider not in SUPPORTED_TRANSCRIBERS:
        raise TranscriptionError(f"unsupported transcriber provider: {provider}")
    if provider == "whisper":
        return "whisper"
    if provider == "mlx_audio":
        if not mlx_available:
            raise TranscriberUnavailableError(mlx_unavailable_message)
        return "mlx_audio"
    if mlx_available:
        return "mlx_audio"
    if allow_whisper_fallback:
        return "whisper"
    raise TranscriberUnavailableError(mlx_unavailable_message)


def run_whisper(
    audio_path: Path,
    output_dir: Path,
    *,
    language: str,
    model: str,
    runner: Any,
    timeout: int,
) -> tuple[Path, list[str]]:
    command = [
        "whisper",
        str(audio_path),
        "--model",
        model,
        "--output_format",
        "json",
        "--output_dir",
        str(output_dir),
        "--task",
        "transcribe",
    ]
    if language and language != "unknown":
        command.extend(["--language", language])
    result = runner(command, capture_output=True, text=True, timeout=timeout)
    stderr = str(result.stderr or "")
    if result.returncode != 0:
        raise TranscriptionError("whisper transcriber command failed", command, stderr)
    transcript_path = expected_transcript_json_path(audio_path, output_dir)
    if not transcript_path.exists() or transcript_path.stat().st_size == 0:
        raise TranscriptionError("whisper completed without timestamp JSON", command, stderr)
    return transcript_path, command


def run_mlx_audio(
    audio_path: Path,
    output_dir: Path,
    *,
    language: str,
    model: str,
    audio_tokenizer_dir: str,
    runner: Any,
    timeout: int,
) -> tuple[Path, list[str]]:
    python_path = resolve_mlx_audio_python(runner=runner, timeout=min(timeout, 10)) or resolve_existing_mlx_audio_python()
    if python_path is None:
        raise TranscriberUnavailableError(mlx_audio_unavailable_message(runner=runner, timeout=min(timeout, 10)))
    transcript_path = expected_transcript_json_path(audio_path, output_dir)
    output_prefix = output_dir / audio_path.stem
    payload = {
        "model": model,
        "audio": str(audio_path),
        "output_path": str(output_prefix),
        "format": "json",
    }
    tokenizer_dir = str(audio_tokenizer_dir or "").strip()
    if tokenizer_dir:
        payload["audio_tokenizer_dir"] = tokenizer_dir
    if language and language != "unknown":
        payload["language"] = language
    command = [
        str(python_path),
        "-c",
        MLX_AUDIO_RUNNER_SCRIPT,
        json.dumps(payload, ensure_ascii=False),
    ]
    env = os.environ.copy()
    env["HF_HUB_DISABLE_XET"] = "1"
    result = runner(command, capture_output=True, text=True, timeout=timeout, env=env)
    stderr = str(result.stderr or "")
    if result.returncode != 0:
        raise TranscriptionError("MLX-Audio transcriber command failed", command, stderr)
    if not transcript_path.exists() or transcript_path.stat().st_size == 0:
        raise TranscriptionError("MLX-Audio completed without timestamp JSON", command, stderr)
    return transcript_path, command


def load_material_from_transcript(
    transcript_path: Path,
    *,
    language: str,
    transcript_quality: str,
    command: list[str],
    stderr: str = "",
) -> dict[str, Any]:
    try:
        raw_payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raw_payload = None
    try:
        return load_transcript_source(transcript_path, language=language, transcript_quality=transcript_quality)
    except TranscriptSourceError as exc:
        raise FormatConversionError("transcriber", f"transcript JSON conversion failed: {exc}", command, stderr) from exc


def probe_audio_duration_seconds(audio_path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(str(result.stdout or "").strip()))
    except ValueError:
        return 0.0


def split_audio_for_mlx(audio_path: Path, chunk_dir: Path, *, chunk_seconds: int = MLX_AUDIO_CHUNK_SECONDS) -> list[Path]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    # The segment muxer treats every percent sign in the full output path as
    # part of its filename template. Escape literal percent signs from video
    # titles while preserving the one numeric chunk placeholder.
    escaped_chunk_dir = str(chunk_dir).replace("%", "%%")
    pattern = f"{escaped_chunk_dir}/chunk-%04d.wav"
    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(audio_path),
            "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
            "-ar", "16000", "-ac", "1", pattern,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        raise TranscriptionError("failed to split long audio for MiMo", result.args, str(result.stderr or ""))
    chunks = sorted(chunk_dir.glob("chunk-*.wav"))
    if not chunks:
        raise TranscriptionError("long audio split produced no chunks", result.args, str(result.stderr or ""))
    return chunks


def run_chunked_mlx_audio(
    audio_path: Path,
    output_dir: Path,
    *,
    language: str,
    model: str,
    audio_tokenizer_dir: str,
    runner: Any,
    timeout: int,
) -> tuple[Path, list[str]]:
    chunk_root = output_dir / f".{audio_path.stem}-mlx-chunks"
    transcript_root = chunk_root / "transcripts"
    chunks = split_audio_for_mlx(audio_path, chunk_root / "audio")
    combined_segments: list[dict[str, Any]] = []
    combined_text: list[str] = []
    commands: list[list[str]] = []
    offset = 0.0
    for chunk in chunks:
        chunk_duration = probe_audio_duration_seconds(chunk)
        transcript_path, command = run_mlx_audio(
            chunk,
            transcript_root,
            language=language,
            model=model,
            audio_tokenizer_dir=audio_tokenizer_dir,
            runner=runner,
            timeout=timeout,
        )
        commands.append(command)
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))
        raw_segments = payload.get("segments") if isinstance(payload, dict) else []
        if isinstance(raw_segments, list):
            for segment in raw_segments:
                if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
                    continue
                start = float(segment.get("start") or 0.0) + offset
                end = float(segment.get("end") or chunk_duration) + offset
                text_value = str(segment.get("text") or "").strip()
                combined_segments.append({"start": start, "end": end, "text": text_value})
                combined_text.append(text_value)
        offset += chunk_duration
    combined_path = expected_transcript_json_path(audio_path, output_dir)
    combined_path.write_text(
        json.dumps({"text": "\n".join(combined_text), "segments": combined_segments}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.rmtree(chunk_root, ignore_errors=True)
    return combined_path, ["mlx_audio_chunked", f"chunks={len(chunks)}", f"chunk_seconds={MLX_AUDIO_CHUNK_SECONDS}"]


def transcribe_audio_to_material(
    audio_path: Path,
    output_dir: Path,
    *,
    language: str,
    transcript_quality: str = "degraded",
    provider: str = "auto",
    model: str = DEFAULT_MLX_AUDIO_MODEL,
    audio_tokenizer_dir: str = DEFAULT_MLX_AUDIO_TOKENIZER_DIR,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    allow_whisper_fallback: bool = False,
    runner: Any = subprocess.run,
    timeout: int = 1800,
    mlx_available_func: Callable[[], bool] | None = None,
) -> TranscriptionResult:
    if not audio_path.exists() or not audio_path.is_file():
        raise TranscriptionError(f"audio file not found: {audio_path}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if mlx_available_func is not None:
        mlx_available = mlx_available_func()
        mlx_unavailable_message = "MLX-Audio missing"
    else:
        mlx_probe_timeout = min(timeout, 10)
        mlx_available = has_mlx_audio(runner=runner, timeout=mlx_probe_timeout)
        mlx_unavailable_message = "" if mlx_available else mlx_audio_unavailable_message(runner=runner, timeout=mlx_probe_timeout)
    selected = choose_transcriber(
        provider,
        allow_whisper_fallback=allow_whisper_fallback,
        mlx_available=mlx_available,
        mlx_unavailable_message=mlx_unavailable_message or "MLX-Audio missing",
    )

    try:
        if selected == "mlx_audio":
            if probe_audio_duration_seconds(audio_path) > MLX_AUDIO_CHUNK_SECONDS:
                transcript_path, command = run_chunked_mlx_audio(
                    audio_path, output_dir, language=language, model=model,
                    audio_tokenizer_dir=audio_tokenizer_dir, runner=runner, timeout=timeout,
                )
            else:
                transcript_path, command = run_mlx_audio(
                    audio_path,
                    output_dir,
                    language=language,
                    model=model,
                    audio_tokenizer_dir=audio_tokenizer_dir,
                    runner=runner,
                    timeout=timeout,
                )
        else:
            transcript_path, command = run_whisper(
                audio_path,
                output_dir,
                language=language,
                model=whisper_model,
                runner=runner,
                timeout=timeout,
            )
    except TranscriptionError:
        if selected == "mlx_audio" and allow_whisper_fallback:
            transcript_path, command = run_whisper(
                audio_path,
                output_dir,
                language=language,
                model=whisper_model,
                runner=runner,
                timeout=timeout,
            )
            selected = "whisper"
        else:
            raise

    material = load_material_from_transcript(
        transcript_path,
        language=language,
        transcript_quality=transcript_quality,
        command=command,
    )
    material["source"]["transcriber"] = selected
    if selected == "mlx_audio":
        material["source"]["transcriber_model"] = model
        if audio_tokenizer_dir:
            material["source"]["audio_tokenizer_dir"] = audio_tokenizer_dir
    else:
        material["source"]["transcriber_model"] = whisper_model
    return TranscriptionResult(transcript_path=transcript_path, material=material, command=command, provider=selected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe standard audio into WatchBrief V5 transcript material.")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--language", default="unknown")
    parser.add_argument("--transcript-quality", default="degraded")
    parser.add_argument("--transcriber", choices=SUPPORTED_TRANSCRIBERS, default="auto")
    parser.add_argument("--mlx-model", default=DEFAULT_MLX_AUDIO_MODEL)
    parser.add_argument("--mlx-audio-tokenizer-dir", default=DEFAULT_MLX_AUDIO_TOKENIZER_DIR)
    parser.add_argument("--whisper-model", default=DEFAULT_WHISPER_MODEL)
    parser.add_argument("--allow-whisper-fallback", action="store_true")
    parser.add_argument("--material-output", required=True, type=Path)
    args = parser.parse_args()
    result = transcribe_audio_to_material(
        args.audio,
        args.output_dir,
        language=args.language,
        transcript_quality=args.transcript_quality,
        provider=args.transcriber,
        model=args.mlx_model,
        audio_tokenizer_dir=args.mlx_audio_tokenizer_dir,
        whisper_model=args.whisper_model,
        allow_whisper_fallback=args.allow_whisper_fallback,
    )
    args.material_output.parent.mkdir(parents=True, exist_ok=True)
    args.material_output.write_text(json.dumps(result.material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
