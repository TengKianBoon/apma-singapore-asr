"""Deterministic presentation normalization for APMA-generated text."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Any


SIMPLIFIED_CHINESE = "simplified"
PRESERVE_CHINESE_SCRIPT = "preserve"
SUPPORTED_CHINESE_SCRIPT_PREFERENCES = {
    SIMPLIFIED_CHINESE,
    PRESERVE_CHINESE_SCRIPT,
}


def normalize_chinese_script_preference(value: str | None) -> str:
    preference = str(value or SIMPLIFIED_CHINESE).strip().lower()
    if preference not in SUPPORTED_CHINESE_SCRIPT_PREFERENCES:
        choices = ", ".join(sorted(SUPPORTED_CHINESE_SCRIPT_PREFERENCES))
        raise ValueError(f"CHINESE_SCRIPT_PREFERENCE must be one of: {choices}")
    return preference


@lru_cache(maxsize=1)
def _traditional_to_simplified_converter():
    try:
        from opencc import OpenCC
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError(
            "Simplified Chinese output requires opencc-python-reimplemented"
        ) from exc
    return OpenCC("t2s")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_generated_text(
    text: str,
    preference: str | None = SIMPLIFIED_CHINESE,
) -> tuple[str, dict[str, Any]]:
    """Return canonical APMA text plus auditable script-normalization metadata.

    The transformation changes Chinese character script only. It does not
    translate Mandarin, Hokkien, English, Bahasa Indonesia, names, or numbers.
    """

    if not isinstance(text, str):
        raise TypeError("Generated text must be a string")
    resolved = normalize_chinese_script_preference(preference)
    normalized = (
        _traditional_to_simplified_converter().convert(text)
        if resolved == SIMPLIFIED_CHINESE
        else text
    )
    return normalized, {
        "preference": resolved,
        "method": "opencc_t2s" if resolved == SIMPLIFIED_CHINESE else "preserve",
        "changed": normalized != text,
        "source_text_sha256": _sha256_text(text),
        "output_text_sha256": _sha256_text(normalized),
        "translation_performed": False,
        "applies_to": "Chinese characters, including Mandarin and Hokkien Han text",
    }


def normalize_segment_texts(
    segments: list[dict[str, Any]],
    preference: str | None = SIMPLIFIED_CHINESE,
) -> list[dict[str, Any]]:
    """Normalize copies of segment text without mutating provider evidence."""

    normalized_segments: list[dict[str, Any]] = []
    for segment in segments:
        normalized_segment = dict(segment)
        text = normalized_segment.get("text")
        if isinstance(text, str):
            normalized_segment["text"] = normalize_generated_text(text, preference)[0]
        normalized_segments.append(normalized_segment)
    return normalized_segments


def simplified_chinese_instruction(preference: str | None) -> str:
    if normalize_chinese_script_preference(preference) == SIMPLIFIED_CHINESE:
        return (
            "When Chinese characters are used, including for Mandarin or Hokkien, "
            "use Simplified Chinese characters. Preserve the spoken language; do not "
            "translate Hokkien into Mandarin."
        )
    return "Preserve the provider's original Chinese character script."


__all__ = [
    "PRESERVE_CHINESE_SCRIPT",
    "SIMPLIFIED_CHINESE",
    "normalize_chinese_script_preference",
    "normalize_generated_text",
    "normalize_segment_texts",
    "simplified_chinese_instruction",
]
