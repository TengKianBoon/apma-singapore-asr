from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from services.final_draft import (
    build_final_draft,
    select_deterministic_representative,
    write_final_draft_artifacts,
)


PROVIDERS = ("M3ASR", "gptTr", "Gem35T")


def _candidate(provider: str, text: str, clip_sha: str) -> dict:
    return {
        "missing": False,
        "text": text,
        "provider": provider,
        "provider_code": provider,
        "model": f"model-{provider}",
        "run_id": f"run-{provider}",
        "clip_id": "clip-1",
        "clip_sha256": clip_sha,
        "global_start_sec": 915.0,
        "global_end_sec": 939.0,
        "timing_authority": "application_owned_audio_clip_bounds",
        "provider_timestamp_used": False,
        "transcript_ref": f"transcripts/{provider}.json",
        "provider_response_ref": f"providers/{provider}.json",
    }


def _region(index: int, status: str, texts: dict[str, str]) -> dict:
    start = 915.0 + (index - 1) * 24.0
    end = start + 24.0
    return {
        "region_id": f"rescue-window-{index:05d}",
        "clip_id": f"clip-{index}",
        "clip_sha256": f"sha-{index}",
        "global_start_sec": start,
        "global_end_sec": end,
        "duration_seconds": 24.0,
        "timing_authority": "application_owned_audio_clip_bounds",
        "boundary": {"strategy": "silence"},
        "agreement_status": status,
        "candidates": {
            provider: _candidate(provider, texts[provider], f"sha-{index}")
            for provider in PROVIDERS
        },
    }


def _source() -> dict:
    common = {
        "M3ASR": "alpha beta",
        "gptTr": "alpha beta gamma",
        "Gem35T": "alpha beta gamma delta",
    }
    return {
        "schema_version": "apma.rescue-comparison.v1",
        "regions": [
            _region(1, "GREEN", common),
            _region(2, "AMBER", common),
            _region(3, "RED", common),
        ],
    }


def test_representative_is_highest_mean_similarity_with_deterministic_tie_break():
    region = _source()["regions"][0]
    provider, candidate, scores = select_deterministic_representative(region)
    expected = min(PROVIDERS, key=lambda item: (-scores[item], PROVIDERS.index(item)))
    assert provider == expected
    assert candidate is region["candidates"][provider]


def test_only_green_is_auto_accepted_and_text_is_exact_source_candidate():
    source = _source()
    untouched = deepcopy(source)
    first = build_final_draft(source, source_sha256="source-sha")
    second = build_final_draft(source, source_sha256="source-sha")

    assert first == second
    assert source == untouched
    assert first["statistics"] == {
        "total_windows": 3,
        "auto_accepted": 1,
        "review_required": 2,
        "status_counts": {"GREEN": 1, "AMBER": 1, "RED": 1},
    }
    green = first["windows"][0]
    assert green["selection_method"] == "deterministic_representative"
    assert green["auto_accepted"] is True
    assert green["review_required"] is False
    assert green["final_text"] == green["provider_candidates"][green["selected_provider"]]["text"]
    assert green["selected_text"] == source["regions"][0]["candidates"][green["selected_provider"]]["text"]
    assert green["selected_candidate_provenance"]["provider_response_ref"]
    assert green["correctness_claimed"] is False

    for window in first["windows"][1:]:
        assert window["final_text"] is None
        assert window["selected_provider"] is None
        assert window["auto_accepted"] is False
        assert window["review_required"] is True
        assert window["provider_candidates"] == source["regions"][
            int(window["window_id"].rsplit("-", 1)[1]) - 1
        ]["candidates"]


def test_global_timestamps_and_window_identity_are_preserved_exactly():
    source = _source()
    draft = build_final_draft(source, source_sha256="source-sha")
    assert [
        (window["window_id"], window["global_start_sec"], window["global_end_sec"])
        for window in draft["windows"]
    ] == [
        (region["region_id"], region["global_start_sec"], region["global_end_sec"])
        for region in source["regions"]
    ]


def test_duplicate_windows_are_rejected():
    source = _source()
    source["regions"][1]["region_id"] = source["regions"][0]["region_id"]
    with pytest.raises(ValueError, match="unique region_id"):
        build_final_draft(source, source_sha256="source-sha")


def test_json_and_html_are_deterministic_and_show_review_state(tmp_path):
    draft = build_final_draft(_source(), source_sha256="source-sha")
    first = write_final_draft_artifacts(draft, tmp_path / "first")
    second = write_final_draft_artifacts(draft, tmp_path / "second")
    first_json = Path(first["json"]).read_bytes()
    second_json = Path(second["json"]).read_bytes()
    first_html = Path(first["html"]).read_bytes()
    second_html = Path(second["html"]).read_bytes()
    assert first_json == second_json
    assert first_html == second_html
    assert json.loads(first_json)["windows"]
    page = first_html.decode("utf-8")
    assert "FINAL DRAFT / REVIEW REQUIRED" in page
    assert "AUTO-ACCEPTED EXISTING CANDIDATE" in page
    assert page.count("REVIEW REQUIRED") >= 2
    assert all(provider in page for provider in PROVIDERS)
