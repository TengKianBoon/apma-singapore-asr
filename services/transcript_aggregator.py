from __future__ import annotations

import datetime
import json
from pathlib import Path
import re
from typing import Any, Iterable

from services.transcript_exports import write_transcript_exports
from services.transcription.reconciliation import reconcile_chunk_candidates


CHUNK_TRANSCRIPT_SCHEMA = "apma.transcript.chunk.v1"
OVERLAP_POLICY = "exact_normalized_boundary_overlap_deduplication"
_OVERLAP_MIN_TOKENS = 4
_OVERLAP_MIN_NORMALIZED_CHARACTERS = 12
_OVERLAP_MAX_TOKENS = 100
_ANCHORED_OVERLAP_MIN_TOKENS = 8
_ANCHORED_OVERLAP_EDGE_TOKENS = 25
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*|"
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\W_]",
    re.UNICODE,
)
_LEADING_SEPARATOR_RE = re.compile(r"^[\s.,!?;:…，。！？；：、\-–—]+")


def _now_iso() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _transcript_path(entry: dict[str, Any], job_dir: Path) -> Path:
    raw_path = entry.get("transcript_path") or entry.get("path")
    if not raw_path:
        raise ValueError("Transcript manifest entry is missing transcript_path")
    path = Path(raw_path).resolve()
    transcripts_dir = (job_dir / "transcripts").resolve()
    try:
        path.relative_to(transcripts_dir)
    except ValueError as exc:
        raise ValueError(
            f"Transcript path must be inside the job transcripts directory: {path}"
        ) from exc
    return path


def _validate_payload_identity(
    job_id: str,
    entry: dict[str, Any],
    payload: dict[str, Any],
    transcript_path: Path,
) -> None:
    is_canonical = payload.get("schema_version") == CHUNK_TRANSCRIPT_SCHEMA
    if is_canonical:
        required = (
            "job_id",
            "chunk_filename",
            "provider",
            "provider_code",
            "model",
            "run_id",
            "response_format",
            "segment_timing_scope",
        )
        missing = [field for field in required if payload.get(field) in (None, "")]
        if missing:
            raise ValueError(
                f"Canonical transcript {transcript_path} is missing: {', '.join(missing)}"
            )

    payload_job_id = payload.get("job_id")
    if payload_job_id not in (None, job_id):
        raise ValueError(
            f"Transcript {transcript_path} has job_id {payload_job_id!r}, expected {job_id!r}"
        )

    for field in (
        "chunk_filename",
        "chunk_sha256",
        "provider",
        "provider_code",
        "model",
        "run_id",
        "response_format",
    ):
        expected = entry.get(field)
        actual = payload.get(field)
        if expected not in (None, "") and actual in (None, ""):
            if is_canonical:
                raise ValueError(
                    f"Transcript {transcript_path} is missing required identity field {field}"
                )
            continue
        if expected not in (None, "") and actual not in (None, "") and expected != actual:
            raise ValueError(
                f"Transcript {transcript_path} has mismatched {field}: "
                f"expected {expected!r}, found {actual!r}"
            )


def _segment_texts(payload: dict[str, Any]) -> list[str]:
    segments = payload.get("segments")
    if isinstance(segments, list):
        rendered = []
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text", "")).strip()
            if not text:
                continue
            raw_speaker = segment.get("speaker")
            speaker = str(raw_speaker).strip() if raw_speaker is not None else ""
            rendered.append(f"{speaker}: {text}" if speaker else text)
        if rendered:
            return rendered

    text = str(payload.get("text", "")).strip()
    return [text] if text else []


def _absolute_time(value: Any, timing_scope: str, chunk_start: float) -> float | None:
    if value is None:
        return None
    timestamp = float(value)
    return timestamp + chunk_start if timing_scope == "chunk" else timestamp


