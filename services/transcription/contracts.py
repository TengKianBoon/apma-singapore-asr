"""Provider-neutral transcript candidate and provenance contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    stripped = value.strip().lower()
    if stripped.endswith("s"):
        stripped = stripped[:-1]
    try:
        return float(stripped)
    except ValueError:
        return None


def _timed_item(
    raw: dict,
    *,
    item_type: str,
    collection: str,
    index: int,
    chunk_start_sec: float,
    chunk_duration_sec: float,
) -> tuple[dict | None, str | None]:
    raw_start = raw.get("start", raw.get("start_sec", raw.get("startOffset", raw.get("start_offset"))))
    raw_end = raw.get("end", raw.get("end_sec", raw.get("endOffset", raw.get("end_offset"))))
    local_start = _seconds(raw_start)
    local_end = _seconds(raw_end)
    if local_start is None or local_end is None:
        return None, f"{collection}[{index}] omitted: provider start/end was unavailable"
    if local_start < 0 or local_end <= local_start or local_end > chunk_duration_sec + 1e-6:
        return None, f"{collection}[{index}] omitted: provider timing was outside the chunk"
    text = raw.get("word", raw.get("text", raw.get("display", "")))
    item = {
        "type": item_type,
        "text": str(text or ""),
        "local_start_sec": local_start,
        "local_end_sec": local_end,
        "global_start_sec": chunk_start_sec + local_start,
        "global_end_sec": chunk_start_sec + local_end,
        "provider_start": raw_start,
        "provider_end": raw_end,
        "speaker": raw.get("speaker", raw.get("speakerLabel")),
        "provider_speaker": raw.get("speaker", raw.get("speakerLabel")),
        "raw_provider_annotation": dict(
            raw.get("raw_provider_annotation")
            if isinstance(raw.get("raw_provider_annotation"), dict)
            else raw
        ),
        "provenance": {
            "raw_collection": collection,
            "raw_index": index,
        },
    }
    return item, None


def normalize_provider_timing(
    *,
    words: list[dict] | None,
    segments: list[dict] | None,
    chunk_start_sec: float,
    chunk_duration_sec: float,
) -> dict:
    """Normalize only provider-native offsets; never infer or clamp timestamps."""

    normalized_words: list[dict] = []
    normalized_segments: list[dict] = []
    warnings: list[str] = []
    for collection, raw_items, item_type, target in (
        ("words", words or [], "provider_native_word", normalized_words),
        ("segments", segments or [], "provider_native_segment", normalized_segments),
    ):
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                warnings.append(f"{collection}[{index}] omitted: provider item was not an object")
                continue
            item, warning = _timed_item(
                raw,
                item_type=item_type,
                collection=collection,
                index=index,
                chunk_start_sec=float(chunk_start_sec),
                chunk_duration_sec=float(chunk_duration_sec),
            )
            if item is not None:
                target.append(item)
            elif warning:
                warnings.append(warning)
    return {
        "chunk": {
            "type": "chunk_global",
            "global_start_sec": float(chunk_start_sec),
            "global_end_sec": float(chunk_start_sec) + float(chunk_duration_sec),
        },
        "provider_native": {
            "words": normalized_words,
            "segments": normalized_segments,
        },
        "normalization_warnings": warnings,
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _response_text(payload: dict) -> str:
    for key in ("text", "transcript"):
        value = payload.get(key)
        if isinstance(value, str):
            return value

    texts: list[str] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content") or {}
        for part in content.get("parts") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return "\n".join(texts)


def normalize_asr_candidate(
    *,
    provider: str,
    provider_code: str,
    model: str,
    raw_response: dict,
    job_id: str,
    run_id: str,
    chunk: dict,
    request_id: str | None = None,
) -> dict:
    """Normalize one ASR response without inventing speech or timestamps."""

    if not isinstance(raw_response, dict):
        raise TypeError("raw_response must be a dictionary")
    canonical_response = json.loads(_canonical_json(raw_response))
    raw_hash = hashlib.sha256(_canonical_json(canonical_response).encode("utf-8")).hexdigest()
    raw_segments = canonical_response.get("segments") or canonical_response.get("utterances") or []
    segments = [dict(item) for item in raw_segments if isinstance(item, dict)]
    resolved_model = canonical_response.get("model") or model
    resolved_model_version = (
        canonical_response.get("modelVersion")
        or canonical_response.get("model_version")
        or canonical_response.get("version")
    )

    return {
        "schema_version": "apma.asr-candidate.v1",
        "job_id": str(job_id),
        "run_id": str(run_id),
        "chunk_id": chunk.get("chunk_id"),
        "chunk_filename": chunk.get("filename"),
        "provider": str(provider),
        "provider_code": str(provider_code),
        "model": str(model),
        "requested_model": str(model),
        "resolved_model": str(resolved_model),
        "resolved_model_version": resolved_model_version,
        "request_id": request_id,
        "text": _response_text(canonical_response),
        "segments": segments,
        "raw_response_sha256": raw_hash,
        "raw_response": canonical_response,
    }


__all__ = ["normalize_asr_candidate", "normalize_provider_timing"]
