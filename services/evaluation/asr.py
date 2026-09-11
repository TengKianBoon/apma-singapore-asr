"""Dependency-free, offline ASR regression metrics and contract validation."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Sequence


SUPPORTED_SCHEMAS = {
    "apma.transcript.chunk.v1",
    "apma.transcript.full.v1",
}

DEFAULT_SCORECARD_THRESHOLDS = {
    "schema_version": "apma.asr-scorecard-thresholds.v1",
    "pass_mean_word_error_rate": 0.15,
    "pass_mean_character_error_rate": 0.10,
    "fail_mean_word_error_rate": 0.30,
    "fail_mean_character_error_rate": 0.20,
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower())


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row_index, reference_item in enumerate(reference, start=1):
        current = [row_index]
        for column_index, hypothesis_item in enumerate(hypothesis, start=1):
            substitution = previous[column_index - 1] + (reference_item != hypothesis_item)
            insertion = current[column_index - 1] + 1
            deletion = previous[column_index] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = _normalize_text(reference).split()
    hypothesis_words = _normalize_text(hypothesis).split()
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return _edit_distance(reference_words, hypothesis_words) / len(reference_words)


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference_chars = list(_normalize_text(reference).replace(" ", ""))
    hypothesis_chars = list(_normalize_text(hypothesis).replace(" ", ""))
    if not reference_chars:
        return 0.0 if not hypothesis_chars else 1.0
    return _edit_distance(reference_chars, hypothesis_chars) / len(reference_chars)


def validate_transcript_contract(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") not in SUPPORTED_SCHEMAS:
        errors.append("unsupported or missing schema_version")
    if not isinstance(payload.get("text"), str):
        errors.append("text must be a string")

    segments = payload.get("segments")
    if not isinstance(segments, list):
        errors.append("segments must be a list")
        return errors

    if payload.get("diarized"):
        for index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                errors.append(f"segments[{index}] must be an object")
                continue
            if not str(segment.get("speaker") or segment.get("provider_speaker") or "").strip():
                errors.append(f"segments[{index}] is missing a speaker label")
            start = segment.get("start_sec")
            end = segment.get("end_sec")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                errors.append(f"segments[{index}] must have numeric start_sec and end_sec")
            elif float(start) > float(end):
                errors.append(f"segments[{index}] start_sec exceeds end_sec")
    return errors


def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    reference = str(case.get("reference", ""))
    hypothesis = str(case.get("hypothesis", ""))
    return {
        "id": case.get("id"),
        "language": case.get("language"),
        "provider": case.get("provider", "fixture"),
        "model": case.get("model", "synthetic"),
        "duration_seconds": float(case.get("duration_seconds", 0.0)),
        "cost_usd": float(case.get("cost_usd", 0.0)),
        "contract_errors": list(case.get("contract_errors") or []),
        "word_error_rate": word_error_rate(reference, hypothesis),
        "character_error_rate": character_error_rate(reference, hypothesis),
    }


def _scorecard_status(mean_wer: float, mean_cer: float, contract_error_count: int, thresholds: dict) -> str:
    if contract_error_count:
        return "fail"
    if (
        mean_wer > float(thresholds["fail_mean_word_error_rate"])
        or mean_cer > float(thresholds["fail_mean_character_error_rate"])
    ):
        return "fail"
    if (
        mean_wer > float(thresholds["pass_mean_word_error_rate"])
        or mean_cer > float(thresholds["pass_mean_character_error_rate"])
    ):
        return "warn"
    return "pass"


def build_provider_scorecards(
    results: Sequence[dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate comparable provider/model results without declaring a winner."""

    policy = dict(DEFAULT_SCORECARD_THRESHOLDS if thresholds is None else thresholds)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        key = (str(result.get("provider", "unknown")), str(result.get("model", "unknown")))
        grouped[key].append(dict(result))

    scorecards: list[dict[str, Any]] = []
    for (provider, model), items in sorted(grouped.items()):
        count = len(items)
        mean_wer = sum(float(item["word_error_rate"]) for item in items) / count
        mean_cer = sum(float(item["character_error_rate"]) for item in items) / count
        duration_seconds = sum(float(item.get("duration_seconds", 0.0)) for item in items)
        total_cost = sum(float(item.get("cost_usd", 0.0)) for item in items)
        contract_error_count = sum(len(item.get("contract_errors") or []) for item in items)
        audio_minutes = duration_seconds / 60.0
        scorecards.append({
            "provider": provider,
            "model": model,
            "case_count": count,
            "languages": sorted({str(item.get("language")) for item in items}),
            "mean_word_error_rate": mean_wer,
            "mean_character_error_rate": mean_cer,
            "max_word_error_rate": max(float(item["word_error_rate"]) for item in items),
            "max_character_error_rate": max(float(item["character_error_rate"]) for item in items),
            "contract_error_count": contract_error_count,
            "duration_seconds": duration_seconds,
            "total_cost_usd": total_cost,
            "cost_per_audio_minute_usd": None if audio_minutes <= 0 else total_cost / audio_minutes,
            "qualification": _scorecard_status(mean_wer, mean_cer, contract_error_count, policy),
        })
    return scorecards


def build_release_gate(scorecards: Sequence[dict[str, Any]], thresholds: dict[str, Any] | None = None) -> dict:
    policy = dict(DEFAULT_SCORECARD_THRESHOLDS if thresholds is None else thresholds)
    qualifications = [str(card.get("qualification")) for card in scorecards]
    if not qualifications or "fail" in qualifications:
        status = "fail"
    elif "warn" in qualifications:
        status = "warn"
    else:
        status = "pass"
    return {
        "schema_version": "apma.asr-release-gate.v1",
        "status": status,
        "thresholds": policy,
        "provider_scorecard_count": len(scorecards),
        "comparison_claim": "No provider winner is declared without representative evidence.",
    }
