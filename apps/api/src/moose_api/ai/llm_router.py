"""LLM provider routing and management with quota handling.

Provides intelligent failover between multiple LLM providers (Google AI Studio,
OpenRouter) with automatic quota exhaustion detection and fallback strategies.
Implements retry logic and cost optimization for reliable AI text generation.

Model tier strategy (2026-07 audit):
- Primary: Gemini 3.5 Flash (stable frontier model) — ~10 RPM
- Fallback 1: Gemini 2.5 Flash (hybrid reasoning, 1M context) — ~10 RPM
- Fallback 2: Gemini 3.1 Flash-Lite (fast, high quota) — ~15 RPM
- Fallback 3: Gemini 2.5 Flash-Lite (extra high quota) — ~15 RPM
- Last resort: OpenRouter (external, metered)
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
        self.exhausted = {
            "gemini-3.5-flash": False,
            "gemini-2.5-flash": False,
            "gemini-3.1-flash-lite": False,
            "gemini-2.5-flash-lite": False,
        }

    def is_exhausted(self, model: str) -> bool:
        return self.exhausted.get(model, False)

    def mark_exhausted(self, model: str):
        self.exhausted[model] = True

    def reset(self):
        for model in self.exhausted:
            self.exhausted[model] = False


batch_quota = BatchQuotaState()


class ModelRateLimiter:
    """Controls request pacing per model to prevent 429 Resource Exhausted errors.

    Maintains the last execution time for each model and ensures we space out
    requests according to their free tier RPM limits.
    """

    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request_time: dict[str, float] = {}
        # Minimum spacing between requests (60 / RPM + small safety buffer)
        self._min_spacing: dict[str, float] = {
            "gemini-3.5-flash": 6.5,        # 10 RPM -> 6s + 0.5s buffer
            "gemini-2.5-flash": 6.5,        # 10 RPM -> 6s + 0.5s buffer
            "gemini-3.1-flash-lite": 4.5,   # 15 RPM -> 4s + 0.5s buffer
            "gemini-2.5-flash-lite": 4.5,   # 15 RPM -> 4s + 0.5s buffer
        }

    def _get_lock(self, model: str) -> asyncio.Lock:
        if model not in self._locks:
            self._locks[model] = asyncio.Lock()
        return self._locks[model]

    async def acquire_and_delay(self, model: str):
        """Acquire the model lock and delay if needed to respect RPM."""
        lock = self._get_lock(model)
        await lock.acquire()
        try:
            now = asyncio.get_event_loop().time()
            last_time = self._last_request_time.get(model, 0.0)
            spacing = self._min_spacing.get(model, 2.0)
            elapsed = now - last_time
            if elapsed < spacing:
                wait_time = spacing - elapsed
                logger.info("Pacing %s: sleeping %.2fs to respect RPM...", model, wait_time)
                await asyncio.sleep(wait_time)
            # Update last request time to current time AFTER we finish sleeping
            self._last_request_time[model] = asyncio.get_event_loop().time()
        finally:
            lock.release()


rate_limiter = ModelRateLimiter()


def reset_batch_quota_state():
    """Reset quota exhaustion flags for new batch processing.

    Called at the start of new batch jobs to ensure fresh quota state
    and allow retry of previously exhausted providers.
    """
    batch_quota.reset()


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
    
    # Per-minute rate limits - DO NOT count as daily exhaustion (they should retry with backoff)
    is_per_minute = (
        "perminute" in msg or 
        "per_minute" in msg or 
        "queries per minute" in msg or 
        "requests per minute" in msg
    )
    if is_per_minute:
        return False

    # Standard daily quota strings from Google AI Studio / Gemini API
    has_daily = (
        "perday" in msg or 
        "per_day" in msg or 
        "requests_per_day" in msg or 
        "daily" in msg or 
        "quota exceeded" in msg or 
        "limit: 0" in msg or
        "requests per day" in msg or
        "limit exceeded" in msg
    )
    return has_daily or ("resource_exhausted" in msg and not is_per_minute)


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
    prompt: str, system_prompt: str = "", model: str = "gemini-3.5-flash"
) -> LLMResponse:
    """Call Gemini API with proper systemInstruction separation.

    Uses the Gemini API's dedicated systemInstruction field rather than
    concatenating system and user prompts into a single message. This
    gives the model much stronger instruction adherence.
    """
    url = GEMINI_API_BASE.format(model=model)

    # Build request body with proper system/user separation
    body: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    # Use the dedicated systemInstruction field when available
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{url}?key={settings.google_gemini_api_key}",
            json=body,
        )

    if resp.status_code != 200:
        error_msg = resp.text
        try:
            err_json = resp.json()
            if "error" in err_json:
                error_msg = err_json["error"].get("message", resp.text)
        except Exception:
            pass

        if resp.status_code == 429:
            raise LLMError(f"RESOURCE_EXHAUSTED 429: {error_msg}")
        raise LLMError(f"Gemini API error {resp.status_code}: {error_msg}")

    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise LLMError("Gemini returned no candidates")

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", "")
    if finish_reason == "MAX_TOKENS":
        logger.warning(
            "Gemini %s hit MAX_TOKENS limit — response was truncated. "
            "Consider increasing maxOutputTokens or shortening the prompt.",
            model,
        )
    content = candidate.get("content", {}).get("parts", [{}])[0].get("text", "")
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

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            OPENROUTER_URL,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-2.5-flash",
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 4096,
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
        model="google/gemini-2.5-flash",
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )


async def generate_text(prompt: str, system_prompt: str = "") -> LLMResponse:
    """Generate text using a tiered LLM strategy.

    Free-tier model order (2026-07):
    Tier 1: Gemini 3.5 Flash (stable frontier, ~10 RPM / ~1,500 RPD)
    Tier 2: Gemini 2.5 Flash (hybrid reasoning, ~10 RPM / ~250 RPD)
    Tier 3: Gemini 3.1 Flash-Lite (high quota, ~15 RPM / ~1,000 RPD)
    Tier 4: Gemini 2.5 Flash-Lite (extra high quota, ~15 RPM / ~1,000 RPD)
    Tier 5: OpenRouter (external fallback)

    Each tier has automatic quota exhaustion detection. Once a tier is
    marked exhausted for the current batch, subsequent calls skip directly
    to the next tier.
    """
    last_error = None
    models_to_try = [
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
    ]

    for model in models_to_try:
        if batch_quota.is_exhausted(model):
            continue

        # Respect the RPM limits for this model by pacing the requests
        await rate_limiter.acquire_and_delay(model)

        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                logger.info("Attempting LLM generation with %s (attempt %d/%d)...", model, attempt + 1, max_attempts)
                return await _call_gemini(prompt, system_prompt, model=model)
            except LLMError as e:
                last_error = e
                # Check if it is a daily quota exhaustion
                if is_daily_quota_exhausted(e):
                    batch_quota.mark_exhausted(model)
                    logger.warning("%s daily quota exhausted. Falling back to next model.", model)
                    break  # Break out of the retry loop to fallback to next model

                is_rate_limit = "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e)

                if is_rate_limit:
                    logger.warning("%s hit per-minute rate limit. Backing off before retry...", model)
                    # Progressive backoff for per-minute limits: 8s, 16s, 32s
                    wait_sec = 8 * (2**attempt)
                    await asyncio.sleep(wait_sec)
                else:
                    # For other errors (e.g. transient network or server errors), wait a short time
                    logger.warning("%s failed with non-rate-limit error: %s. Retrying after brief delay...", model, e)
                    await asyncio.sleep(2)
        else:
            # If all 3 attempts failed for this model (e.g. repeated rate limits), we assume it's exhausted
            # or degraded for this batch. Mark it as exhausted so we don't keep wasting time on it.
            batch_quota.mark_exhausted(model)
            logger.warning("All %d attempts failed/rate-limited for %s. Marking exhausted for batch.", max_attempts, model)

    # --- Fallback to OpenRouter (last resort) ---
    logger.warning("All Gemini tiers exhausted or failed. Falling back to OpenRouter.")
    try:
        return await _call_openrouter(prompt, system_prompt)
    except LLMError as e:
        logger.error("OpenRouter fallback also failed: %s", e)
        raise LLMError(f"All LLM providers failed. Last Gemini error: {last_error}. OpenRouter error: {e}") from e
