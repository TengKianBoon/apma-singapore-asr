from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from scripts import local_dashboard
from services import job as job_mod
from services.human_review import PROVIDER_ORDER, REVIEW_HTML
from services.speaker_overlay import SpeakerMappingStore, attach_speaker_overlay
from tests.helpers import generate_sine_wav


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_quality_job(tmp_path: Path) -> tuple[Path, Path, dict, dict[str, str]]:
    storage = tmp_path / "jobs"
    job_dir = job_mod.create_job("speaker-overlay-fixture", str(storage))
    audio_dir = job_dir / "quality" / "rescue" / "extracted-clips" / "chunks"
    audio_dir.mkdir(parents=True)
    windows = []
    for index, (start, end) in enumerate(((0.0, 20.0), (20.0, 40.0)), start=1):
        audio_path = audio_dir / f"chunk-{index:05d}.wav"
        generate_sine_wav(str(audio_path), duration_sec=1.0, framerate=8000)
        clip_sha = _sha256(audio_path)
        candidates = {
            provider: {
                "missing": False,
                "text": f"{provider} unchanged text {index}",
                "provider_code": provider,
            }
            for provider in PROVIDER_ORDER
        }
        windows.append(
            {
                "window_id": f"window-{index}",
                "global_start_sec": start,
                "global_end_sec": end,
                "clip_sha256": clip_sha,
                "derived_status": "RED",
                "provider_candidates": candidates,
                "final_text": None,
                "selected_text": None,
                "selected_provider": None,
                "auto_accepted": False,
                "review_required": True,
            }
        )
    final_payload = {
        "schema_version": "apma.final-draft.v1",
        "windows": windows,
    }
    final_path = job_dir / "quality" / "final-draft" / "FINAL_DRAFT.json"
    final_path.parent.mkdir(parents=True)
    final_path.write_text(json.dumps(final_payload, indent=2), encoding="utf-8")

    provider_hashes = {}
    providers = {}
    for provider in PROVIDER_ORDER:
        path = job_dir / "quality" / "provider-evidence" / provider / f"{provider}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"provider": provider, "text": "unchanged"}), encoding="utf-8")
        provider_hashes[provider] = _sha256(path)
        providers[provider] = {"json": str(path)}

    manifest = job_mod.read_manifest(job_dir)
    manifest["quality"] = {
        "review_audio_dir": str(audio_dir),
        "outputs": {
            "final_draft": {"json": str(final_path)},
            "final_reviewed": {
                "json": str(job_dir / "quality" / "review" / "FINAL_REVIEWED.json")
            },
            "providers": providers,
        },
    }
    job_mod.write_manifest(job_dir, manifest)
    return job_dir, final_path, final_payload, provider_hashes


def _write_timeline(tmp_path: Path) -> tuple[Path, Path, dict]:
    segments = [
        {
            "segment_id": "turn-1",
            "speaker_id": "spk:0",
            "global_start_sec": 5.0,
            "global_end_sec": 12.0,
            "text": "provider evidence alpha",
            "provider_code": "Gem35T",
            "run_id": "run-a",
            "chunk_filename": "clip-a.wav",
        },
        {
            "segment_id": "turn-2",
            "speaker_id": "spk:1",
            "global_start_sec": 12.0,
            "global_end_sec": 18.0,
            "text": "provider evidence beta",
            "provider_code": "Gem35T",
            "run_id": "run-a",
            "chunk_filename": "clip-a.wav",
        },
        {
            "segment_id": "turn-3",
            "speaker_id": "spk:0",
            "global_start_sec": 21.0,
            "global_end_sec": 28.0,
            "text": "provider evidence gamma",
            "provider_code": "Gem35T",
            "run_id": "run-a",
            "chunk_filename": "clip-a.wav",
        },
    ]
    timeline = {
        "schema_version": "apma.speaker-timeline.v1",
        "statistics": {"speaker_count": 2, "speaker_ids": ["spk:0", "spk:1"], "word_count": 99},
        "words": [{"raw_annotation_index": index} for index in range(99)],
        "segments": segments,
    }
    json_path = tmp_path / "speaker_timeline.json"
    html_path = tmp_path / "speaker_timeline.html"
    json_path.write_text(json.dumps(timeline, indent=2), encoding="utf-8")
    html_path.write_text("<html>unchanged speaker evidence</html>", encoding="utf-8")
    return json_path, html_path, timeline


def _text_snapshot(payload: dict) -> list[dict]:
    return [
        {
            "final_text": item.get("final_text"),
            "selected_text": item.get("selected_text"),
            "provider_candidates": deepcopy(item.get("provider_candidates")),
        }
        for item in payload["windows"]
    ]


