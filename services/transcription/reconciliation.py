"""Strict, provider-neutral transcript candidate reconciliation.

Reconciliation in APMA is evidence selection, not text generation. This module
never combines, rewrites, translates, or repairs candidate speech. It selects
one existing candidate verbatim and records enough evidence for later review.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Sequence


RECONCILIATION_SCHEMA = "apma.transcript.reconciliation.v1"
LEXICAL_AGREEMENT_THRESHOLD = 0.85


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _candidate_id(record: dict[str, Any]) -> str:
    chunk = record["chunk"]
    identity = {
        "chunk_filename": chunk.get("chunk_filename"),
        "chunk_sha256": chunk.get("chunk_sha256"),
        "source_sha256": chunk.get("source_sha256"),
        "provider": chunk.get("provider"),
        "provider_code": chunk.get("provider_code"),
        "model": chunk.get("model"),
        "run_id": chunk.get("run_id"),
        "transcript_path": chunk.get("transcript_path"),
        "text": record.get("text", ""),
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    return f"cand-{digest[:20]}"


def _candidate_view(record: dict[str, Any]) -> dict[str, Any]:
    chunk = record["chunk"]
    text = str(record.get("text", ""))
    return {
        "candidate_id": _candidate_id(record),
        "ordinal": int(record["ordinal"]),
        "provider": chunk.get("provider"),
        "provider_code": chunk.get("provider_code"),
        "model": chunk.get("model"),
        "run_id": chunk.get("run_id"),
        "response_format": chunk.get("response_format"),
        "diarized": bool(chunk.get("diarized")),
        "chunk_filename": chunk.get("chunk_filename"),
        "chunk_sha256": chunk.get("chunk_sha256"),
        "source_sha256": chunk.get("source_sha256"),
        "transcript_path": chunk.get("transcript_path"),
        "provider_artifact_path": chunk.get("provider_artifact_path"),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
        "segments": [dict(segment) for segment in record.get("segments", [])],
    }


def _pairwise_agreement(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for left_index, left in enumerate(candidates):
        left_text = _normalized_text(left["text"])
        for right in candidates[left_index + 1 :]:
            right_text = _normalized_text(right["text"])
            ratio = SequenceMatcher(None, left_text, right_text, autojunk=False).ratio()
            comparisons.append(
                {
                    "left_candidate_id": left["candidate_id"],
                    "right_candidate_id": right["candidate_id"],
                    "lexical_similarity": round(ratio, 6),
                }
            )
    return comparisons


def reconcile_chunk_candidates(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Select one existing chunk candidate and grade the supporting evidence."""

    if not records:
        raise ValueError("At least one transcript candidate is required")

    ordered_records = sorted(records, key=lambda record: int(record["ordinal"]))
    hashes = {
        str(record["chunk"].get("chunk_sha256"))
        for record in ordered_records
        if record["chunk"].get("chunk_sha256")
    }
    if len(hashes) > 1:
        raise ValueError("Transcript candidates for one chunk have conflicting chunk hashes")

    candidates = [_candidate_view(record) for record in ordered_records]
    usable_indexes = [
        index for index, candidate in enumerate(candidates) if _normalized_text(candidate["text"])
    ]
    selected_index = usable_indexes[0] if usable_indexes else 0
    selected_record = ordered_records[selected_index]
    selected_candidate = candidates[selected_index]
    comparisons = _pairwise_agreement(candidates)
    similarities = [item["lexical_similarity"] for item in comparisons]
    minimum_similarity = min(similarities) if similarities else None
    normalized_texts = [_normalized_text(candidate["text"]) for candidate in candidates]
    all_equal = bool(normalized_texts) and len(set(normalized_texts)) == 1
    independent_providers = {
        str(candidate.get("provider")).strip().casefold()
        for candidate in candidates
        if str(candidate.get("provider") or "").strip()
    }

    if not usable_indexes:
        grade = "red"
        reason = "no_usable_candidate_text"
    elif len(usable_indexes) != len(candidates):
        grade = "red"
        reason = "one_or_more_candidates_are_empty"
    elif len(candidates) == 1:
        grade = "amber"
        reason = "single_candidate_only"
    elif all_equal and len(independent_providers) >= 2:
        grade = "green"
        reason = "exact_normalized_agreement_across_independent_providers"
    elif all_equal:
        grade = "amber"
        reason = "agreement_is_within_one_provider"
    elif minimum_similarity is not None and minimum_similarity >= LEXICAL_AGREEMENT_THRESHOLD:
        grade = "amber"
        reason = "close_lexical_agreement_with_unresolved_differences"
    else:
        grade = "red"
        reason = "material_candidate_disagreement"

    review_recommendation = {
        "green": "generally_accepted",
        "amber": "review_consideration",
        "red": "strong_review_required",
    }[grade]
    selection_reason = (
        "first_nonempty_candidate_preserved"
        if selected_index != 0
        else "primary_candidate_preserved"
    )

    return {
        "selected_record": selected_record,
        "candidates": candidates,
        "decision": {
            "schema_version": RECONCILIATION_SCHEMA,
            "grade": grade,
            "reason": reason,
            "review_recommendation": review_recommendation,
            "selected_candidate_id": selected_candidate["candidate_id"],
            "selection_policy": "verbatim_candidate_only",
            "selection_reason": selection_reason,
            "candidate_count": len(candidates),
            "independent_provider_count": len(independent_providers),
            "all_candidates_normalized_equal": all_equal,
            "minimum_pairwise_lexical_similarity": minimum_similarity,
            "lexical_agreement_threshold": LEXICAL_AGREEMENT_THRESHOLD,
            "pairwise_agreement": comparisons,
            "unresolved_uncertainty": grade != "green",
            "synthesis_performed": False,
            "translation_performed": False,
        },
    }


__all__ = [
    "LEXICAL_AGREEMENT_THRESHOLD",
    "RECONCILIATION_SCHEMA",
    "reconcile_chunk_candidates",
]
