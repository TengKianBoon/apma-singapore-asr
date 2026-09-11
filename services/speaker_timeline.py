"""Provider-native Gem35T speaker evidence on APMA's global timeline."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import html
import json
from pathlib import Path
from typing import Any, Iterable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _quality_evidence(paths: Iterable[Path]) -> list[dict[str, Any]]:
    result = []
    for value in paths:
        path = Path(value).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Quality transcript evidence not found: {path}")
        result.append(
            {
                "path": str(path),
                "sha256": _sha256(path),
                "modified": False,
            }
        )
    return result


def _chunks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    chunks = payload.get("chunks")
    if isinstance(chunks, list):
        return [item for item in chunks if isinstance(item, dict)]
    return [payload]


def _speaker_id(word: dict[str, Any]) -> str | None:
    value = word.get("provider_speaker", word.get("speaker"))
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _segment_text(words: list[dict[str, Any]]) -> str:
    """Join unchanged provider word text without inserting spaces into Chinese."""

    output = ""
    for item in words:
        raw = item.get("raw_provider_annotation")
        value = (
            raw.get("text")
            if isinstance(raw, dict) and isinstance(raw.get("text"), str)
            else item.get("text")
        )
        piece = str(value or "")
        if not piece:
            continue
        needs_space = bool(
            output
            and output[-1].isascii()
            and output[-1].isalnum()
            and piece[0].isascii()
            and piece[0].isalnum()
        )
        output += (" " if needs_space else "") + piece
    return output.strip()


def build_speaker_timeline(
    gem35_payload: dict[str, Any],
    *,
    source_sha256: str,
    quality_evidence_paths: Iterable[Path] = (),
) -> dict[str, Any]:
    """Build deterministic turns from provider-native words without naming speakers."""

    words: list[dict[str, Any]] = []
    for chunk_index, chunk in enumerate(_chunks(gem35_payload)):
        if str(chunk.get("provider_code") or "") != "Gem35T":
            raise ValueError("Speaker timeline input must be retained Gem35T evidence")
        timing = chunk.get("timing") if isinstance(chunk.get("timing"), dict) else {}
        native = (
            timing.get("provider_native")
            if isinstance(timing.get("provider_native"), dict)
            else {}
        )
        for word_index, source_word in enumerate(native.get("words") or []):
            if not isinstance(source_word, dict):
                continue
            start = float(source_word["global_start_sec"])
            end = float(source_word["global_end_sec"])
            local_start = float(source_word["local_start_sec"])
            local_end = float(source_word["local_end_sec"])
            if start < 0 or end <= start or local_start < 0 or local_end <= local_start:
                raise ValueError("Gem35T returned an invalid provider-native word timestamp")
            item = deepcopy(source_word)
            raw_annotation = item.get("raw_provider_annotation")
            if isinstance(raw_annotation, dict) and isinstance(
                raw_annotation.get("text"), str
            ):
                item["text"] = raw_annotation["text"]
            item.update(
                {
                    "word_id": f"word-{len(words) + 1:05d}",
                    "speaker_id": _speaker_id(source_word),
                    "provider": "google",
                    "provider_code": "Gem35T",
                    "model": chunk.get("model"),
                    "run_id": chunk.get("run_id"),
                    "chunk_filename": chunk.get("chunk_filename"),
                    "provider_artifact_path": chunk.get("provider_artifact_path"),
                    "source_chunk_index": chunk_index,
                    "source_word_index": word_index,
                    "timing_source": "provider_native_word",
                }
            )
            words.append(item)

    words.sort(
        key=lambda item: (
            float(item["global_start_sec"]),
            float(item["global_end_sec"]),
            item["word_id"],
        )
    )
    for previous, current in zip(words, words[1:]):
        if float(current["global_start_sec"]) < float(previous["global_start_sec"]):
            raise ValueError("Gem35T word timestamps are not globally ordered")

    segments: list[dict[str, Any]] = []
    for word in words:
        same_turn = bool(
            segments
            and segments[-1]["speaker_id"] == word["speaker_id"]
            and segments[-1]["run_id"] == word.get("run_id")
            and segments[-1]["chunk_filename"] == word.get("chunk_filename")
        )
        if not same_turn:
            segments.append(
                {
                    "segment_id": f"speaker-segment-{len(segments) + 1:05d}",
                    "speaker_id": word["speaker_id"],
                    "global_start_sec": word["global_start_sec"],
                    "global_end_sec": word["global_end_sec"],
                    "local_start_sec": word["local_start_sec"],
                    "local_end_sec": word["local_end_sec"],
                    "text": "",
                    "text_derivation": "language_aware_join_of_unchanged_provider_word_annotations",
                    "words": [],
                    "provider": "google",
                    "provider_code": "Gem35T",
                    "model": word.get("model"),
                    "run_id": word.get("run_id"),
                    "chunk_filename": word.get("chunk_filename"),
                    "provider_artifact_path": word.get("provider_artifact_path"),
                    "speaker_identity_scope": "provider_native_single_clip",
                    "speaker_name": None,
                }
            )
        segment = segments[-1]
        segment["words"].append(deepcopy(word))
        segment["global_end_sec"] = word["global_end_sec"]
        segment["local_end_sec"] = word["local_end_sec"]
        segment["text"] = _segment_text(segment["words"])

    speaker_ids: list[str] = []
    for word in words:
        speaker = word["speaker_id"]
        if speaker is not None and speaker not in speaker_ids:
            speaker_ids.append(speaker)

    return {
        "schema_version": "apma.speaker-timeline.v1",
        "purpose": "Gem35T provider-native speaker and timing evidence only.",
        "source_gem35t_sha256": source_sha256,
        "speaker_policy": {
            "identity_source": "provider_native",
            "identity_scope": "provider_native_single_clip",
            "speaker_names_invented": False,
            "cross_chunk_identity_attempted": False,
            "quality_transcript_replaced": False,
        },
        "quality_transcript_integrity": _quality_evidence(quality_evidence_paths),
        "statistics": {
            "speaker_count": len(speaker_ids),
            "speaker_ids": speaker_ids,
            "word_count": len(words),
            "segment_count": len(segments),
        },
        "words": words,
        "segments": segments,
    }


def attach_speaker_evidence_to_final(
    final_payload: dict[str, Any], speaker_timeline: dict[str, Any]
) -> dict[str, Any]:
    """Return a derived FINAL copy with overlapping evidence; never alter final text."""

    derived = deepcopy(final_payload)
    segments = speaker_timeline.get("segments") or []
    for window in derived.get("windows") or []:
        start = float(window["global_start_sec"])
        end = float(window["global_end_sec"])
        overlaps = [
            deepcopy(segment)
            for segment in segments
            if float(segment["global_end_sec"]) > start
            and float(segment["global_start_sec"]) < end
        ]
        speaker_ids: list[str] = []
        for segment in overlaps:
            speaker = segment.get("speaker_id")
            if speaker and speaker not in speaker_ids:
                speaker_ids.append(speaker)
        window["speaker_evidence"] = {
            "provider_code": "Gem35T",
            "evidence_only": True,
            "speaker_identity_scope": "provider_native_single_clip",
            "speaker_ids": speaker_ids,
            "segments": overlaps,
            "quality_text_overwritten": False,
        }
    derived["speaker_evidence"] = {
        "source_speaker_timeline_sha256": hashlib.sha256(
            json.dumps(
                speaker_timeline, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest(),
        "provider_code": "Gem35T",
        "evidence_only": True,
        "quality_text_overwritten": False,
    }
    return derived


def _format_time(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_speaker_timeline(
    timeline: dict[str, Any], output_dir: Path
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "speaker_timeline.json"
    html_path = output_dir / "speaker_timeline.html"
    json_path.write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
    rows = []
    for segment in timeline.get("segments") or []:
        speaker = segment.get("speaker_id") or "UNLABELLED BY PROVIDER"
        rows.append(
            "<tr><td>"
            + html.escape(_format_time(segment["global_start_sec"]))
            + "<br>to<br>"
            + html.escape(_format_time(segment["global_end_sec"]))
            + "</td><td>"
            + html.escape(str(speaker))
            + "</td><td><pre>"
            + html.escape(str(segment.get("text") or ""))
            + "</pre></td></tr>"
        )
    page = (
        '<!doctype html><html><head><meta charset="utf-8"><title>APMA Speaker Timeline</title>'
        "<style>body{font-family:Arial,sans-serif;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%}th,td{border:1px solid #aeb6bf;padding:10px;vertical-align:top}th{background:#1f4e78;color:#fff}pre{white-space:pre-wrap;word-break:break-word;margin:0;font:14px/1.45 Arial,sans-serif}.note{background:#eef3f8;padding:12px;border-left:4px solid #1f4e78}</style>"
        "</head><body><h1>APMA Gem35T Speaker Timeline</h1>"
        '<p class="note">Speaker IDs and word times are provider-native evidence. They are not names, are not mapped across chunks, and do not replace APMA quality transcript wording.</p>'
        "<table><thead><tr><th>TIME</th><th>SPEAKER</th><th>TEXT</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )
    html_path.write_text(page, encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


__all__ = [
    "attach_speaker_evidence_to_final",
    "build_speaker_timeline",
    "write_speaker_timeline",
]
