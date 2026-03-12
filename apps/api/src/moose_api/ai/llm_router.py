"""LLM provider routing and management with quota handling.

Provides intelligent failover between multiple LLM providers (Google AI Studio,
OpenRouter) with automatic quota exhaustion detection and fallback strategies.
Implements retry logic and cost optimization for reliable AI text generation.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from moose_api.core.config import settings

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class BatchQuotaState:
    """Tracks quota exhaustion state across batch processing.

    Prevents repeated failed API calls when quotas are exhausted by
    maintaining in-memory state of provider availability during batch jobs.
    """

    def __init__(self):
        self.gemini_3_1_lite_exhausted = False


batch_quota = BatchQuotaState()


def reset_batch_quota_state():
    """Reset quota exhaustion flags for new batch processing.

    Called at the start of new batch jobs to ensure fresh quota state
    and allow retry of previously exhausted providers.
    """
    batch_quota.gemini_3_1_lite_exhausted = False


def is_daily_quota_exhausted(e: Exception) -> bool:
    """Detect if API error indicates daily quota exhaustion.

    Parses error messages to distinguish between daily quota limits,
    per-minute rate limits, and other API errors. Critical for
    implementing intelligent fallback strategies.

    Args:
        e: Exception from LLM API call

    Returns:
        True if error indicates daily quota is exhausted, False otherwise
    """
    msg = str(e).lower()
    has_daily = "perday" in msg or "per_day" in msg or "requests_per_day" in msg or "_per_model_per_day" in msg
    # Fully exhausted (limit: 0) is a definitive quota exhaustion
    is_fully_exhausted = "limit: 0" in msg and "resource_exhausted" in msg
    # Per-minute limits should not trigger fallback (just retry)
    is_per_minute = "perminute" in msg or "per_minute" in msg

    if is_per_minute:
        return False

    return has_daily or is_fully_exhausted


class LLMResponse:
    """Standardized response format for all LLM providers.

    Normalizes different provider response formats into a common
    interface for cost tracking and usage analytics.
    """

    def __init__(
        self,
        content: str,
        provider: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ):
        self.content = content
        self.provider = provider
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LLMError(Exception):
    """Custom exception for LLM provider errors.

    Wraps provider-specific errors with consistent handling for
    retry logic and fallback strategies.
    """


async def _call_gemini(
    prompt: str, system_prompt: str = "", model: str = "gemini-3.1-flash-lite-preview"
) -> LLMResponse:
    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
    url = GEMINI_API_BASE.format(model=model)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{url}?key={settings.google_gemini_api_key}",
            json={
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {
                    "temperature": 0.8,
                    "maxOutputTokens": 2048,
                },
            },
        )

    if resp.status_code != 200:
        if resp.status_code == 429:
            raise LLMError(f"RESOURCE_EXHAUSTED 429: {resp.text[:300]}")
        raise LLMError(f"Gemini API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise LLMError("Gemini returned no candidates")

    content = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    usage = data.get("usageMetadata", {})

    return LLMResponse(
        content=content,
        provider="google_ai_studio",
        model=model,
        input_tokens=usage.get("promptTokenCount", 0),
        output_tokens=usage.get("candidatesTokenCount", 0),
    )


async def _call_openrouter(prompt: str, system_prompt: str = "") -> LLMResponse:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-3.1-flash-lite-preview",
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 2048,
            },
        )

    if resp.status_code != 200:
        raise LLMError(f"OpenRouter API error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    choices = data.get("choices", [])
    if not choices:
        raise LLMError("OpenRouter returned no choices")

    content = choices[0].get("message", {}).get("content", "")
    usage = data.get("usage", {})

    return LLMResponse(
        content=content,
        provider="openrouter",
        model="google/gemini-3.1-flash-lite-preview",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


async def generate_text(prompt: str, system_prompt: str = "") -> LLMResponse:
    last_error = None

    if not batch_quota.gemini_3_1_lite_exhausted:
        for attempt in range(2):
            try:
                logger.info("Attempting LLM generation with gemini-3.1-flash-lite-preview...")
                return await _call_gemini(prompt, system_prompt, model="gemini-3.1-flash-lite-preview")
            except LLMError as e:
                last_error = e
                if is_daily_quota_exhausted(e) or str(e).find("RESOURCE_EXHAUSTED") != -1:
                    batch_quota.gemini_3_1_lite_exhausted = True
                    logger.warning("gemini-3.1-flash-lite-preview quota exhausted. Skipping for rest of batch.")
                    break
                logger.warning("gemini-3.1-flash-lite-preview attempt %d failed: %s", attempt + 1, e)
                if attempt < 1:
                    await asyncio.sleep(2**attempt)

    logger.warning("Gemini tier exhausted or failed. Falling back to OpenRouter.")
    try:
        return await _call_openrouter(prompt, system_prompt)
    except LLMError as e:
        logger.error("OpenRouter fallback also failed: %s", e)
        raise LLMError(f"All LLM providers failed. Last Gemini error: {last_error}. OpenRouter error: {e}") from e
