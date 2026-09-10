"""Prompt template loading and processing for AI generation.

Loads markdown templates from filesystem, applies variable substitutions,
and prepends guardrails for safe AI prompt construction.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path("/prompts") if Path("/prompts").exists() else Path(__file__).resolve().parents[4] / "prompts"


def load_prompt(template_name: str) -> str:
    """Load prompt template from filesystem.

    Args:
        template_name: Filename of the prompt template

    Returns:
        Template content or empty string if not found
    """
    path = PROMPTS_DIR / template_name
    if not path.exists():
        logger.warning("Prompt template not found: %s", path)
        return ""
    return path.read_text(encoding="utf-8")


def build_guarded_prompt(
    template_name: str,
    replacements: dict[str, str] | None = None,
) -> str:
    """Load template, apply substitutions, and prepend guardrails.

    Constructs safe AI prompts by combining guardrails with variable
    substitution for dynamic content generation.

    Args:
        template_name: Base template filename
        replacements: Key-value pairs for template substitution

    Returns:
        Complete prompt with guardrails and applied substitutions
    """
    guardrails = load_prompt("guardrails.md")
    core_template = load_prompt(template_name)

    if core_template and replacements:
        for key, val in replacements.items():
            core_template = core_template.replace(f"{{{{{key}}}}}", val)

    return f"{guardrails}\n\n{core_template}"


def build_recap_prompt(
    template_name: str,
    stat_payload: dict,
) -> tuple[str, str]:
    """Construct system and user prompts for recap generation.

    Creates paired prompts for AI recap generation with structured
    statistics payload and guardrails-protected system prompt.

    Args:
        template_name: Recap template filename
        stat_payload: Game statistics for AI processing

    Returns:
        Tuple of (system_prompt, user_prompt) for LLM API
    """
    system_prompt = build_guarded_prompt(template_name)
    user_prompt = json.dumps(stat_payload, indent=2, default=str)
    return system_prompt, user_prompt
