from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import socket

import pytest

from scripts import local_dashboard
from services.human_review import (
    PROVIDER_ORDER,
    REVIEW_HTML,
    ReviewWorkspace,
    speaker_sensitive_window_ids,
)
from tests.helpers import generate_sine_wav


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(provider: str, text: str, index: int) -> dict:
    return {
        "missing": False,
        "text": text,
        "provider": provider.lower(),
        "provider_code": provider,
        "model": f"model-{provider}",
        "run_id": f"run-{provider}-{index}",
        "clip_id": f"clip-{index}",
        "clip_sha256": f"placeholder-{index}",
        "global_start_sec": 900.0 + index * 20.0,
        "global_end_sec": 920.0 + index * 20.0,
        "timing_authority": "application_owned_audio_clip_bounds",
        "provider_timestamp_used": False,
        "transcript_ref": f"transcripts/{provider}-{index}.json",
        "provider_response_ref": f"providers/{provider}-{index}.json",
    }


def _write_workspace(tmp_path: Path) -> tuple[Path, Path, Path, dict]:
    audio_dir = tmp_path / "audio"
    output_dir = tmp_path / "review"
    audio_dir.mkdir()
    windows = []
    for index in range(1, 6):
        audio_path = audio_dir / f"chunk-{index:05d}.wav"
        generate_sine_wav(str(audio_path), duration_sec=1.0, framerate=8000)
        clip_sha = _sha256(audio_path)
        candidates = {
            provider: _candidate(provider, f"{provider} exact candidate {index}", index)
            for provider in PROVIDER_ORDER
        }
        for candidate in candidates.values():
            candidate["clip_sha256"] = clip_sha
        status = "GREEN" if index == 2 else ("AMBER" if index in {1, 4} else "RED")
        start = 915.0 + (index - 1) * 24.0
        end = start + 24.0
        window = {
            "window_id": f"rescue-window-{index:05d}",
            "global_start_sec": start,
            "global_end_sec": end,
            "duration_seconds": 24.0,
            "derived_status": status,
            "clip_id": f"clip-{index}",
            "clip_sha256": clip_sha,
            "timing_authority": "application_owned_audio_clip_bounds",
            "provider_candidates": candidates,
            "final_text": None,
            "selected_text": None,
            "selected_provider": None,
            "auto_accepted": False,
            "review_required": True,
        }
        if status == "GREEN":
            window.update(
                {
                    "final_text": candidates["gptTr"]["text"],
                    "selected_text": candidates["gptTr"]["text"],
                    "selected_provider": "gptTr",
                    "auto_accepted": True,
                    "review_required": False,
                }
            )
        windows.append(window)
    draft = {
        "schema_version": "apma.final-draft.v1",
        "statistics": {"total_windows": 5, "auto_accepted": 1, "review_required": 4},
        "windows": windows,
    }
    final_draft_path = tmp_path / "FINAL_DRAFT.json"
    final_draft_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")
    return final_draft_path, audio_dir, output_dir, draft


def _resolve_all(workspace: ReviewWorkspace) -> dict:
    workspace.save_decision(
        "rescue-window-00001",
        "provider_candidate",
        selected_provider="M3ASR",
        reviewed_at="2026-08-29T01:00:00Z",
    )
    workspace.save_decision(
        "rescue-window-00003",
        "provider_candidate",
        selected_provider="gptTr",
        reviewed_at="2026-08-29T01:01:00Z",
    )
    workspace.save_decision(
        "rescue-window-00004",
        "provider_candidate",
        selected_provider="Gem35T",
        reviewed_at="2026-08-29T01:02:00Z",
    )
    manual_text = "  Exact human-entered correction; spacing preserved.  "
    workspace.save_decision(
        "rescue-window-00005",
        "manual_correction",
        final_text=manual_text,
        reviewed_at="2026-08-29T01:03:00Z",
    )
    return {"manual_text": manual_text}


def test_loads_all_windows_and_keeps_green_auto_selection(tmp_path):
    final_path, audio_dir, output_dir, source = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    view = workspace.view()

    assert view["statistics"] == {
        "total_windows": 5,
        "resolved_windows": 1,
        "unresolved_windows": 4,
        "human_review_decisions": 0,
    }
    green = view["windows"][1]
    assert green["derived_status"] == "GREEN"
    assert green["final_text"] == source["windows"][1]["provider_candidates"]["gptTr"]["text"]
    assert green["review_resolution"]["decision_type"] == "auto_accepted_provider_candidate"
    assert green["review_resolution"]["selected_provider"] == "gptTr"


