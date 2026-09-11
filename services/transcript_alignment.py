"""Deterministic timestamp-only alignment of retained provider transcripts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import escape
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "apma.transcript-comparison.v1"
PROVIDER_ORDER = ("M3ASR", "gptTr", "Gem35T")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chunk_bounds(chunk: dict[str, Any]) -> tuple[float | None, float | None]:
    start = _number(chunk.get("chunk_start_sec", chunk.get("start_sec")))
    end = _number(chunk.get("chunk_end_sec", chunk.get("end_sec")))
    if end is None and start is not None:
        duration = _number(chunk.get("duration_seconds"))
        if duration is not None:
            end = start + duration
    return start, end


def _segment_bounds(
    segment: dict[str, Any],
    chunk: dict[str, Any],
    *,
    root_segment: bool,
) -> tuple[float | None, float | None]:
    start = _number(segment.get("global_start_sec"))
    end = _number(segment.get("global_end_sec"))
    if start is not None and end is not None:
        return start, end

    start = _number(segment.get("start_sec", segment.get("start")))
    end = _number(segment.get("end_sec", segment.get("end")))
    if start is None or end is None:
        return None, None

    scope = str(
        segment.get("segment_timing_scope")
        or chunk.get("segment_timing_scope")
        or ""
    ).strip().lower()
    if not root_segment and scope in {"chunk", "chunk_relative", "relative"}:
        chunk_start, _ = _chunk_bounds(chunk)
        if chunk_start is None:
            return None, None
        start += chunk_start
        end += chunk_start
    return start, end


def _validate_bounds(
    start: float,
    end: float,
    recording_duration_seconds: float,
    candidate_id: str,
) -> None:
    tolerance = 1e-6
    if start < -tolerance or end < start - tolerance:
        raise ValueError(f"Invalid timestamp interval for {candidate_id}")
    if end > recording_duration_seconds + tolerance:
        raise ValueError(f"Timestamp exceeds recording duration for {candidate_id}")


def normalize_provider_segments(
    provider: str,
    source_json: Path,
    *,
    timeline_offset_seconds: float,
    recording_duration_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Normalize retained segments, falling back truthfully to timed chunks."""

    source_json = Path(source_json).resolve()
    with source_json.open("r", encoding="utf-8") as source:
        payload = json.load(source)
    if not isinstance(payload, dict):
        raise ValueError(f"Provider JSON must contain an object: {source_json}")

    chunks = payload.get("chunks") or []
    if not isinstance(chunks, list):
        raise ValueError(f"Provider JSON chunks must be a list: {source_json}")
    root_segments = payload.get("segments") or []
    if not isinstance(root_segments, list):
        root_segments = []

    root_by_chunk: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    ungrouped_root: list[tuple[int, dict[str, Any]]] = []
    for root_index, segment in enumerate(root_segments):
        if not isinstance(segment, dict):
            continue
        chunk_filename = segment.get("chunk_filename")
        if chunk_filename:
            root_by_chunk.setdefault(str(chunk_filename), []).append(
                (root_index, segment)
            )
        else:
            ungrouped_root.append((root_index, segment))

    normalized: list[dict[str, Any]] = []
    used_root_indexes: set[int] = set()
    source_hash = _sha256(source_json)

    def add_candidate(
        *,
        text: Any,
        local_start: float,
        local_end: float,
        chunk: dict[str, Any],
        source_kind: str,
        source_index: int,
        segment_id: Any = None,
    ) -> None:
        candidate_id = f"{provider}:{len(normalized) + 1:05d}"
        global_start = float(timeline_offset_seconds) + float(local_start)
        global_end = float(timeline_offset_seconds) + float(local_end)
        _validate_bounds(
            global_start,
            global_end,
            float(recording_duration_seconds),
            candidate_id,
        )
        normalized.append(
            {
                "candidate_id": candidate_id,
                "provider": provider,
                "global_start_sec": global_start,
                "global_end_sec": global_end,
                "text": "" if text is None else str(text),
                "provenance": {
                    "source_json": str(source_json),
                    "source_sha256": source_hash,
                    "source_kind": source_kind,
                    "source_index": source_index,
                    "job_id": payload.get("job_id"),
                    "chunk_filename": chunk.get("chunk_filename")
                    or chunk.get("filename"),
                    "segment_id": segment_id,
                    "provider_code": chunk.get("provider_code"),
                    "model": chunk.get("model") or payload.get("model"),
                    "run_id": chunk.get("run_id"),
                    "transcript_path": chunk.get("transcript_path"),
                    "provider_artifact_path": chunk.get("provider_artifact_path"),
                },
            }
        )

    for chunk_index, chunk in enumerate(chunks):
        if not isinstance(chunk, dict):
            continue
        filename = str(chunk.get("chunk_filename") or chunk.get("filename") or "")
        timed_root: list[tuple[int, dict[str, Any], float, float]] = []
        for root_index, segment in root_by_chunk.get(filename, []):
            start, end = _segment_bounds(segment, chunk, root_segment=True)
            if start is not None and end is not None:
                timed_root.append((root_index, segment, start, end))

        if timed_root:
            for root_index, segment, start, end in timed_root:
                used_root_indexes.add(root_index)
                add_candidate(
                    text=segment.get("text"),
                    local_start=start,
                    local_end=end,
                    chunk=chunk,
                    source_kind="root_segment",
                    source_index=root_index,
                    segment_id=segment.get("segment_id"),
                )
            continue

        embedded = chunk.get("segments") or chunk.get("utterances") or []
        timed_embedded: list[tuple[int, dict[str, Any], float, float]] = []
        if isinstance(embedded, list):
            for segment_index, segment in enumerate(embedded):
                if not isinstance(segment, dict):
                    continue
                start, end = _segment_bounds(segment, chunk, root_segment=False)
                if start is not None and end is not None:
                    timed_embedded.append((segment_index, segment, start, end))

        if timed_embedded:
            for segment_index, segment, start, end in timed_embedded:
                add_candidate(
                    text=segment.get("text"),
                    local_start=start,
                    local_end=end,
                    chunk=chunk,
                    source_kind="chunk_segment",
                    source_index=segment_index,
                    segment_id=segment.get("segment_id"),
                )
            continue

        start, end = _chunk_bounds(chunk)
        if start is None or end is None:
            raise ValueError(
                f"Chunk lacks usable timestamps for {provider}: {filename or chunk_index}"
            )
        add_candidate(
            text=chunk.get("text"),
            local_start=start,
            local_end=end,
            chunk=chunk,
            source_kind="chunk_fallback",
            source_index=chunk_index,
        )

    empty_chunk: dict[str, Any] = {}
    for root_index, segment in ungrouped_root:
        if root_index in used_root_indexes:
            continue
        start, end = _segment_bounds(segment, empty_chunk, root_segment=True)
        if start is None or end is None:
            continue
        add_candidate(
            text=segment.get("text"),
            local_start=start,
            local_end=end,
            chunk=empty_chunk,
            source_kind="root_segment",
            source_index=root_index,
            segment_id=segment.get("segment_id"),
        )

    normalized.sort(
        key=lambda candidate: (
            candidate["global_start_sec"],
            candidate["global_end_sec"],
            candidate["candidate_id"],
        )
    )
    return normalized, {
        "source_json": str(source_json),
        "source_sha256": source_hash,
        "job_id": payload.get("job_id"),
        "segment_count": len(normalized),
    }


