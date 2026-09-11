from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from services.human_review import REVIEW_HTML
from services.speaker_timeline import (
    attach_speaker_evidence_to_final,
    build_speaker_timeline,
    write_speaker_timeline,
)


def _word(text: str, speaker: str, local_start: float, local_end: float) -> dict:
    raw = {
        "type": "word_info",
        "text": text,
        "start_offset": f"{local_start:.3f}s",
        "end_offset": f"{local_end:.3f}s",
        "speaker": speaker,
    }
    return {
        "type": "provider_native_word",
        "text": text,
        "local_start_sec": local_start,
        "local_end_sec": local_end,
        "global_start_sec": 915.0 + local_start,
        "global_end_sec": 915.0 + local_end,
        "provider_start": raw["start_offset"],
        "provider_end": raw["end_offset"],
        "speaker": speaker,
        "provider_speaker": speaker,
        "raw_provider_annotation": raw,
        "provenance": {"raw_collection": "words", "raw_index": 0},
    }


def _gem35_payload() -> dict:
    words = [
        _word("Hello", "spk_1", 0.1, 0.4),
        _word("你好", "spk_2", 0.5, 0.8),
        _word("again", "spk_1", 0.9, 1.2),
        _word("thanks", "spk_1", 1.3, 1.6),
    ]
    return {
        "schema_version": "apma.retained-transcript.v1",
        "chunks": [
            {
                "provider_code": "Gem35T",
                "model": "gemini-3.5-transcribe",
                "run_id": "gem35t-proof",
                "chunk_filename": "two-speakers.wav",
                "provider_artifact_path": "providers/raw.json",
                "text": "provider wording stays separate",
                "timing": {"provider_native": {"words": words, "segments": []}},
            }
        ],
    }


def test_builds_provider_native_speaker_timeline_and_retains_raw_annotations(tmp_path):
    quality = tmp_path / "quality.json"
    quality.write_text('{"text":"unchanged quality transcript"}', encoding="utf-8")
    quality_hash = hashlib.sha256(quality.read_bytes()).hexdigest()

    timeline = build_speaker_timeline(
        _gem35_payload(),
        source_sha256="gem35-source-sha",
        quality_evidence_paths=[quality],
    )

    assert timeline["statistics"] == {
        "speaker_count": 2,
        "speaker_ids": ["spk_1", "spk_2"],
        "word_count": 4,
        "segment_count": 3,
    }
    assert [segment["speaker_id"] for segment in timeline["segments"]] == [
        "spk_1",
        "spk_2",
        "spk_1",
    ]
    assert timeline["words"][1]["raw_provider_annotation"]["speaker"] == "spk_2"
    assert timeline["words"][0]["global_start_sec"] == 915.1
    assert timeline["words"][-1]["global_end_sec"] == 916.6
    assert timeline["speaker_policy"]["speaker_names_invented"] is False
    assert timeline["quality_transcript_integrity"][0]["sha256"] == quality_hash
    assert quality.read_text(encoding="utf-8") == '{"text":"unchanged quality transcript"}'

    outputs = write_speaker_timeline(timeline, tmp_path / "speaker")
    stored = json.loads(Path(outputs["json"]).read_text(encoding="utf-8"))
    page = Path(outputs["html"]).read_text(encoding="utf-8")
    assert stored["statistics"]["speaker_count"] == 2
    assert "spk_1" in page and "spk_2" in page
    assert "provider-native evidence" in page


def test_final_enrichment_preserves_text_and_multiple_speakers_per_window():
    timeline = build_speaker_timeline(
        _gem35_payload(), source_sha256="gem35-source-sha"
    )
    final_payload = {
        "schema_version": "apma.final-draft.v1",
        "windows": [
            {
                "window_id": "window-1",
                "global_start_sec": 915.0,
                "global_end_sec": 916.0,
                "final_text": "Existing selected quality wording.",
                "provider_candidates": {
                    "M3ASR": {"text": "raw M3"},
                    "gptTr": {"text": "raw gpt"},
                    "Gem35T": {"text": "raw gem"},
                },
            },
            {
                "window_id": "window-2",
                "global_start_sec": 916.0,
                "global_end_sec": 917.0,
                "final_text": None,
                "provider_candidates": {
                    "M3ASR": {"text": "second M3"},
                    "gptTr": {"text": "second gpt"},
                    "Gem35T": {"text": "second gem"},
                },
            },
        ],
    }
    original = deepcopy(final_payload)

    enriched = attach_speaker_evidence_to_final(final_payload, timeline)

    assert final_payload == original
    assert [window["final_text"] for window in enriched["windows"]] == [
        "Existing selected quality wording.",
        None,
    ]
    assert [window["provider_candidates"] for window in enriched["windows"]] == [
        window["provider_candidates"] for window in original["windows"]
    ]
    assert enriched["windows"][0]["speaker_evidence"]["speaker_ids"] == [
        "spk_1",
        "spk_2",
    ]
    assert enriched["windows"][0]["speaker_evidence"]["quality_text_overwritten"] is False
    assert "Gem35T provider-native speaker/timing evidence" in REVIEW_HTML