def test_overlay_and_mapping_preserve_quality_text_and_raw_evidence(tmp_path, monkeypatch):
    job_dir, final_path, final_payload, provider_hashes = _write_quality_job(tmp_path)
    timeline_path, timeline_html, timeline = _write_timeline(tmp_path)
    final_hash = _sha256(final_path)
    timeline_hash = _sha256(timeline_path)

    overlay = attach_speaker_overlay(
        job_dir,
        timeline_path,
        speaker_timeline_html=timeline_html,
    )
    assert overlay["statistics"] == {
        "window_count": 2,
        "local_speaker_count": 2,
        "multiple_speaker_window_count": 1,
    }
    assert overlay["source_speaker_timeline"]["copied_byte_for_byte"] is True
    assert _sha256(Path(overlay["source_speaker_timeline"]["copied_path"])) == timeline_hash
    assert len(json.loads(timeline_path.read_text(encoding="utf-8"))["words"]) == 99
    assert json.loads(timeline_path.read_text(encoding="utf-8")) == timeline
    assert _sha256(final_path) == final_hash
    assert _text_snapshot(json.loads(final_path.read_text(encoding="utf-8"))) == _text_snapshot(final_payload)

    manifest = job_mod.read_manifest(job_dir)
    for provider, expected in provider_hashes.items():
        path = Path(manifest["quality"]["outputs"]["providers"][provider]["json"])
        assert _sha256(path) == expected

    overlay_path = Path(manifest["quality"]["outputs"]["speaker_overlay"]["json"])
    mapping_path = Path(manifest["quality"]["outputs"]["speaker_overlay"]["speaker_mappings_json"])
    store = SpeakerMappingStore(overlay_path, mapping_path)
    speakers = store.view()["local_speakers"]
    spk0 = next(item for item in speakers if item["provider_speaker_id"] == "spk:0")
    spk1 = next(item for item in speakers if item["provider_speaker_id"] == "spk:1")
    mapping = store.save_mapping(
        spk0["local_speaker_key"],
        "Test Human Name",
        mapped_at="2026-08-29T02:00:00Z",
    )
    assert mapping["mapping_source"] == "human"
    assert mapping["provider_speaker_id"] == "spk:0"

    reloaded = SpeakerMappingStore(overlay_path, mapping_path).view()
    reloaded_by_id = {item["provider_speaker_id"]: item for item in reloaded["local_speakers"]}
    assert reloaded_by_id["spk:0"]["display_label"] == "Test Human Name [spk:0]"
    assert reloaded_by_id["spk:1"]["display_label"] == "spk:1"
    assert reloaded_by_id["spk:0"]["mapping"]["canonical_speaker_id"] == "canonical-speaker-001"

    monkeypatch.setattr(local_dashboard, "STORAGE_PATH", job_dir.parent)
    view = local_dashboard._review_payload(job_dir.name)
    assert len(view["windows"][0]["speaker_evidence"]["segments"]) == 2
    labels = [
        segment["display_label"]
        for window in view["windows"]
        for segment in window["speaker_evidence"]["segments"]
    ]
    assert "Test Human Name [spk:0]" in labels
    assert "spk:1" in labels
    assert _text_snapshot(view) == _text_snapshot(final_payload)


def test_separate_run_scopes_can_be_human_mapped_to_same_canonical(tmp_path):
    overlay_path = tmp_path / "overlay.json"
    scopes = [
        {
            "local_speaker_key": "local-a",
            "provider_code": "Gem35T",
            "run_id": "run-a",
            "chunk_filename": "a.wav",
            "provider_speaker_id": "spk:0",
            "identity_inferred": False,
        },
        {
            "local_speaker_key": "local-b",
            "provider_code": "Gem35T",
            "run_id": "run-b",
            "chunk_filename": "b.wav",
            "provider_speaker_id": "spk:1",
            "identity_inferred": False,
        },
    ]
    overlay_path.write_text(
        json.dumps(
            {
                "schema_version": "apma.speaker-overlay.v1",
                "local_speakers": scopes,
                "windows": [],
            }
        ),
        encoding="utf-8",
    )
    store = SpeakerMappingStore(overlay_path, tmp_path / "mappings.json")
    first = store.save_mapping("local-a", "Test Person")
    second = store.save_mapping(
        "local-b",
        "Test Person",
        canonical_speaker_id=first["canonical_speaker_id"],
    )
    assert first["canonical_speaker_id"] == second["canonical_speaker_id"]
    assert first["provider_speaker_id"] == "spk:0"
    assert second["provider_speaker_id"] == "spk:1"


def test_review_page_exposes_minimal_human_mapping_controls():
    assert "/api/review/speaker-mappings" in REVIEW_HTML
    assert "Human speaker labels" in REVIEW_HTML
    assert "segment.display_label || segment.speaker_id" in REVIEW_HTML
    assert "automatic" not in REVIEW_HTML.lower().split("human speaker labels", 1)[1].split("</section>", 1)[0]
