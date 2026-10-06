"""AI usage cost tracking and logging utilities.

Provides cost estimation for LLM API calls and database logging
for AI usage analytics and billing purposes.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from moose_api.ai.llm_router import LLMResponse
from moose_api.models.ai_usage import AIUsageLog

logger = logging.getLogger(__name__)

# Google AI Studio currently free for Gemini models
COST_PER_1M_INPUT = {
    "google_ai_studio": Decimal("0.0"),
    "openrouter": Decimal("0.15"),
}

COST_PER_1M_OUTPUT = {
    "google_ai_studio": Decimal("0.0"),
    "openrouter": Decimal("0.30"),
}


def estimate_cost(response: LLMResponse) -> Decimal:
    """Calculate USD cost for LLM API response.

    Uses provider-specific pricing tiers to compute total cost based on
    input and output token counts. Costs are scaled to per-token rates.

    Args:
        response: LLM response containing token usage and provider info

    Returns:
        Total cost in USD as Decimal for precise financial calculations
    """
    input_cost = COST_PER_1M_INPUT.get(response.provider, Decimal("0"))
    output_cost = COST_PER_1M_OUTPUT.get(response.provider, Decimal("0"))

    cost = Decimal(str(response.input_tokens)) * input_cost / Decimal("1000000") + Decimal(
        str(response.output_tokens)
    ) * output_cost / Decimal("1000000")
    return cost


async def log_usage(
    db: AsyncSession,
    response: LLMResponse,
    recap_id: int | None = None,
    success: bool = True,
    error_message: str | None = None,
) -> AIUsageLog:
    """Log LLM usage to database for cost tracking and analytics.

    Creates an audit trail of all AI API calls with cost calculations
    and success/failure status for monitoring and billing purposes.

    Args:
        db: Async database session for transaction management
        response: LLM response with usage metrics
        recap_id: Optional associated recap ID for context
        success: Whether the API call succeeded
        error_message: Error details if the call failed

    Returns:
        Created AIUsageLog record (not yet committed to database)
    """
    cost = estimate_cost(response)

    log_entry = AIUsageLog(
        provider=response.provider,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        cost_usd=cost,
        recap_id=recap_id,
        success=success,
        error_message=error_message,
    )
    db.add(log_entry)
    return log_entry
