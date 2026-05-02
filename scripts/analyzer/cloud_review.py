"""Cloud and OpenAI-compatible review adapters for WatchBrief V5."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

try:
    from .codex_review import CODEX_CLI_JSON_CONTRACT, build_codex_cli_prompt, build_review_request, write_debug_text
    from .model_clients import ModelClientError, call_named_provider_text
except ImportError:  # pragma: no cover
    from codex_review import CODEX_CLI_JSON_CONTRACT, build_codex_cli_prompt, build_review_request, write_debug_text
    from model_clients import ModelClientError, call_named_provider_text


DEFAULT_REVIEW_MODELS = {
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-20250514",
    "kimi": "kimi-k2.5",
    "openai-compatible": "",
}


class CloudReviewError(RuntimeError):
    stage = "codex_review"

    def __init__(self, provider: str, reason_code: str, message: str) -> None:
        self.provider = provider
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{provider}:{reason_code}: {message}")


def run_cloud_review(
    local_extract_payload: dict[str, Any],
    *,
    review_provider: str,
    model: str = "",
    api_base: str = "",
    api_key_env: str = "",
    timeout: int = 120,
    debug_dir: Optional[Path] = None,
    text_model_caller: Any = call_named_provider_text,
) -> str:
    provider = str(review_provider or "").strip()
    selected_model = model or DEFAULT_REVIEW_MODELS.get(provider, "")
    if provider == "openai-compatible" and not selected_model:
        raise CloudReviewError(provider, "review_model_missing", "--review-model is required for openai-compatible")

    review_request = build_review_request(local_extract_payload)
    user_prompt = build_codex_cli_prompt(review_request)
    system_prompt = (
        "You are a WatchBrief V5 review adapter. Return exactly one JSON object. "
        "Do not output Markdown, explanations, or code fences.\n\n"
        f"{CODEX_CLI_JSON_CONTRACT}"
    )
    try:
        raw = text_model_caller(
            provider=provider,
            model=selected_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_base=api_base,
            api_key_env=api_key_env,
            timeout=timeout,
        )
    except ModelClientError as exc:
        raise CloudReviewError(provider, exc.reason_code, exc.message) from exc
    write_debug_text(debug_dir, f"{provider}_review_raw_response.txt", raw)
    return raw
