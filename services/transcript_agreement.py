"""Derived ASR agreement/disagreement for existing aligned regions only."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from itertools import combinations
import unicodedata
from typing import Any

from services.transcript_alignment import PROVIDER_ORDER


AGREEMENT_METHOD = "normalized_character_token_and_length_similarity_v1"
AGREEMENT_DISCLAIMER = (
    "DERIVED ASR agreement/disagreement only; not transcription correctness, "
    "not provider confidence, and not winner selection."
)


@dataclass(frozen=True)
class AgreementThresholds:
    green_min_pairwise: float = 0.78
    green_mean_pairwise: float = 0.84
    red_min_pairwise: float = 0.32
    red_mean_pairwise: float = 0.42
    red_length_ratio: float = 0.45

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"Agreement threshold {name} must be between 0 and 1")
        if self.red_min_pairwise > self.green_min_pairwise:
            raise ValueError("RED minimum threshold cannot exceed GREEN minimum threshold")
        if self.red_mean_pairwise > self.green_mean_pairwise:
            raise ValueError("RED mean threshold cannot exceed GREEN mean threshold")


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def _normalized_forms(text: str) -> tuple[str, list[str]]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    compact = "".join(
        character
        for character in normalized
        if character.isalnum() or unicodedata.category(character).startswith("M")
    )

    tokens: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            tokens.append("".join(current))
            current.clear()

    for character in normalized:
        if _is_cjk(character):
            flush()
            tokens.append(character)
        elif character.isalnum() or unicodedata.category(character).startswith("M"):
            current.append(character)
        else:
            flush()
    flush()
    return compact, tokens


def _sequence_similarity(left: list[str] | str, right: list[str] | str) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def pairwise_similarity(left_text: str, right_text: str) -> dict[str, float]:
    left_compact, left_tokens = _normalized_forms(left_text)
    right_compact, right_tokens = _normalized_forms(right_text)
    character_similarity = _sequence_similarity(left_compact, right_compact)
    token_similarity = _sequence_similarity(left_tokens, right_tokens)
    longest = max(len(left_compact), len(right_compact))
    length_ratio = (
        1.0 if longest == 0 else min(len(left_compact), len(right_compact)) / longest
    )
    derived_similarity = (
        0.60 * character_similarity
        + 0.30 * token_similarity
        + 0.10 * length_ratio
    )
    return {
        "character_similarity": round(character_similarity, 6),
        "token_similarity": round(token_similarity, 6),
        "length_ratio": round(length_ratio, 6),
        "derived_similarity": round(derived_similarity, 6),
    }


def _classify_region(
    region: dict[str, Any],
    thresholds: AgreementThresholds,
    provider_order: tuple[str, ...],
) -> dict[str, Any]:
    candidates = region.get("candidates") or {}
    missing = [
        provider
        for provider in provider_order
        if not isinstance(candidates.get(provider), dict)
        or candidates[provider].get("missing") is True
    ]
    texts = {
        provider: str(candidates[provider].get("text") or "")
        for provider in provider_order
        if provider not in missing
    }
    empty = [provider for provider, text in texts.items() if not text.strip()]

    pairwise = []
    scores = []
    length_ratios = []
    for left, right in combinations(provider_order, 2):
        if left in missing or right in missing:
            pairwise.append(
                {
                    "providers": [left, right],
                    "missing": True,
                    "character_similarity": None,
                    "token_similarity": None,
                    "length_ratio": None,
                    "derived_similarity": None,
                }
            )
            continue
        similarity = pairwise_similarity(texts[left], texts[right])
        pairwise.append({"providers": [left, right], "missing": False, **similarity})
        scores.append(similarity["derived_similarity"])
        length_ratios.append(similarity["length_ratio"])

    mean_score = round(sum(scores) / len(scores), 6) if scores else 0.0
    min_score = min(scores) if scores else 0.0
    min_length_ratio = min(length_ratios) if length_ratios else 0.0

    if missing:
        status = "RED"
        score = 0.0
        reasons = ["Missing provider candidate: " + ", ".join(missing) + "."]
    elif empty:
        status = "RED"
        score = mean_score
        reasons = ["Empty provider candidate: " + ", ".join(empty) + "."]
    elif min_length_ratio < thresholds.red_length_ratio:
        status = "RED"
        score = mean_score
        reasons = [
            f"Substantial text-length difference; minimum pair length ratio {min_length_ratio:.3f}."
        ]
    elif (
        min_score < thresholds.red_min_pairwise
        or mean_score < thresholds.red_mean_pairwise
    ):
        status = "RED"
        score = mean_score
        reasons = [
            f"Major derived disagreement; minimum pair {min_score:.3f}, mean {mean_score:.3f}."
        ]
    elif (
        min_score >= thresholds.green_min_pairwise
        and mean_score >= thresholds.green_mean_pairwise
    ):
        status = "GREEN"
        score = mean_score
        reasons = [
            f"Substantial derived agreement; minimum pair {min_score:.3f}, mean {mean_score:.3f}."
        ]
    else:
        status = "AMBER"
        score = mean_score
        reasons = [
            f"Meaningful derived differences; minimum pair {min_score:.3f}, mean {mean_score:.3f}."
        ]

    return {
        "agreement_status": status,
        "agreement_basis": AGREEMENT_DISCLAIMER,
        "derived_agreement_score": round(score, 6),
        "pairwise_similarities": pairwise,
        "agreement_reasons": reasons,
    }


def classify_comparison(
    comparison: dict[str, Any],
    thresholds: AgreementThresholds | None = None,
) -> dict[str, Any]:
    """Classify each existing region once without changing candidate text."""

    configured = thresholds or AgreementThresholds()
    configured.validate()
    classified = deepcopy(comparison)
    provider_order = tuple(classified.get("provider_order") or PROVIDER_ORDER)
    if not provider_order or len(set(provider_order)) != len(provider_order):
        raise ValueError("Comparison provider_order must contain unique provider codes")
    regions = classified.get("regions")
    if not isinstance(regions, list):
        raise ValueError("Comparison regions must be a list")

    counts = {"GREEN": 0, "AMBER": 0, "RED": 0}
    for region in regions:
        if not isinstance(region, dict):
            raise ValueError("Each comparison region must be an object")
        result = _classify_region(region, configured, provider_order)
        for field in (
            "agreement_status",
            "agreement_basis",
            "derived_agreement_score",
            "pairwise_similarities",
            "agreement_reasons",
        ):
            region[field] = result[field]
        counts[result["agreement_status"]] += 1

    classified["agreement_analysis"] = {
        "label": AGREEMENT_DISCLAIMER,
        "method": AGREEMENT_METHOD,
        "thresholds": asdict(configured),
        "status_counts": counts,
        "counting_basis": (
            "One status per existing Goal D aligned region. Intentional timestamp "
            "overlaps are not converted into or double-counted as duration totals."
        ),
    }
    statistics = classified.setdefault("statistics", {})
    statistics["classified_regions"] = len(regions)
    statistics["agreement_status_counts"] = counts
    return classified


__all__ = [
    "AGREEMENT_DISCLAIMER",
    "AGREEMENT_METHOD",
    "AgreementThresholds",
    "classify_comparison",
    "pairwise_similarity",
]
