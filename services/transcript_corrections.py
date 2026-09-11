from __future__ import annotations

import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.transcript_exports import write_transcript_exports


FULL_TRANSCRIPT_SCHEMA = "apma.transcript.full.v1"


def _required_text(field: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _load_canonical(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"Canonical transcript does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Canonical transcript is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Canonical transcript must be a JSON object")
    if payload.get("schema_version") != FULL_TRANSCRIPT_SCHEMA:
        raise ValueError(
            f"Unsupported transcript schema: {payload.get('schema_version')!r}; "
            f"expected {FULL_TRANSCRIPT_SCHEMA!r}"
        )
    return payload


def _validated_segments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    segments = payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError("Canonical transcript segments must be a list")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("Canonical transcript contains an invalid segment")
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise ValueError("Every transcript segment must have a segment_id")
        if segment_id in seen:
            raise ValueError(f"Canonical transcript contains duplicate segment_id: {segment_id}")
        seen.add(segment_id)
        validated.append(segment)
    return validated


def _render_segment(segment: dict[str, Any]) -> str:
    text = str(segment.get("text") or "").strip()
    speaker = str(segment.get("speaker") or "").strip()
    return f"{speaker}: {text}" if speaker else text


def _rebuild_text(payload: dict[str, Any]) -> None:
    segments = payload["segments"]
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        raise ValueError("Canonical transcript chunks must be a list")

    by_chunk: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        chunk_filename = str(segment.get("chunk_filename") or "").strip()
        if chunk_filename:
            by_chunk.setdefault(chunk_filename, []).append(segment)

    chunk_texts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise ValueError("Canonical transcript contains an invalid chunk")
        chunk_filename = str(chunk.get("chunk_filename") or "").strip()
        chunk_segments = by_chunk.get(chunk_filename, [])
        if chunk_segments:
            chunk["text"] = "\n".join(_render_segment(segment) for segment in chunk_segments)
        chunk_text = str(chunk.get("text") or "").strip()
        if chunk_text:
            chunk_texts.append(chunk_text)

    full_text = "\n\n".join(chunk_texts)
    payload["text"] = full_text
    payload["word_count"] = len(full_text.split())
    payload["character_count"] = len(full_text)


def _correction_id(event: dict[str, Any]) -> str:
    identity = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"corr-{_sha256(identity)[:20]}"


def apply_segment_correction(
    job_dir: str | Path,
    segment_id: str,
    accepted_text: str,
    reason: str,
    reviewer: str,
    *,
    expected_original_sha256: str | None = None,
    corrected_at: str | None = None,
) -> dict[str, Any]:
    """Apply an evidence-preserving human correction to one canonical segment."""

    segment_id = _required_text("segment_id", segment_id)
    accepted_text = _required_text("accepted_text", accepted_text)
    reason = _required_text("reason", reason)
    reviewer = _required_text("reviewer", reviewer)
    corrected_at = _required_text("corrected_at", corrected_at or _utc_now())

    outputs_dir = Path(job_dir) / "outputs"
    canonical_path = outputs_dir / "full_transcript.json"
    payload = _load_canonical(canonical_path)
    segments = _validated_segments(payload)

    target = next((segment for segment in segments if segment["segment_id"] == segment_id), None)
    if target is None:
        raise ValueError(f"Cannot correct unknown segment_id: {segment_id}")

    original_text = str(target.get("text") or "")
    original_hash = _sha256(original_text)
    if expected_original_sha256 is not None and expected_original_sha256 != original_hash:
        raise ValueError(
            "Correction rejected because the expected original hash is stale or does not match"
        )

    history = payload.get("correction_history", [])
    if not isinstance(history, list):
        raise ValueError("Canonical transcript correction_history must be a list")

    updated = deepcopy(payload)
    updated_target = next(
        segment for segment in updated["segments"] if segment["segment_id"] == segment_id
    )
    updated_target["text"] = accepted_text

    event: dict[str, Any] = {
        "corrected_at": corrected_at,
        "reviewer": reviewer,
        "reason": reason,
        "segment_id": segment_id,
        "original_text": original_text,
        "accepted_text": accepted_text,
        "original_text_sha256": original_hash,
        "accepted_text_sha256": _sha256(accepted_text),
        "source_provider": target.get("provider"),
        "source_model": target.get("model"),
        "source_run_id": target.get("run_id"),
        "source_start_sec": target.get("start_sec"),
        "source_end_sec": target.get("end_sec"),
    }
    event["correction_id"] = _correction_id(event)
    updated.setdefault("correction_history", []).append(event)
    _rebuild_text(updated)

    canonical_json = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(canonical_path, canonical_json)
    _atomic_write_text(outputs_dir / "full_transcript.txt", updated["text"])
    write_transcript_exports(updated, outputs_dir)

    return deepcopy(event)