def _normalized_segments(payload: dict[str, Any], chunk_filename: str | None) -> list[dict[str, Any]]:
    segments = payload.get("segments")
    if isinstance(segments, list):
        normalized = []
        timing_scope = payload.get("segment_timing_scope", "chunk")
        chunk_start = float(payload.get("chunk_start_sec", 0.0) or 0.0)
        for index, seg in enumerate(segments, start=1):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text", "")).strip()
            if not text:
                continue
            speaker = seg.get("speaker")
            provider_speaker = seg.get("provider_speaker") or speaker
            normalized.append({
                "segment_id": f"{chunk_filename or 'chunk'}:{index:04d}",
                "chunk_filename": chunk_filename,
                "chunk_start_sec": chunk_start,
                "start_sec": _absolute_time(seg.get("start_sec"), timing_scope, chunk_start),
                "end_sec": _absolute_time(seg.get("end_sec"), timing_scope, chunk_start),
                "speaker": speaker,
                "provider_speaker": provider_speaker,
                "speaker_scope": seg.get("speaker_scope", "chunk" if speaker else None),
                "text": text,
                "provider": payload.get("provider"),
                "provider_code": payload.get("provider_code"),
                "model": payload.get("model"),
                "run_id": payload.get("run_id"),
            })
        if normalized:
            return normalized

    text = str(payload.get("text", "")).strip()
    if not text:
        return []
    return [
        {
            "segment_id": f"{chunk_filename or 'chunk'}:0001",
            "chunk_filename": chunk_filename,
            "chunk_start_sec": float(payload.get("chunk_start_sec", 0.0) or 0.0),
            "start_sec": None,
            "end_sec": None,
            "speaker": None,
            "provider_speaker": None,
            "speaker_scope": None,
            "text": text,
            "provider": payload.get("provider"),
            "provider_code": payload.get("provider_code"),
            "model": payload.get("model"),
            "run_id": payload.get("run_id"),
        }
    ]


def _tokens_with_spans(text: str) -> list[tuple[str, int, int]]:
    return [
        (match.group(0).casefold(), match.start(), match.end())
        for match in _TOKEN_RE.finditer(text)
    ]


def _overlap_prefix_end(previous_text: str, current_text: str) -> tuple[int, int]:
    """Return raw prefix characters and tokens for the longest exact normalized overlap."""
    previous_tokens = _tokens_with_spans(previous_text)
    current_tokens = _tokens_with_spans(current_text)
    maximum = min(len(previous_tokens), len(current_tokens), _OVERLAP_MAX_TOKENS)
    for count in range(maximum, _OVERLAP_MIN_TOKENS - 1, -1):
        previous_suffix = [token[0] for token in previous_tokens[-count:]]
        current_prefix = [token[0] for token in current_tokens[:count]]
        if previous_suffix != current_prefix:
            continue
        if len("".join(current_prefix)) < _OVERLAP_MIN_NORMALIZED_CHARACTERS:
            continue
        prefix_end = current_tokens[count - 1][2]
        separator = _LEADING_SEPARATOR_RE.match(current_text[prefix_end:])
        if separator:
            prefix_end += separator.end()
        return prefix_end, count
    return 0, 0


def _overlap_removal_span(previous_text: str, current_text: str) -> tuple[int, int, int, str | None]:
    """Find a conservative exact repeat at, or tightly anchored near, a chunk boundary."""
    prefix_end, prefix_tokens = _overlap_prefix_end(previous_text, current_text)
    if prefix_end:
        return 0, prefix_end, prefix_tokens, "suffix_prefix"

    previous_tokens = _tokens_with_spans(previous_text)[-_OVERLAP_MAX_TOKENS:]
    current_tokens = _tokens_with_spans(current_text)[:_OVERLAP_MAX_TOKENS]
    candidates: list[tuple[int, int, int, int, int, int]] = []
    for previous_index in range(len(previous_tokens)):
        for current_index in range(len(current_tokens)):
            count = 0
            while (
                previous_index + count < len(previous_tokens)
                and current_index + count < len(current_tokens)
                and previous_tokens[previous_index + count][0]
                == current_tokens[current_index + count][0]
            ):
                count += 1
            if count < _ANCHORED_OVERLAP_MIN_TOKENS:
                continue
            normalized_characters = len(
                "".join(token[0] for token in current_tokens[current_index:current_index + count])
            )
            if normalized_characters < _OVERLAP_MIN_NORMALIZED_CHARACTERS:
                continue
            previous_trailing_tokens = len(previous_tokens) - previous_index - count
            if (
                previous_trailing_tokens > _ANCHORED_OVERLAP_EDGE_TOKENS
                or current_index > _ANCHORED_OVERLAP_EDGE_TOKENS
            ):
                continue
            start = current_tokens[current_index][1]
            end = current_tokens[current_index + count - 1][2]
            separator = _LEADING_SEPARATOR_RE.match(current_text[end:])
            if separator:
                end += separator.end()
            candidates.append(
                (
                    -count,
                    previous_trailing_tokens + current_index,
                    current_index,
                    previous_trailing_tokens,
                    start,
                    end,
                )
            )
    if not candidates:
        return 0, 0, 0, None
    best = min(candidates)
    return best[4], best[5], -best[0], "anchored_exact_span"


