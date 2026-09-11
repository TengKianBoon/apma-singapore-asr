import hashlib
import json
from pathlib import Path

import pytest

from services.transcript_alignment import build_comparison, comparison_html


PROVIDERS = ("M3ASR", "gptTr", "Gem35T")


def _write_provider(path: Path, provider: str, chunks, segments=None) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "fixture.v1",
                "job_id": f"job-{provider.lower()}",
                "chunks": chunks,
                "segments": segments or [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _chunk(filename: str, start: float, end: float, text: str) -> dict:
    return {
        "chunk_filename": filename,
        "chunk_start_sec": start,
        "chunk_end_sec": end,
        "provider_code": "fixture",
        "model": "fixture-model",
        "run_id": "fixture-run",
        "text": text,
    }


def test_chunk_fallback_alignment_preserves_text_and_sources(tmp_path):
    source_paths = {}
    source_hashes = {}
    for provider in PROVIDERS:
        source_path = _write_provider(
            tmp_path / f"{provider}.json",
            provider,
            [
                _chunk("chunk-00001.wav", 0.0, 10.0, f"  {provider} 一\nline  "),
                _chunk("chunk-00002.wav", 9.0, 20.0, f"{provider} second"),
            ],
        )
        source_paths[provider] = source_path
        source_hashes[provider] = hashlib.sha256(source_path.read_bytes()).hexdigest()

    comparison = build_comparison(
        source_paths,
        timeline_offset_seconds=100.0,
        recording_duration_seconds=500.0,
    )

    assert comparison["statistics"]["source_segments"] == {
        "M3ASR": 2,
        "gptTr": 2,
        "Gem35T": 2,
    }
    assert comparison["statistics"]["represented_segments"] == 6
    assert comparison["statistics"]["aligned_regions"] == 2
    assert comparison["unaligned_segment_ids"] == []
    assert [region["global_start_sec"] for region in comparison["regions"]] == [
        100.0,
        109.0,
    ]
    assert (
        comparison["regions"][0]["candidates"]["M3ASR"]["text"]
        == "  M3ASR 一\nline  "
    )
    for provider, source_path in source_paths.items():
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hashes[provider]
        assert comparison["sources"][provider]["source_json"] == str(
            source_path.resolve()
        )


def test_missing_and_unaligned_candidates_are_explicit_and_once_only(tmp_path):
    sources = {
        "M3ASR": _write_provider(
            tmp_path / "m3.json",
            "M3ASR",
            [
                _chunk("c1.wav", 0.0, 10.0, "m3 one"),
                _chunk("c2.wav", 10.0, 20.0, "m3 two"),
                _chunk("c3.wav", 30.0, 40.0, "<m3 only>"),
            ],
        ),
        "gptTr": _write_provider(
            tmp_path / "gpt.json",
            "gptTr",
            [_chunk("c1.wav", 0.0, 10.0, "gpt one")],
        ),
        "Gem35T": _write_provider(
            tmp_path / "gem.json",
            "Gem35T",
            [_chunk("c2.wav", 10.0, 20.0, "gem two")],
        ),
    }

    comparison = build_comparison(
        sources,
        timeline_offset_seconds=0.0,
        recording_duration_seconds=60.0,
    )

    assert comparison["statistics"]["aligned_regions"] == 3
    assert comparison["statistics"]["total_source_segments"] == 5
    assert comparison["statistics"]["represented_segments"] == 5
    assert comparison["statistics"]["unaligned_segments"] == 1
    assert comparison["regions"][0]["candidates"]["Gem35T"]["missing"] is True
    assert comparison["regions"][1]["candidates"]["gptTr"]["missing"] is True
    assert comparison["regions"][2]["candidates"]["M3ASR"]["missing"] is False
    assert comparison["regions"][2]["candidates"]["gptTr"]["missing"] is True
    candidate_ids = [
        candidate["candidate_id"]
        for region in comparison["regions"]
        for candidate in region["candidates"].values()
        if not candidate["missing"]
    ]
    assert len(candidate_ids) == len(set(candidate_ids)) == 5

    html = comparison_html(comparison)
    assert "<th>M3ASR</th><th>gptTr</th><th>Gem35T</th>" in html
    assert "Missing / not aligned" in html
    assert "&lt;m3 only&gt;" in html
    assert "<m3 only>" not in html


def test_relative_segments_are_mapped_to_original_recording_timeline(tmp_path):
    m3_chunk = _chunk("shared.wav", 100.0, 120.0, "chunk fallback")
    m3_chunk["segment_timing_scope"] = "chunk"
    m3_chunk["segments"] = [
        {"segment_id": "m3-1", "start_sec": 1.0, "end_sec": 2.0, "text": "m3"}
    ]
    sources = {
        "M3ASR": _write_provider(tmp_path / "m3.json", "M3ASR", [m3_chunk]),
        "gptTr": _write_provider(
            tmp_path / "gpt.json",
            "gptTr",
            [_chunk("shared.wav", 100.0, 120.0, "gpt fallback")],
            segments=[
                {
                    "segment_id": "gpt-1",
                    "chunk_filename": "shared.wav",
                    "start_sec": 101.0,
                    "end_sec": 102.0,
                    "text": "gpt",
                }
            ],
        ),
        "Gem35T": _write_provider(
            tmp_path / "gem.json",
            "Gem35T",
            [_chunk("shared.wav", 100.0, 120.0, "gem")],
        ),
    }

    comparison = build_comparison(
        sources,
        timeline_offset_seconds=900.0,
        recording_duration_seconds=2000.0,
    )

    assert comparison["statistics"]["aligned_regions"] == 1
    region = comparison["regions"][0]
    assert region["global_start_sec"] == 1000.0
    assert region["global_end_sec"] == 1020.0
    assert region["candidates"]["M3ASR"]["global_start_sec"] == 1001.0
    assert region["candidates"]["gptTr"]["global_end_sec"] == 1002.0
    assert region["candidates"]["Gem35T"]["global_end_sec"] == 1020.0


def test_timestamp_outside_original_recording_is_rejected(tmp_path):
    sources = {
        provider: _write_provider(
            tmp_path / f"{provider}.json",
            provider,
            [_chunk("chunk.wav", 0.0, 20.0, provider)],
        )
        for provider in PROVIDERS
    }

    with pytest.raises(ValueError, match="exceeds recording duration"):
        build_comparison(
            sources,
            timeline_offset_seconds=90.0,
            recording_duration_seconds=100.0,
        )
