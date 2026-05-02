"""Non-Qwen extraction adapters for WatchBrief V5."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from .codex_review import call_codex_cli
    from .local_extract import (
        LocalQwenError,
        QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
        build_local_extract_payload,
        build_qwen_chunk_extract_user_prompt,
        build_qwen_chunk_reduce_user_prompt,
        build_qwen_local_extract_user_prompt,
        parse_qwen_message_json,
    )
    from .model_clients import ModelClientError, call_named_provider_text
except ImportError:  # pragma: no cover
    from codex_review import call_codex_cli
    from local_extract import (
        LocalQwenError,
        QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
        build_local_extract_payload,
        build_qwen_chunk_extract_user_prompt,
        build_qwen_chunk_reduce_user_prompt,
        build_qwen_local_extract_user_prompt,
        parse_qwen_message_json,
    )
    from model_clients import ModelClientError, call_named_provider_text


DEFAULT_EXTRACT_MODELS = {
    "local-openai-compatible": "",
    "openai-compatible": "",
    "gemini": "gemini-2.5-flash",
    "claude": "claude-sonnet-4-20250514",
    "kimi": "kimi-k2.5",
    "codex-cli": "gpt-5.5",
    "codex-cli-extract": "gpt-5.5",
}


def prompt_for_seed(seed: dict[str, Any]) -> str:
    mode = str(seed.get("local_extract_mode") or "")
    if mode == "chunk":
        return build_qwen_chunk_extract_user_prompt(seed)
    if mode == "chunk_reduce":
        return build_qwen_chunk_reduce_user_prompt(seed)
    return build_qwen_local_extract_user_prompt(seed)


def provider_model_id(provider: str, model: str) -> str:
    return f"{provider}:{model or DEFAULT_EXTRACT_MODELS.get(provider, '')}".rstrip(":")


def call_codex_extract_text(
    *,
    system_prompt: str,
    user_prompt: str,
    model: str,
    codex_home: str | None = None,
    codex_home_root: str | None = None,
    codex_account: str | None = None,
    timeout: int = 120,
) -> str:
    prompt = (
        f"{system_prompt}\n\n"
        f"{user_prompt}\n\n"
        "Output exactly one JSON object for the requested intermediate extraction schema. "
        "Do not output Markdown, explanations, or code fences."
    )
    return call_codex_cli(
        {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "_prompt_override": prompt,
        },
        model=model or "gpt-5.5",
        timeout=timeout,
        codex_home=codex_home,
        codex_home_root=codex_home_root,
        codex_account=codex_account,
    )


def build_external_extract_payload(
    metadata: dict[str, Any],
    transcript_segments: list[dict[str, Any]],
    *,
    transcript_language: str = "",
    transcript_quality: str = "ok",
    transcript_source: str = "mock",
    provider: str,
    model: str = "",
    api_base: str = "",
    api_key_env: str = "",
    timeout: int = 120,
    debug_dir: Optional[Path] = None,
    urlopen_func: Any = None,
    text_model_caller: Optional[Callable[..., str]] = None,
    codex_home: str | None = None,
    codex_home_root: str | None = None,
    codex_account: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    normalized_provider = "codex-cli" if provider == "codex-cli-extract" else str(provider or "").strip()
    selected_model = model or DEFAULT_EXTRACT_MODELS.get(normalized_provider, "")
    if normalized_provider in {"local-openai-compatible", "openai-compatible"} and not selected_model:
        raise LocalQwenError("external_extract_model_missing", f"{normalized_provider} requires --extract-model")

    def external_extractor(seed: dict[str, Any]) -> dict[str, Any]:
        user_prompt = prompt_for_seed(seed)
        try:
            if normalized_provider == "codex-cli":
                raw_text = call_codex_extract_text(
                    system_prompt=QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    model=selected_model,
                    codex_home=codex_home,
                    codex_home_root=codex_home_root,
                    codex_account=codex_account,
                    timeout=timeout,
                )
            else:
                caller = text_model_caller or call_named_provider_text
                call_kwargs = {
                    "provider": normalized_provider,
                    "model": selected_model,
                    "system_prompt": QWEN_LOCAL_EXTRACT_SYSTEM_PROMPT,
                    "user_prompt": user_prompt,
                    "api_base": api_base,
                    "api_key_env": api_key_env,
                    "timeout": timeout,
                }
                if urlopen_func is not None:
                    call_kwargs["urlopen_func"] = urlopen_func
                raw_text = caller(**call_kwargs)
        except ModelClientError as exc:
            raise LocalQwenError(f"{normalized_provider}_extract_failed", exc.message) from exc
        parsed = parse_qwen_message_json(raw_text)
        parsed["_qwen_model"] = provider_model_id(normalized_provider, selected_model)
        return parsed

    payload = build_local_extract_payload(
        metadata,
        transcript_segments,
        transcript_language=transcript_language,
        transcript_quality=transcript_quality,
        transcript_source=transcript_source,
        qwen_extractor=external_extractor,
        qwen_model=provider_model_id(normalized_provider, selected_model),
        qwen_timeout=timeout,
        debug_dir=debug_dir,
        **kwargs,
    )
    payload = copy.deepcopy(payload)
    payload["extract_version"] = "watchbrief_v5.external_extract.v1"
    payload["analysis_boundary"]["local_qwen_model_call"] = False
    payload["analysis_boundary"]["qwen_family_only"] = False
    payload["analysis_boundary"]["external_extract_provider"] = normalized_provider
    payload["analysis_boundary"]["external_model_id"] = selected_model
    payload["analysis_boundary"]["external_api_base"] = api_base
    return payload
