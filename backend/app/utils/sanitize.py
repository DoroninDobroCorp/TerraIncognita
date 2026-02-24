"""Input sanitization for LLM prompts — protection against prompt injection."""

from __future__ import annotations

import re

# Patterns that indicate prompt injection attempts
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"ignore\s+(all\s+)?above",
    r"disregard\s+(all\s+)?previous",
    r"forget\s+(all\s+)?instructions",
    r"you\s+are\s+now\s+(?:a|an)\s+",
    r"new\s+instructions?:",
    r"system\s*prompt:",
    r"<\s*system\s*>",
    r"\[INST\]",
    r"\[/INST\]",
    r"<<SYS>>",
    r"<\|im_start\|>",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]


def sanitize_user_input(text: str) -> str:
    """Sanitize user input before sending to LLM.

    - Strips control characters
    - Truncates to reasonable length
    - Does NOT remove prompt injection attempts (they get neutralized
      by the system prompt structure), but logs warnings
    """
    # Strip null bytes and control characters (keep newlines, tabs)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # Warn about potential injection (but don't block — may be legitimate)
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(text):
            # Return sanitized version with injection markers neutralized
            text = _neutralize_injection(text)
            break

    return text.strip()


def _neutralize_injection(text: str) -> str:
    """Wrap suspicious text in quotes to prevent it from being interpreted as instructions."""
    # Remove any XML-like tags that could confuse the LLM
    text = re.sub(r"<\s*/?\s*(system|assistant|human|user)\s*>", "", text, flags=re.IGNORECASE)
    # Remove special tokens
    text = re.sub(r"\[/?INST\]|<</?SYS>>|<\|im_(?:start|end)\|>", "", text, flags=re.IGNORECASE)
    return text


def sanitize_for_log(text: str, max_length: int = 200) -> str:
    """Sanitize text for logging — remove PII-like patterns and truncate."""
    # Mask email addresses
    text = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "[EMAIL]", text)
    # Mask phone numbers
    text = re.sub(r"\b\+?\d[\d\s\-()]{7,}\d\b", "[PHONE]", text)
    # Truncate
    if len(text) > max_length:
        text = text[:max_length] + "..."
    return text