def _overlap_seconds(candidate: dict[str, Any], region: dict[str, Any]) -> float:
    return max(
        0.0,
        min(candidate["global_end_sec"], region["global_end_sec"])
        - max(candidate["global_start_sec"], region["global_start_sec"]),
    )


def align_segments(
    segments_by_provider: dict[str, list[dict[str, Any]]],
    provider_order: tuple[str, ...] = PROVIDER_ORDER,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Align once-only candidates by maximum positive timestamp overlap."""

    regions: list[dict[str, Any]] = []
    for provider in provider_order:
        for candidate in segments_by_provider.get(provider, []):
            matches = []
            candidate_midpoint = (
                candidate["global_start_sec"] + candidate["global_end_sec"]
            ) / 2.0
            for creation_index, region in enumerate(regions):
                if provider in region["assigned"]:
                    continue
                overlap = _overlap_seconds(candidate, region)
                if overlap <= 0:
                    continue
                region_midpoint = (
                    region["global_start_sec"] + region["global_end_sec"]
                ) / 2.0
                matches.append(
                    (
                        overlap,
                        -abs(candidate_midpoint - region_midpoint),
                        -creation_index,
                        region,
                    )
                )
            if matches:
                region = max(matches, key=lambda item: item[:3])[3]
                region["global_start_sec"] = min(
                    region["global_start_sec"], candidate["global_start_sec"]
                )
                region["global_end_sec"] = max(
                    region["global_end_sec"], candidate["global_end_sec"]
                )
            else:
                region = {
                    "global_start_sec": candidate["global_start_sec"],
                    "global_end_sec": candidate["global_end_sec"],
                    "assigned": {},
                }
                regions.append(region)
            region["assigned"][provider] = candidate

    regions.sort(
        key=lambda region: (
            region["global_start_sec"],
            region["global_end_sec"],
            min(
                candidate["candidate_id"]
                for candidate in region["assigned"].values()
            ),
        )
    )
    represented: list[str] = []
    unaligned: list[str] = []
    output_regions: list[dict[str, Any]] = []
    for index, region in enumerate(regions, start=1):
        assigned = region["assigned"]
        candidates: dict[str, Any] = {}
        for provider in provider_order:
            candidate = assigned.get(provider)
            if candidate is None:
                candidates[provider] = {
                    "missing": True,
                    "text": None,
                    "provenance": None,
                }
            else:
                represented.append(candidate["candidate_id"])
                candidates[provider] = {"missing": False, **candidate}
        if len(assigned) == 1:
            unaligned.extend(
                candidate["candidate_id"] for candidate in assigned.values()
            )
        output_regions.append(
            {
                "region_id": f"region-{index:05d}",
                "global_start_sec": region["global_start_sec"],
                "global_end_sec": region["global_end_sec"],
                "candidates": candidates,
            }
        )

    expected = sorted(
        candidate["candidate_id"]
        for provider in provider_order
        for candidate in segments_by_provider.get(provider, [])
    )
    if sorted(represented) != expected or len(represented) != len(set(represented)):
        raise ValueError("Alignment did not represent every candidate exactly once")
    return output_regions, sorted(unaligned)


def build_comparison(
    provider_sources: dict[str, Path],
    *,
    timeline_offset_seconds: float,
    recording_duration_seconds: float,
    provider_order: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    resolved_provider_order = tuple(provider_order or PROVIDER_ORDER)
    if not resolved_provider_order or len(set(resolved_provider_order)) != len(
        resolved_provider_order
    ):
        raise ValueError("Provider order must contain unique provider codes")
    if tuple(provider_sources) != resolved_provider_order:
        raise ValueError(
            f"Providers must be supplied in order: {', '.join(resolved_provider_order)}"
        )
    if recording_duration_seconds <= 0:
        raise ValueError("Recording duration must be positive")

    segments_by_provider: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, dict[str, Any]] = {}
    for provider in resolved_provider_order:
        segments, source = normalize_provider_segments(
            provider,
            provider_sources[provider],
            timeline_offset_seconds=timeline_offset_seconds,
            recording_duration_seconds=recording_duration_seconds,
        )
        segments_by_provider[provider] = segments
        sources[provider] = source

    regions, unaligned = align_segments(segments_by_provider, resolved_provider_order)
    total_segments = sum(len(items) for items in segments_by_provider.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _utc_now(),
        "alignment_method": "maximum_positive_global_timestamp_overlap",
        "timeline": {
            "basis": "original_recording_seconds",
            "offset_seconds": float(timeline_offset_seconds),
            "recording_duration_seconds": float(recording_duration_seconds),
        },
        "provider_order": list(resolved_provider_order),
        "sources": sources,
        "statistics": {
            "source_segments": {
                provider: len(segments_by_provider[provider])
                for provider in resolved_provider_order
            },
            "total_source_segments": total_segments,
            "represented_segments": total_segments,
            "aligned_regions": len(regions),
            "unaligned_segments": len(unaligned),
        },
        "unaligned_segment_ids": unaligned,
        "regions": regions,
    }


def _format_timestamp(seconds: float) -> str:
    milliseconds = round(float(seconds) * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def comparison_html(comparison: dict[str, Any]) -> str:
    classified = bool(comparison.get("agreement_analysis"))
    provider_order = tuple(comparison.get("provider_order") or PROVIDER_ORDER)
    rows = []
    for region in comparison["regions"]:
        cells = [
            "<td class=\"time\">"
            + escape(_format_timestamp(region["global_start_sec"]))
            + "<br>to<br>"
            + escape(_format_timestamp(region["global_end_sec"]))
            + "</td>"
        ]
        if classified:
            status = str(region.get("agreement_status") or "RED")
            cells.append(
                '<td class="status"><span class="badge '
                + escape(status.lower())
                + '">'
                + escape(status)
                + '</span><div class="reason">'
                + escape(" ".join(region.get("agreement_reasons") or []))
                + "</div></td>"
            )
        for provider in provider_order:
            candidate = region["candidates"][provider]
            if candidate["missing"]:
                cells.append(
                    '<td><span class="missing">Missing / not aligned</span></td>'
                )
                continue
            provenance = candidate["provenance"]
            detail = " · ".join(
                value
                for value in (
                    str(provenance.get("chunk_filename") or ""),
                    str(provenance.get("model") or ""),
                )
                if value
            )
            cells.append(
                '<td><div class="text">'
                + escape(candidate["text"])
                + '</div><div class="provenance">'
                + escape(detail or candidate["candidate_id"])
                + "</div></td>"
            )
        rows.append("<tr>" + "".join(cells) + "</tr>")

    counts = comparison["statistics"]["source_segments"]
    summary = " · ".join(f"{provider}: {counts[provider]}" for provider in provider_order)
    if classified:
        status_counts = comparison["agreement_analysis"]["status_counts"]
        summary += (
            " · DERIVED agreement only — GREEN: "
            + str(status_counts["GREEN"])
            + " · AMBER: "
            + str(status_counts["AMBER"])
            + " · RED: "
            + str(status_counts["RED"])
        )
    headers = "<th>Time</th>"
    if classified:
        headers += "<th>Derived agreement</th>"
    headers += "".join(f"<th>{escape(provider)}</th>" for provider in provider_order)
    banner = (
        "DERIVED ASR agreement/disagreement only; not transcription correctness, "
        "not provider confidence, and not winner selection."
        if classified
        else "No scoring or winner selection."
    )
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>APMA Multi-ASR Timeline Comparison</title>
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f6f7f9;color:#172033}
h1{margin-bottom:6px}.summary{color:#526079;margin-bottom:18px}
table{width:100%;border-collapse:collapse;table-layout:fixed;background:white}
th,td{border:1px solid #d8dde8;padding:12px;vertical-align:top}
th{background:#24314d;color:white;position:sticky;top:0}.time{width:120px;font-variant-numeric:tabular-nums}
.text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.45}.provenance{margin-top:10px;color:#69758c;font-size:.82rem}
.missing{color:#8a4b18;font-style:italic}tr:nth-child(even){background:#f9fafc}
.status{width:190px}.badge{display:inline-block;padding:4px 9px;border-radius:999px;font-weight:700;color:white}
.badge.green{background:#257942}.badge.amber{background:#a86500}.badge.red{background:#ad2e32}.reason{margin-top:8px;font-size:.82rem;color:#526079}
</style>
</head>
<body>
<h1>APMA Multi-ASR Timeline Comparison</h1>
<div class="summary">""" + escape(banner) + " " + escape(summary) + """</div>
<table>
<thead><tr>""" + headers + """</tr></thead>
<tbody>""" + "".join(rows) + """</tbody>
</table>
</body>
</html>
"""


def write_comparison(
    comparison: dict[str, Any], output_dir: Path
) -> tuple[Path, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "comparison.json"
    html_path = output_dir / "comparison.html"
    json_path.write_text(
        json.dumps(comparison, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    html_path.write_text(comparison_html(comparison), encoding="utf-8")
    return json_path, html_path


__all__ = [
    "PROVIDER_ORDER",
    "SCHEMA_VERSION",
    "align_segments",
    "build_comparison",
    "comparison_html",
    "normalize_provider_segments",
    "write_comparison",
]
