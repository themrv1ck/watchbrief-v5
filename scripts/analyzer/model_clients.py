"""Small HTTP clients for WatchBrief model adapters.

The functions here never persist API keys. They only read keys from environment
variables and send them in request headers.
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class ModelClientError(RuntimeError):
    def __init__(self, provider: str, reason_code: str, message: str) -> None:
        self.provider = provider
        self.reason_code = reason_code
        self.message = message
        super().__init__(f"{provider}:{reason_code}: {message}")


def env_api_key(env_name: str, *, provider: str, required: bool = True) -> str:
    if not env_name:
        return ""
    value = str(os.environ.get(env_name) or "").strip()
    if required and not value:
        raise ModelClientError(provider, "api_key_missing", f"missing environment variable: {env_name}")
    return value


def post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: Optional[dict[str, str]] = None,
    provider: str,
    timeout: int = 120,
    urlopen_func: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urlopen_func(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except (TimeoutError, socket.timeout) as exc:
        raise ModelClientError(provider, "timeout", f"request timed out after {timeout}s") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
        raise ModelClientError(provider, "http_error", f"HTTP {exc.code}: {detail[:800]}") from exc
    except urllib.error.URLError as exc:
        raise ModelClientError(provider, "unavailable", str(exc)) from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelClientError(provider, "invalid_json", "model endpoint returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelClientError(provider, "invalid_json", "model endpoint returned non-object JSON")
    return parsed


def openai_chat_url(api_base: str) -> str:
    base = str(api_base or "").rstrip("/")
    if not base:
        raise ModelClientError("openai-compatible", "api_base_missing", "api base is required")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def extract_openai_message_text(data: dict[str, Any], *, provider: str) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelClientError(provider, "empty_response", "chat response has no choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    raise ModelClientError(provider, "empty_response", "chat response has no message content")


def call_openai_compatible_text(
    *,
    provider: str,
    model: str,
    api_base: str,
    system_prompt: str,
    user_prompt: str,
    api_key_env: str = "",
    api_key_required: bool = False,
    timeout: int = 120,
    urlopen_func: Any = urllib.request.urlopen,
) -> str:
    key = env_api_key(api_key_env, provider=provider, required=api_key_required)
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = post_json(
        openai_chat_url(api_base),
        provider=provider,
        timeout=timeout,
        urlopen_func=urlopen_func,
        headers=headers,
        payload={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "stream": False,
        },
    )
    return extract_openai_message_text(data, provider=provider)


def call_gemini_text(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str = "https://generativelanguage.googleapis.com/v1beta",
    api_key_env: str = "GEMINI_API_KEY",
    timeout: int = 120,
    urlopen_func: Any = urllib.request.urlopen,
) -> str:
    key = env_api_key(api_key_env, provider="gemini", required=True)
    quoted_model = urllib.parse.quote(str(model or "gemini-2.5-flash"), safe="-_.:")
    data = post_json(
        f"{api_base.rstrip('/')}/models/{quoted_model}:generateContent",
        provider="gemini",
        timeout=timeout,
        urlopen_func=urlopen_func,
        headers={"x-goog-api-key": key},
        payload={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0},
        },
    )
    chunks: list[str] = []
    candidates = data.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else {}
            parts = content.get("parts") if isinstance(content, dict) else []
            if isinstance(parts, list):
                for part in parts:
                    text = part.get("text") if isinstance(part, dict) else None
                    if isinstance(text, str):
                        chunks.append(text)
    text = "".join(chunks).strip()
    if not text:
        raise ModelClientError("gemini", "empty_response", "Gemini response has no text")
    return text


def call_claude_text(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str = "https://api.anthropic.com/v1",
    api_key_env: str = "ANTHROPIC_API_KEY",
    timeout: int = 120,
    urlopen_func: Any = urllib.request.urlopen,
) -> str:
    key = env_api_key(api_key_env, provider="claude", required=True)
    data = post_json(
        f"{api_base.rstrip('/')}/messages",
        provider="claude",
        timeout=timeout,
        urlopen_func=urlopen_func,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        payload={
            "model": model or "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
    )
    chunks: list[str] = []
    content = data.get("content")
    if isinstance(content, list):
        for part in content:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str):
                chunks.append(text)
    text = "".join(chunks).strip()
    if not text:
        raise ModelClientError("claude", "empty_response", "Claude response has no text")
    return text


def call_named_provider_text(
    *,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    api_base: str = "",
    api_key_env: str = "",
    timeout: int = 120,
    urlopen_func: Any = urllib.request.urlopen,
) -> str:
    normalized = str(provider or "").strip()
    if normalized == "gemini":
        return call_gemini_text(
            model=model or "gemini-2.5-flash",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_base=api_base or "https://generativelanguage.googleapis.com/v1beta",
            api_key_env=api_key_env or "GEMINI_API_KEY",
            timeout=timeout,
            urlopen_func=urlopen_func,
        )
    if normalized == "claude":
        return call_claude_text(
            model=model or "claude-sonnet-4-20250514",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_base=api_base or "https://api.anthropic.com/v1",
            api_key_env=api_key_env or "ANTHROPIC_API_KEY",
            timeout=timeout,
            urlopen_func=urlopen_func,
        )
    if normalized == "kimi":
        return call_openai_compatible_text(
            provider="kimi",
            model=model or "kimi-k2.5",
            api_base=api_base or "https://api.moonshot.ai/v1",
            api_key_env=api_key_env or "MOONSHOT_API_KEY",
            api_key_required=True,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
            urlopen_func=urlopen_func,
        )
    if normalized in {"openai-compatible", "local-openai-compatible"}:
        return call_openai_compatible_text(
            provider=normalized,
            model=model,
            api_base=api_base,
            api_key_env=api_key_env,
            api_key_required=normalized == "openai-compatible" and bool(api_key_env),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            timeout=timeout,
            urlopen_func=urlopen_func,
        )
    raise ModelClientError(normalized or "unknown", "unsupported_provider", f"unsupported model provider: {provider}")
