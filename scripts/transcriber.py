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
DEFAULT_WHISPER_MODEL = "base"
DEFAULT_MLX_AUDIO_MODEL = os.environ.get("WATCHBRIEF_MLX_AUDIO_MODEL", "mlx-community/whisper-large-v3-turbo")
DEFAULT_PROJECT_ROOT = Path("/Users/apple/Documents/New project/watchbrief_v5")
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
    skill_root = Path(__file__).resolve().parents[1]
    project_root = Path(os.environ.get("WATCHBRIEF_PROJECT_ROOT") or DEFAULT_PROJECT_ROOT).expanduser()
    candidates.append(project_root / ".venv-mlx" / "bin" / "python")
    candidates.append(skill_root / ".venv-mlx" / "bin" / "python")
    candidates.append(skill_root.parent / ".venv-mlx" / "bin" / "python")
    candidates.append(Path.home() / ".hermes" / "skills" / "openclaw-imports" / "watchbrief_v5" / ".venv-mlx" / "bin" / "python")
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


def check_mlx_audio_python(candidate: Path, *, runner: Any = subprocess.run, timeout: int = 10) -> tuple[bool, str]:
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


def mlx_audio_python_statuses(*, runner: Any = subprocess.run, timeout: int = 10) -> list[dict[str, str]]:
    statuses: list[dict[str, str]] = []
    for candidate in mlx_audio_python_candidates():
        available, status = check_mlx_audio_python(candidate, runner=runner, timeout=timeout)
        statuses.append({
            "python": str(candidate),
            "status": status,
            "available": str(available).lower(),
        })
    return statuses


def resolve_mlx_audio_python(*, runner: Any = subprocess.run, timeout: int = 10) -> Optional[Path]:
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


def has_mlx_audio(runner: Any = subprocess.run, timeout: int = 10) -> bool:
    return resolve_mlx_audio_python(runner=runner, timeout=timeout) is not None


def mlx_audio_unavailable_message(*, runner: Any = subprocess.run, timeout: int = 10) -> str:
    details = "; ".join(
        f"{row['python']} [{row['status']}]"
        for row in mlx_audio_python_statuses(runner=runner, timeout=timeout)
    )
    return (
        "MLX-Audio unavailable. Checked Python candidates: "
        f"{details}. Set WATCHBRIEF_MLX_AUDIO_PYTHON or create .venv-mlx with mlx-audio."
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
        return load_transcript_source(transcript_path, language=language, transcript_quality=transcript_quality)
    except TranscriptSourceError as exc:
        raise FormatConversionError("transcriber", f"transcript JSON conversion failed: {exc}", command, stderr) from exc


def transcribe_audio_to_material(
    audio_path: Path,
    output_dir: Path,
    *,
    language: str,
    transcript_quality: str = "degraded",
    provider: str = "auto",
    model: str = DEFAULT_MLX_AUDIO_MODEL,
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
            transcript_path, command = run_mlx_audio(
                audio_path,
                output_dir,
                language=language,
                model=model,
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
    return TranscriptionResult(transcript_path=transcript_path, material=material, command=command, provider=selected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe standard audio into WatchBrief V5 transcript material.")
    parser.add_argument("--audio", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--language", default="unknown")
    parser.add_argument("--transcript-quality", default="degraded")
    parser.add_argument("--transcriber", choices=SUPPORTED_TRANSCRIBERS, default="auto")
    parser.add_argument("--mlx-model", default=DEFAULT_MLX_AUDIO_MODEL)
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
        whisper_model=args.whisper_model,
        allow_whisper_fallback=args.allow_whisper_fallback,
    )
    args.material_output.parent.mkdir(parents=True, exist_ok=True)
    args.material_output.write_text(json.dumps(result.material, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
