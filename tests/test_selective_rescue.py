from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import wave

import pytest

from services.config import Config
from services.selective_rescue import (
    PROVIDER_ORDER,
    build_review_comparison_from_regions,
    build_rescue_comparison,
    build_rescue_windows,
    estimate_live_costs,
    select_first_red_region,
    write_rescue_artifacts,
)


def _write_wav(path: Path, duration_sec: float = 120.0, rate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        frames = bytearray()
        for index in range(int(duration_sec * rate)):
            value = int(2000 * math.sin(2 * math.pi * 220 * index / rate))
            frames.extend(struct.pack("<h", value))
        wav_file.writeframes(bytes(frames))


def _comparison() -> dict:
    return {
        "regions": [
            {
                "region_id": "region-00001",
                "global_start_sec": 915.0,
                "global_end_sec": 1035.0,
                "agreement_status": "RED",
                "agreement_reasons": ["fixture disagreement"],
            },
            {
                "region_id": "region-00002",
                "global_start_sec": 1031.0,
                "global_end_sec": 1151.0,
                "agreement_status": "AMBER",
            },
        ]
    }


def test_selects_first_existing_red_region_without_mutation():
    comparison = _comparison()
    selected = select_first_red_region(comparison)
    selected["region_id"] = "changed"
    assert comparison["regions"][0]["region_id"] == "region-00001"


def test_builds_five_silence_bounded_application_timed_windows(tmp_path, monkeypatch):
    source = tmp_path / "authorized.wav"
    _write_wav(source)
    monkeypatch.setattr(
        "services.chunker._detect_silences",
        lambda *_args, **_kwargs: [
            (23.0, 24.5),
            (47.0, 48.5),
            (71.0, 72.5),
            (95.0, 96.5),
        ],
    )
    monkeypatch.setattr(
        "services.selective_rescue._extract_region_wav",
        lambda _source, output, **_kwargs: output.write_bytes(source.read_bytes()),
    )

    selected, windows, identity = build_rescue_windows(
        source,
        _comparison(),
        tmp_path / "proof",
        Config(ffmpeg_binary="ffmpeg"),
        source_global_start_sec=915.0,
    )

    assert selected["region_id"] == "region-00001"
    assert len(windows) == 5
    assert windows[0]["global_start_sec"] == 915.0
    assert windows[-1]["global_end_sec"] == 1035.0
    assert all(15.0 <= item["duration_seconds"] <= 30.0 for item in windows)
    assert all(item["timing_authority"] == "application_owned_audio_clip_bounds" for item in windows)
    assert all(item["boundary"]["strategy"] == "silence" for item in windows[:-1])
    assert len({item["clip_id"] for item in windows}) == 5
    assert identity["source_audio_sha256"]


def test_builds_exact_review_clips_when_comparison_has_no_red(tmp_path):
    source = tmp_path / "authorized.wav"
    _write_wav(source, duration_sec=4.0)
    candidates = {
        provider: {"missing": False, "text": "same retained text", "provider": provider}
        for provider in ("M3ASR", "gptTr", "Gem35T")
    }
    comparison = {
        "schema_version": "apma.comparison.v1",
        "statistics": {
            "source_segments": {"M3ASR": 2, "gptTr": 2, "Gem35T": 2}
        },
        "agreement_analysis": {
            "status_counts": {"GREEN": 2, "AMBER": 0, "RED": 0}
        },
        "regions": [
            {
                "region_id": "region-00001",
                "global_start_sec": 10.0,
                "global_end_sec": 12.0,
                "agreement_status": "GREEN",
                "candidates": candidates,
            },
            {
                "region_id": "region-00002",
                "global_start_sec": 12.0,
                "global_end_sec": 14.0,
                "agreement_status": "GREEN",
                "candidates": candidates,
            },
        ],
    }

    result = build_review_comparison_from_regions(
        source,
        comparison,
        tmp_path / "review",
        Config(ffmpeg_binary="ffmpeg"),
        source_global_start_sec=10.0,
        source_sha256="comparison-sha",
    )

    assert result["provider_calls"] == 0
    assert result["source_comparison"] == {
        "sha256": "comparison-sha",
        "modified": False,
    }
    assert [item["region_id"] for item in result["regions"]] == [
        "region-00001",
        "region-00002",
    ]
    assert all(item["clip_sha256"] for item in result["regions"])
    assert all(
        item["timing_authority"] == "application_owned_audio_clip_bounds"
        for item in result["regions"]
    )
    assert len(list((tmp_path / "review" / "chunks").glob("*.wav"))) == 2
    assert comparison["regions"][0].get("clip_sha256") is None


def test_combined_cost_cap_is_enforced(monkeypatch):
    windows = [{"duration_seconds": 24.0} for _ in range(5)]
    cfg = Config(
        enable_live_meralion_transcription=True,
        enable_live_openai_transcription=True,
        enable_live_gemini_transcription=True,
        meralion_api_key="fake",
        openai_api_key="fake",
        gemini_api_key="fake",
        meralion_price_per_minute_usd=1.0,
    )
    costs = estimate_live_costs(windows, cfg, 3.0)
    assert costs["combined_estimated_cost_usd"] < 3.0
    assert all(
        costs["providers"][provider]["estimated_cost_usd"] > 0.0
        for provider in PROVIDER_ORDER
    )
    assert all(
        costs["providers"][provider]["cost_cap_included"] is True
        for provider in PROVIDER_ORDER
    )
    assert costs["paid_provider_estimated_total_usd"] == costs["combined_estimated_cost_usd"]
    assert costs["paid_provider_cost_buffer_percent"] == 15.0
    assert costs["paid_provider_authorization_total_usd"] == pytest.approx(
        costs["combined_estimated_cost_usd"] * 1.15, abs=1e-6
    )
    cfg.openai_price_per_minute["gpt-transcribe"] = 3.0
    cfg.openai_price_per_minute["gpt-4o-transcribe-diarize"] = 3.0
    cfg.gemini_price_per_minute_usd = 3.0
    with pytest.raises(RuntimeError, match="exceeds cap"):
        estimate_live_costs(windows, cfg, 5.0)


def test_comparison_preserves_raw_candidates_and_same_clip_proof(tmp_path):
    windows = []
    for index in range(2):
        windows.append(
            {
                "window_id": f"rescue-window-{index + 1:05d}",
                "clip_id": f"clip-{index + 1}",
                "clip_sha256": f"sha-{index + 1}",
                "global_start_sec": 915.0 + index * 20.0,
                "global_end_sec": 935.0 + index * 20.0,
                "duration_seconds": 20.0,
                "boundary": {"strategy": "silence"},
            }
        )
    texts = {
        "gptTr": ["Hello 世界", "unique gptTr"],
        "gpt4oDiarz": ["Hello 世界", "different OpenAI"],
        "Gem35T": ["Hello 世界", "sangat berbeza sekali"],
    }
    results = {
        provider: [
            {
                "text": text,
                "provider": provider,
                "model": f"model-{provider}",
                "run_id": f"run-{provider}",
                "chunk_sha256": windows[index]["clip_sha256"],
            }
            for index, text in enumerate(provider_texts)
        ]
        for provider, provider_texts in texts.items()
    }
    summaries = {
        provider: {"provider_calls": 2, "processing_seconds": 1.0}
        for provider in texts
    }
    classified = build_rescue_comparison(
        _comparison()["regions"][0],
        windows,
        results,
        summaries,
        {"combined_cap_usd": 3.0, "combined_estimated_cost_usd": 0.1},
        {"source_audio_sha256": "source-sha"},
        tmp_path,
        comparison_source_identity={"sha256": "comparison-sha", "modified": False},
        source_label="authorized fixture",
        total_processing_seconds=3.0,
    )
    assert [classified["regions"][0]["candidates"][p]["text"] for p in texts] == [
        texts[p][0] for p in texts
    ]
    assert classified["regions"][0]["agreement_status"] == "GREEN"
    assert classified["regions"][1]["agreement_status"] in {"AMBER", "RED"}
    for region in classified["regions"]:
        assert len({candidate["clip_sha256"] for candidate in region["candidates"].values()}) == 1
        assert all(not candidate["provider_timestamp_used"] for candidate in region["candidates"].values())

    paths = write_rescue_artifacts(classified, tmp_path)
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))["regions"]
    page = Path(paths["html"]).read_text(encoding="utf-8")
    assert "DERIVED ASR agreement/disagreement only" in page
    assert all(provider in page for provider in PROVIDER_ORDER)