def test_four_exception_windows_resolve_independently_and_preserve_candidates(tmp_path):
    final_path, audio_dir, output_dir, source = _write_workspace(tmp_path)
    source_bytes = final_path.read_bytes()
    original_candidates = deepcopy([window["provider_candidates"] for window in source["windows"]])
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    proof = _resolve_all(workspace)

    reviewed = json.loads(workspace.reviewed_json_path.read_text(encoding="utf-8"))
    assert reviewed["statistics"] == {
        "total_windows": 5,
        "resolved_windows": 5,
        "unresolved_windows": 0,
        "human_review_decisions": 4,
    }
    assert all(isinstance(window["final_text"], str) and window["final_text"] for window in reviewed["windows"])
    assert reviewed["windows"][0]["final_text"] == original_candidates[0]["M3ASR"]["text"]
    assert reviewed["windows"][2]["final_text"] == original_candidates[2]["gptTr"]["text"]
    assert reviewed["windows"][3]["final_text"] == original_candidates[3]["Gem35T"]["text"]
    assert reviewed["windows"][4]["final_text"] == proof["manual_text"]
    assert reviewed["windows"][4]["review_resolution"]["decision_type"] == "manual_correction"
    assert reviewed["windows"][4]["review_resolution"]["selected_provider"] is None
    assert [window["provider_candidates"] for window in reviewed["windows"]] == original_candidates
    assert final_path.read_bytes() == source_bytes
    assert workspace.reviewed_html_path.is_file()


def test_provider_choice_copies_exact_candidate_and_records_provenance(tmp_path):
    final_path, audio_dir, output_dir, source = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    decision = workspace.save_decision(
        "rescue-window-00001",
        "provider_candidate",
        selected_provider="Gem35T",
        reviewed_at="2026-08-29T02:00:00Z",
    )

    candidate = source["windows"][0]["provider_candidates"]["Gem35T"]
    assert decision["final_text"] == candidate["text"]
    assert decision["selected_provider"] == "Gem35T"
    assert decision["selected_candidate_provenance"]["provider_response_ref"] == candidate["provider_response_ref"]
    assert decision["original_candidates"] == source["windows"][0]["provider_candidates"]


def test_review_honors_quality_provider_order_and_manual_weave(tmp_path):
    final_path, audio_dir, output_dir, draft = _write_workspace(tmp_path)
    new_order = ["gptTr", "gpt4oDiarz", "Gem35T"]
    for window in draft["windows"]:
        old = window["provider_candidates"]
        window["provider_candidates"] = {
            "gptTr": old["gptTr"],
            "gpt4oDiarz": {**old["M3ASR"], "provider_code": "gpt4oDiarz"},
            "Gem35T": old["Gem35T"],
        }
        if window.get("selected_provider") == "M3ASR":
            window["selected_provider"] = "gpt4oDiarz"
    draft["provider_order"] = new_order
    final_path.write_text(json.dumps(draft, indent=2), encoding="utf-8")

    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    assert workspace.view()["provider_order"] == new_order
    provider_decision = workspace.save_decision(
        "rescue-window-00001",
        "provider_candidate",
        selected_provider="Gem35T",
    )
    assert provider_decision["final_text"] == draft["windows"][0][
        "provider_candidates"
    ]["Gem35T"]["text"]

    woven = "Human-corrected wording woven from listening and comparison."
    manual_decision = workspace.save_decision(
        "rescue-window-00003",
        "manual_correction",
        final_text=woven,
    )
    assert manual_decision["final_text"] == woven