def _remove_text_span(text: str, start: int, end: int) -> str:
    if end <= start:
        return text
    left = text[:start].rstrip()
    right = text[end:].lstrip()
    if left and right:
        return f"{left} {right}"
    return left or right


def _trim_unlabelled_segments(
    segments: list[dict[str, Any]], removal_start: int, removal_end: int
) -> list[dict[str, Any]]:
    """Remove a known exact text span without attempting timestamp alignment."""
    if removal_end <= removal_start or any(segment.get("speaker") for segment in segments):
        return [dict(segment) for segment in segments]

    trimmed: list[dict[str, Any]] = []
    cursor = 0
    for segment in segments:
        current = dict(segment)
        text = str(current.get("text", ""))
        segment_start = cursor
        segment_end = cursor + len(text)
        overlap_start = max(removal_start, segment_start)
        overlap_end = min(removal_end, segment_end)
        if overlap_end > overlap_start:
            text = _remove_text_span(
                text,
                overlap_start - segment_start,
                overlap_end - segment_start,
            )
        text = text.strip()
        if text:
            current["text"] = text
            trimmed.append(current)
        cursor = segment_end + 1
    return trimmed


def aggregate_transcripts(job_id: str, job_dir: Path, transcript_entries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Merge per-chunk transcript JSON files into full transcript outputs."""
    job_dir = Path(job_dir)
    outputs_dir = job_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []

    for ordinal, entry in enumerate(transcript_entries):
        transcript_path = _transcript_path(entry, job_dir)
        payload = _read_json(transcript_path)
        _validate_payload_identity(job_id, entry, payload, transcript_path)
        chunk_filename = payload.get("chunk_filename") or entry.get("chunk_filename")
        chunk_texts = _segment_texts(payload)
        chunk_text = "\n".join(chunk_texts)
        chunk_start = payload.get("chunk_start_sec", entry.get("chunk_start_sec"))
        records.append(
            {
                "ordinal": ordinal,
                "sort_start": float(chunk_start or 0.0),
                "chunk": {
                    "chunk_filename": chunk_filename,
                    "chunk_sha256": payload.get("chunk_sha256", entry.get("chunk_sha256")),
                    "source_sha256": payload.get("source_sha256", entry.get("source_sha256")),
                    "transcript_path": str(transcript_path),
                    "provider_artifact_path": payload.get(
                        "provider_artifact_path", entry.get("provider_artifact_path")
                    ),
                    "duration_seconds": payload.get("duration_seconds", entry.get("duration_seconds")),
                    "chunk_start_sec": chunk_start,
                    "chunk_end_sec": payload.get("chunk_end_sec", entry.get("chunk_end_sec")),
                    "provider": payload.get("provider", entry.get("provider")),
                    "provider_code": payload.get("provider_code", entry.get("provider_code")),
                    "model": payload.get("model", entry.get("model")),
                    "run_id": payload.get("run_id", entry.get("run_id")),
                    "response_format": payload.get("response_format", entry.get("response_format")),
                    "diarized": bool(payload.get("diarized", entry.get("diarized", False))),
                    "text": chunk_text,
                },
                "segments": _normalized_segments(payload, chunk_filename),
                "text": chunk_text,
            }
        )

    grouped_records: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        chunk_filename = record["chunk"].get("chunk_filename")
        if chunk_filename:
            group_key = ("chunk", str(chunk_filename))
        else:
            group_key = ("transcript", str(record["chunk"]["transcript_path"]))
        grouped_records.setdefault(group_key, []).append(record)

    ordered_groups = sorted(
        grouped_records.values(),
        key=lambda group: min(
            (
                record["sort_start"],
                str(record["chunk"].get("chunk_filename") or ""),
                record["ordinal"],
            )
            for record in group
        ),
    )
    canonical_records: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    all_candidates: list[dict[str, Any]] = []
    chunk_reviews: list[dict[str, Any]] = []
    grade_counts = {"green": 0, "amber": 0, "red": 0}

    for group in ordered_groups:
        reconciliation = reconcile_chunk_candidates(group)
        selected_record = reconciliation["selected_record"]
        candidates = reconciliation["candidates"]
        decision = reconciliation["decision"]
        selected_chunk = dict(selected_record["chunk"])
        selected_chunk["candidate_count"] = len(candidates)
        selected_chunk["candidates"] = candidates
        selected_chunk["reconciliation"] = decision
        canonical_records.append(selected_record)
        chunks.append(selected_chunk)
        all_candidates.extend(candidates)
        grade_counts[decision["grade"]] += 1
        chunk_reviews.append(
            {
                "chunk_filename": selected_chunk.get("chunk_filename"),
                "grade": decision["grade"],
                "reason": decision["reason"],
                "review_recommendation": decision["review_recommendation"],
                "candidate_count": decision["candidate_count"],
                "selected_candidate_id": decision["selected_candidate_id"],
            }
        )

    segments: list[dict[str, Any]] = []
    text_parts: list[str] = []
    previous_text = ""
    for record, chunk in zip(canonical_records, chunks):
        raw_text = record["text"]
        has_speaker_labels = any(segment.get("speaker") for segment in record["segments"])
        if has_speaker_labels:
            removal_start, removal_end, removed_tokens, match_mode = 0, 0, 0, None
        else:
            removal_start, removal_end, removed_tokens, match_mode = _overlap_removal_span(
                previous_text, raw_text
            )
        deduplicated_text = _remove_text_span(raw_text, removal_start, removal_end)
        chunk["overlap_dedup"] = {
            "policy": OVERLAP_POLICY,
            "applied": bool(removal_end > removal_start),
            "match_mode": match_mode,
            "removed_token_count": removed_tokens,
            "removed_character_count": max(0, removal_end - removal_start),
            "removal_start_character": removal_start,
        }
        segments.extend(
            _trim_unlabelled_segments(record["segments"], removal_start, removal_end)
        )
        if deduplicated_text:
            text_parts.append(deduplicated_text)
            previous_text = (
                f"{previous_text}\n\n{deduplicated_text}" if previous_text else deduplicated_text
            )

    segments.sort(
        key=lambda segment: (
            segment.get("start_sec") is None,
            segment.get("start_sec") if segment.get("start_sec") is not None else 0.0,
            segment.get("end_sec") if segment.get("end_sec") is not None else 0.0,
            str(segment.get("segment_id") or ""),
        )
    )
    full_text = "\n\n".join(text_parts)
    json_path = outputs_dir / "full_transcript.json"
    txt_path = outputs_dir / "full_transcript.txt"

    diarized = any(bool(chunk.get("diarized")) for chunk in chunks)
    highest_attention_grade = (
        "red"
        if grade_counts["red"]
        else "amber"
        if grade_counts["amber"]
        else "green"
        if grade_counts["green"]
        else None
    )
    provider_artifacts = sorted(
        {
            str(candidate["provider_artifact_path"])
            for candidate in all_candidates
            if candidate.get("provider_artifact_path")
        }
    )
    output_payload = {
        "schema_version": "apma.transcript.full.v1",
        "job_id": job_id,
        "created_at": _now_iso(),
        "diarized": diarized,
        "speaker_identity_scope": "chunk" if diarized else None,
        "overlap_policy": OVERLAP_POLICY,
        "reconciliation_policy": "verbatim_candidate_only",
        "provenance": {
            "providers": sorted(
                {str(candidate["provider"]) for candidate in all_candidates if candidate.get("provider")}
            ),
            "models": sorted(
                {str(candidate["model"]) for candidate in all_candidates if candidate.get("model")}
            ),
            "run_ids": sorted(
                {str(candidate["run_id"]) for candidate in all_candidates if candidate.get("run_id")}
            ),
            "source_sha256": sorted(
                {
                    str(candidate["source_sha256"])
                    for candidate in all_candidates
                    if candidate.get("source_sha256")
                }
            ),
            "provider_artifacts": provider_artifacts,
        },
        "candidate_count": len(all_candidates),
        "review": {
            "schema_version": "apma.transcript.review-summary.v1",
            "grade_counts": grade_counts,
            "highest_attention_grade": highest_attention_grade,
            "human_review_recommended": bool(grade_counts["amber"] or grade_counts["red"]),
            "human_review_required": bool(grade_counts["red"]),
            "chunks": chunk_reviews,
        },
        "correction_history": [],
        "chunks": chunks,
        "segments": segments,
        "text": full_text,
        "word_count": len(full_text.split()),
        "character_count": len(full_text),
    }

    with json_path.open("w", encoding="utf-8") as fh:
        json.dump(output_payload, fh, indent=2, ensure_ascii=False)
    txt_path.write_text(full_text, encoding="utf-8")
    derived_exports = write_transcript_exports(output_payload, outputs_dir)

    return {
        "state": "completed",
        "full_transcript_json": str(json_path),
        "full_transcript_txt": str(txt_path),
        **derived_exports,
        "errors": [],
    }
