from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401 - ensures watchbrief_v5 is on sys.path before scripts imports
from helpers import ROOT
from scripts.acquisition_errors import FormatConversionError, TranscriberUnavailableError, TranscriptionError
from scripts import transcriber
from scripts.transcriber import transcribe_audio_to_material


class AcquisitionTranscriberTest(unittest.TestCase):
    def test_mlx_audio_candidates_include_project_root_venv_before_system_python(self) -> None:
        candidates = transcriber.mlx_audio_python_candidates()
        project_venv_python = ROOT / ".venv-mlx" / "bin" / "python"
        canonical_venv_python = Path("/Users/apple/Documents/New project/watchbrief_v5/.venv-mlx/bin/python")
        mimo_venv_python = Path("/Users/apple/Documents/MiMo-V2.5-ASR-MLX/.venv/bin/python")

        self.assertIn(mimo_venv_python, candidates)
        self.assertIn(project_venv_python, candidates)
        self.assertIn(canonical_venv_python, candidates)
        self.assertFalse(any("v1deodownload" in str(candidate) for candidate in candidates))
        self.assertLess(
            candidates.index(mimo_venv_python),
            candidates.index(project_venv_python),
        )
        self.assertLess(
            candidates.index(project_venv_python),
            candidates.index(Path(transcriber.sys.executable)),
        )

    def test_default_mlx_audio_model_is_local_mimo_4bit(self) -> None:
        self.assertEqual(
            transcriber.DEFAULT_MLX_AUDIO_MODEL,
            "/Users/apple/Documents/MiMo-V2.5-ASR-MLX/models/MiMo-V2.5-ASR-MLX",
        )
        self.assertEqual(
            transcriber.DEFAULT_MLX_AUDIO_TOKENIZER_DIR,
            "/Users/apple/Documents/MiMo-V2.5-ASR-MLX/models/MiMo-Audio-Tokenizer",
        )

    def test_resolve_mlx_audio_python_uses_candidate_that_can_import_mlx_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = root / "missing-python"
            import_fails = root / "import-fails"
            import_ok = root / "import-ok"
            import_fails.write_text("#!/bin/sh\n", encoding="utf-8")
            import_ok.write_text("#!/bin/sh\n", encoding="utf-8")

            def run(command, capture_output, text, timeout, **kwargs):
                if command[0] == str(import_ok):
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="No module named mlx_audio")

            with mock.patch("scripts.transcriber.mlx_audio_python_candidates", return_value=[missing, import_fails, import_ok]):
                self.assertEqual(transcriber.resolve_mlx_audio_python(runner=run), import_ok)

    def test_mlx_audio_unavailable_message_lists_checked_python_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            import_fails = root / "import-fails"
            import_fails.write_text("#!/bin/sh\n", encoding="utf-8")
            missing = root / "missing-python"

            def run(command, capture_output, text, timeout, **kwargs):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="No module named mlx_audio")

            with mock.patch("scripts.transcriber.mlx_audio_python_candidates", return_value=[missing, import_fails]):
                message = transcriber.mlx_audio_unavailable_message(runner=run)

        self.assertIn(str(missing), message)
        self.assertIn("[missing]", message)
        self.assertIn(str(import_fails), message)
        self.assertIn("No module named mlx_audio", message)

    def test_default_auto_unavailable_error_reports_python_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.wav"
            audio.write_bytes(b"RIFFmock")

            with mock.patch("scripts.transcriber.resolve_mlx_audio_python", return_value=None):
                with mock.patch(
                    "scripts.transcriber.mlx_audio_unavailable_message",
                    return_value="MLX-Audio unavailable. Checked Python candidates: /tmp/venv/bin/python [missing].",
                ):
                    with self.assertRaises(TranscriberUnavailableError) as context:
                        transcribe_audio_to_material(audio, Path(temp_dir) / "out", language="zh")

            self.assertIn("/tmp/venv/bin/python [missing]", context.exception.message)

    def test_transcribe_audio_to_material_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"RIFFmock")
            output_dir = root / "transcript"
            seen_payload: dict[str, object] = {}

            def run(command, capture_output, text, timeout, **kwargs):
                if command[1] == "-c" and command[2] == "import mlx_audio":
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                seen_payload.update(json.loads(command[3]))
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text(
                    json.dumps({
                        "language": "zh",
                        "segments": [
                            {"start": 0.0, "end": 2.0, "text": "第一句。"},
                            {"start": 2.0, "end": 4.0, "text": "第二句。"},
                        ],
                    }),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = transcribe_audio_to_material(audio, output_dir, language="zh", runner=run, mlx_available_func=lambda: True)

            self.assertEqual(seen_payload["model"], transcriber.DEFAULT_MLX_AUDIO_MODEL)
            self.assertEqual(seen_payload["audio_tokenizer_dir"], transcriber.DEFAULT_MLX_AUDIO_TOKENIZER_DIR)
            self.assertEqual(result.transcript_path.name, "sample.json")
            self.assertEqual(result.provider, "mlx_audio")
            self.assertEqual(result.material["source"]["transcriber_model"], transcriber.DEFAULT_MLX_AUDIO_MODEL)
            self.assertEqual(result.material["source"]["audio_tokenizer_dir"], transcriber.DEFAULT_MLX_AUDIO_TOKENIZER_DIR)
            self.assertEqual(result.material["material_version"], "watchbrief_v5.transcript_material.v1")
            self.assertEqual(result.material["segments"][0]["start"], "00:00")
            self.assertEqual(result.material["segments"][1]["end"], "00:04")

    def test_transcriber_command_failure_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.wav"
            audio.write_bytes(b"RIFFmock")

            def run(command, capture_output, text, timeout, **kwargs):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="whisper failed")

            with self.assertRaises(TranscriptionError) as context:
                transcribe_audio_to_material(
                    audio,
                    Path(temp_dir) / "out",
                    language="zh",
                    provider="whisper",
                    runner=run,
                    mlx_available_func=lambda: False,
                )
            self.assertEqual(context.exception.reason_code, "transcribe_failed")

    def test_missing_transcript_json_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.wav"
            audio.write_bytes(b"RIFFmock")

            def run(command, capture_output, text, timeout, **kwargs):
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with self.assertRaises(TranscriptionError):
                transcribe_audio_to_material(
                    audio,
                    Path(temp_dir) / "out",
                    language="zh",
                    provider="whisper",
                    runner=run,
                    mlx_available_func=lambda: False,
                )

    def test_invalid_transcript_json_conversion_is_classified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"RIFFmock")
            output_dir = root / "transcript"

            def run(command, capture_output, text, timeout, **kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            with self.assertRaises(FormatConversionError) as context:
                transcribe_audio_to_material(audio, output_dir, language="zh", provider="whisper", runner=run, mlx_available_func=lambda: False)
            self.assertEqual(context.exception.reason_code, "format_conversion_failed")

    def test_default_auto_requires_mlx_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            audio = Path(temp_dir) / "sample.wav"
            audio.write_bytes(b"RIFFmock")

            with self.assertRaises(TranscriberUnavailableError) as context:
                transcribe_audio_to_material(audio, Path(temp_dir) / "out", language="zh", mlx_available_func=lambda: False)
            self.assertEqual(context.exception.reason_code, "transcriber_unavailable")

    def test_explicit_whisper_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"RIFFmock")
            output_dir = root / "transcript"
            seen_command: list[str] = []

            def run(command, capture_output, text, timeout, **kwargs):
                seen_command.extend(command)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text(
                    json.dumps({"language": "en", "segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = transcribe_audio_to_material(
                audio,
                output_dir,
                language="en",
                provider="whisper",
                runner=run,
                mlx_available_func=lambda: False,
            )

            self.assertEqual(result.provider, "whisper")
            self.assertEqual(seen_command[0], "whisper")

    def test_allow_whisper_fallback_when_mlx_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"RIFFmock")
            output_dir = root / "transcript"

            def run(command, capture_output, text, timeout, **kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text(
                    json.dumps({"language": "en", "segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = transcribe_audio_to_material(
                audio,
                output_dir,
                language="en",
                allow_whisper_fallback=True,
                runner=run,
                mlx_available_func=lambda: False,
            )

            self.assertEqual(result.provider, "whisper")

    def test_allow_whisper_fallback_when_mlx_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            audio = root / "sample.wav"
            audio.write_bytes(b"RIFFmock")
            output_dir = root / "transcript"
            calls: list[str] = []

            def run(command, capture_output, text, timeout, **kwargs):
                if command[1] == "-c" and command[2] == "import mlx_audio":
                    return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
                if command[1] == "-c":
                    calls.append("mlx_audio")
                    return subprocess.CompletedProcess(command, 1, stdout="", stderr="mlx failed")
                calls.append("whisper")
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "sample.json").write_text(
                    json.dumps({"language": "en", "segments": [{"start": 0, "end": 1, "text": "hello"}]}),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

            result = transcribe_audio_to_material(
                audio,
                output_dir,
                language="en",
                allow_whisper_fallback=True,
                runner=run,
                mlx_available_func=lambda: True,
            )

            self.assertEqual(calls, ["mlx_audio", "whisper"])
            self.assertEqual(result.provider, "whisper")


if __name__ == "__main__":
    unittest.main()