def test_manual_correction_is_stored_exactly_and_green_override_is_rejected(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    entered = "  Human text, unchanged.\nSecond line.  "
    decision = workspace.save_decision(
        "rescue-window-00003",
        "manual_correction",
        final_text=entered,
        reviewed_at="2026-08-29T03:00:00Z",
    )
    assert decision["final_text"] == entered
    assert decision["decision_type"] == "manual_correction"
    assert decision["reviewed_at"] == "2026-08-29T03:00:00Z"
    with pytest.raises(ValueError, match="GREEN"):
        workspace.save_decision(
            "rescue-window-00002", "provider_candidate", selected_provider="M3ASR"
        )


def test_decisions_persist_and_reload_with_source_hash_guard(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    _resolve_all(workspace)

    reopened = ReviewWorkspace(final_path, audio_dir, output_dir)
    view = reopened.view()
    assert view["statistics"]["resolved_windows"] == 5
    assert view["statistics"]["human_review_decisions"] == 4
    assert view["windows"][0]["review_resolution"]["selected_provider"] == "M3ASR"
    assert view["windows"][4]["review_resolution"]["decision_type"] == "manual_correction"


def test_audio_endpoint_maps_exact_global_range_and_sha_verified_clip(tmp_path):
    final_path, audio_dir, output_dir, source = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    path, metadata = workspace.audio("rescue-window-00004")

    assert path == audio_dir.resolve() / "chunk-00004.wav"
    assert metadata["global_start_sec"] == source["windows"][3]["global_start_sec"]
    assert metadata["global_end_sec"] == source["windows"][3]["global_end_sec"]
    assert metadata["clip_sha256"] == _sha256(path)
    assert local_dashboard._match_review_audio_request(
        "/api/review/audio/rescue-window-00004"
    ) == "rescue-window-00004"

    class StubHandler:
        def __init__(self):
            self.status = None
            self.headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.headers[key] = value

        def end_headers(self):
            return None

    handler = StubHandler()
    local_dashboard._review_audio_response(handler, workspace, "rescue-window-00004")
    assert handler.status == 200
    assert handler.headers["Content-Type"] == "audio/wav"
    assert handler.headers["X-APMA-Global-Start"] == str(source["windows"][3]["global_start_sec"])
    assert handler.headers["X-APMA-Global-End"] == str(source["windows"][3]["global_end_sec"])
    assert handler.headers["X-APMA-Clip-SHA256"] == _sha256(path)
    assert handler.wfile.getvalue() == path.read_bytes()


def test_review_runs_without_credentials_or_network(tmp_path, monkeypatch):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    monkeypatch.setenv("MERALION_API_KEY", "must-not-appear")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-appear")

    def fail_network(*_args, **_kwargs):
        raise AssertionError("human review attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    _resolve_all(workspace)
    for path in output_dir.iterdir():
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            assert "must-not-appear" not in content


def test_dashboard_review_route_contract_and_environment_workspace(tmp_path, monkeypatch):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    monkeypatch.setenv(local_dashboard.REVIEW_FINAL_DRAFT_ENV, str(final_path))
    monkeypatch.setenv(local_dashboard.REVIEW_AUDIO_DIR_ENV, str(audio_dir))
    monkeypatch.setenv(local_dashboard.REVIEW_OUTPUT_DIR_ENV, str(output_dir))

    payload = local_dashboard._review_payload()
    assert payload["statistics"]["total_windows"] == 5
    assert payload["windows"][0]["audio_url"].endswith("rescue-window-00001")
    assert Path(payload["outputs"]["json"]).name == "FINAL_REVIEWED.json"
    assert Path(payload["outputs"]["html"]).name == "FINAL_REVIEWED.html"
    assert "/api/review" in REVIEW_HTML
    assert "/api/review/decisions" in REVIEW_HTML
    assert "Use M3ASR" not in REVIEW_HTML
    assert "`Use ${provider}`" in REVIEW_HTML
    assert "Needs review" in REVIEW_HTML
    assert "Next visible area" in REVIEW_HTML
    assert 'data-filter="RED"' in REVIEW_HTML
    assert 'data-filter="AMBER"' in REVIEW_HTML
    assert "renderVisibleWindows" in REVIEW_HTML
    assert "/api/review/speaker-decisions" in REVIEW_HTML
    assert "Still unclear" in REVIEW_HTML
    assert "Speaker labels sound right" in REVIEW_HTML
    assert "Playback speed" in REVIEW_HTML


def test_unclear_decision_records_attention_without_inventing_final_text(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(
        final_path,
        audio_dir,
        output_dir,
        targeted_verification_requested=True,
        source_audio_seconds=120.0,
    )

    decision = workspace.save_decision(
        "rescue-window-00001",
        "unclear",
        reviewed_at="2026-10-10T01:00:00Z",
    )
    view = workspace.view()
    window = view["windows"][0]
    verification = view["targeted_verification"]

    assert decision["final_text"] is None
    assert decision["final_text_sha256"] is None
    assert decision["reason_code"] == "audio_still_unclear"
    assert window["review_required"] is True
    assert window["final_text"] is None
    assert window["review_resolution"]["decision_type"] == "unclear"
    assert verification["requested"] is True
    assert verification["content_decisions"]["unclear"] == 1
    assert verification["unresolved_selected_windows"] == 4
    assert verification["accuracy_uplift_claimed"] is False
    assert verification["completion_label"] == (
        "Selected-window human reviewed with unresolved items"
    )


def test_content_and_speaker_verification_are_separate_and_auditable(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(
        final_path,
        audio_dir,
        output_dir,
        targeted_verification_requested=True,
        source_audio_seconds=120.0,
    )
    _resolve_all(workspace)
    for index in (1, 3, 4, 5):
        workspace.save_speaker_decision(
            f"rescue-window-{index:05d}",
            "not_applicable" if index == 5 else "confirmed",
            reviewed_at=f"2026-10-10T01:0{index}:00Z",
        )

    view = workspace.view()
    verification = view["targeted_verification"]
    reviewed = json.loads(workspace.reviewed_json_path.read_text(encoding="utf-8"))

    assert workspace.speaker_decisions_path.is_file()
    assert verification["completion_label"] == "Selected-window human confirmed"
    assert verification["selected_window_count"] == 4
    assert verification["selected_audio_seconds"] == 96.0
    assert verification["selected_audio_share_percent"] == 80.0
    assert verification["content_decisions"]["resolved"] == 4
    assert verification["speaker_decisions"]["reviewed"] == 4
    assert verification["speaker_decisions"]["confirmed"] == 3
    assert verification["speaker_decisions"]["not_applicable"] == 1
    assert verification["windows_requiring_attention"] == 0
    assert reviewed["windows"][0]["speaker_review_resolution"]["speaker_status"] == "confirmed"
    assert reviewed["windows"][0]["provider_candidates"] == workspace.source["windows"][0]["provider_candidates"]


def test_opt_in_adds_multi_speaker_green_window_without_reopening_its_text(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    overlay = {
        "windows": [
            {
                "window_id": "rescue-window-00002",
                "speaker_evidence": {
                    "segments": [
                        {"speaker_id": "spk_1", "global_start_sec": 939.0, "global_end_sec": 950.0},
                        {"speaker_id": "spk_2", "global_start_sec": 950.0, "global_end_sec": 963.0},
                    ]
                },
            }
        ]
    }
    speaker_ids = speaker_sensitive_window_ids(overlay)
    workspace = ReviewWorkspace(
        final_path,
        audio_dir,
        output_dir,
        targeted_verification_requested=True,
        source_audio_seconds=120.0,
        speaker_review_window_ids=speaker_ids,
    )

    view = workspace.view()
    green = view["windows"][1]
    verification = view["targeted_verification"]

    assert speaker_ids == {"rescue-window-00002"}
    assert verification["selected_window_count"] == 5
    assert verification["content_review_window_count"] == 4
    assert verification["speaker_sensitive_window_count"] == 1
    assert green["targeted_verification_selection"] == {
        "selected": True,
        "content_review": False,
        "speaker_review": True,
    }
    with pytest.raises(ValueError, match="do not require a Goal J decision"):
        workspace.save_decision(
            "rescue-window-00002", "manual_correction", final_text="not allowed"
        )
    speaker_decision = workspace.save_speaker_decision(
        "rescue-window-00002", "confirmed"
    )
    assert speaker_decision["speaker_status"] == "confirmed"


def test_classic_review_does_not_require_speaker_checks_without_opt_in(tmp_path):
    final_path, audio_dir, output_dir, _ = _write_workspace(tmp_path)
    workspace = ReviewWorkspace(final_path, audio_dir, output_dir)
    _resolve_all(workspace)

    verification = workspace.view()["targeted_verification"]

    assert verification["requested"] is False
    assert verification["speaker_decisions"]["pending"] == 0
    assert verification["windows_requiring_attention"] == 0
    assert verification["completion_label"] == "Selected-window human confirmed"
