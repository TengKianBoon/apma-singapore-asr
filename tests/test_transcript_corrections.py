import hashlib
import json
import socket
from pathlib import Path

import pytest

from services.transcript_corrections import apply_segment_correction


JOB_ID = "correction-test"
CORRECTED_AT = "2026-08-28T04:05:06Z"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _segment(segment_id: str, text: str, start: float, end: float) -> dict:
    return {
        "segment_id": segment_id,
        "chunk_filename": "chunk-00001.wav" if segment_id.startswith("c1") else "chunk-00002.wav",
        "start_sec": start,
        "end_sec": end,
        "speaker": "Speaker 1",
        "provider_speaker": "A",
        "speaker_scope": "chunk",
        "text": text,
        "provider": "openai",
        "provider_code": "gptTr",
        "model": "gpt-transcribe",
        "run_id": "run-123",
    }


def _candidate(candidate_id: str, text: str, provider: str = "openai") -> dict:
    return {
        "candidate_id": candidate_id,
        "provider": provider,
        "provider_code": "gptTr" if provider == "openai" else "Gem35",
        "model": "gpt-transcribe" if provider == "openai" else "gemini-transcribe",
        "run_id": f"{provider}-run-1",
        "source_sha256": "source-hash",
        "provider_artifact_path": f"providers/{provider}/artifact.json",
        "text": text,
    }


def _write_fixture(job_dir: Path, *, duplicate_segment_id: bool = False) -> dict:
    original_one = "We will approve the budget."
    original_two = "The report is due Friday."
    second_id = "c1:0001" if duplicate_segment_id else "c2:0001"
    first_segment = _segment("c1:0001", original_one, 0.0, 3.0)
    second_segment = _segment(second_id, original_two, 3.0, 6.0)
    payload = {
        "schema_version": "apma.transcript.full.v1",
        "job_id": JOB_ID,
        "created_at": "2026-08-28T00:00:00Z",
        "provenance": {
            "providers": ["google", "openai"],
            "models": ["gemini-transcribe", "gpt-transcribe"],
            "run_ids": ["google-run-1", "openai-run-1"],
            "source_sha256": ["source-hash"],
            "provider_artifacts": ["providers/google/artifact.json", "providers/openai/artifact.json"],
        },
        "candidate_count": 2,
        "review": {"grade_counts": {"green": 1, "amber": 1, "red": 0}},
        "correction_history": [],
        "chunks": [
            {
                "chunk_filename": "chunk-00001.wav",
                "text": f"Speaker 1: {original_one}",
                "candidate_count": 2,
                "candidates": [_candidate("c1-openai", original_one), _candidate("c1-google", original_one, "google")],
                "reconciliation": {"selected_candidate_id": "c1-openai", "grade": "amber"},
            },
            {
                "chunk_filename": "chunk-00002.wav",
                "text": f"Speaker 1: {original_two}",
                "candidate_count": 1,
                "candidates": [_candidate("c2-openai", original_two)],
                "reconciliation": {"selected_candidate_id": "c2-openai", "grade": "green"},
            },
        ],
        "segments": [first_segment, second_segment],
        "text": f"Speaker 1: {original_one}\n\nSpeaker 1: {original_two}",
        "word_count": 14,
        "character_count": len(f"Speaker 1: {original_one}\n\nSpeaker 1: {original_two}"),
    }
    outputs = job_dir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "full_transcript.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for filename in ("full_transcript.txt", "full_transcript.html", "full_transcript.srt", "full_transcript.vtt"):
        (outputs / filename).write_text("stale derived output", encoding="utf-8")
    return payload


def _read_payload(job_dir: Path) -> dict:
    return json.loads((job_dir / "outputs" / "full_transcript.json").read_text(encoding="utf-8"))


def test_apply_correction_updates_canonical_segment_chunk_full_text_and_counts(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)

    result = apply_segment_correction(
        job_dir,
        "c1:0001",
        "We will approve the revised budget.",
        "Reviewer corrected the amount discussed.",
        "Alex",
        corrected_at=CORRECTED_AT,
    )

    payload = _read_payload(job_dir)
    assert result["correction_id"] == payload["correction_history"][0]["correction_id"]
    assert payload["schema_version"] == "apma.transcript.full.v1"
    assert payload["segments"][0]["text"] == "We will approve the revised budget."
    assert payload["chunks"][0]["text"] == "Speaker 1: We will approve the revised budget."
    assert payload["text"] == "Speaker 1: We will approve the revised budget.\n\nSpeaker 1: The report is due Friday."
    assert payload["word_count"] == len(payload["text"].split())
    assert payload["character_count"] == len(payload["text"])


def test_correction_event_preserves_evidence_and_records_source_provenance(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    original = _write_fixture(job_dir)
    original_candidates = json.loads(json.dumps(original["chunks"][0]["candidates"]))

    apply_segment_correction(
        job_dir,
        "c1:0001",
        "We will approve the revised budget.",
        "Clarified during human review.",
        "Alex",
        corrected_at=CORRECTED_AT,
    )

    event = _read_payload(job_dir)["correction_history"][0]
    assert event["corrected_at"] == CORRECTED_AT
    assert event["reviewer"] == "Alex"
    assert event["reason"] == "Clarified during human review."
    assert event["segment_id"] == "c1:0001"
    assert event["original_text"] == "We will approve the budget."
    assert event["accepted_text"] == "We will approve the revised budget."
    assert event["original_text_sha256"] == _sha256(event["original_text"])
    assert event["accepted_text_sha256"] == _sha256(event["accepted_text"])
    assert event["source_provider"] == "openai"
    assert event["source_model"] == "gpt-transcribe"
    assert event["source_run_id"] == "run-123"
    assert event["source_start_sec"] == 0.0
    assert event["source_end_sec"] == 3.0
    assert _read_payload(job_dir)["chunks"][0]["candidates"] == original_candidates
    assert _read_payload(job_dir)["provenance"] == original["provenance"]


@pytest.mark.parametrize("field, value", [("accepted_text", ""), ("reason", "  "), ("reviewer", None)])
def test_rejects_missing_or_empty_correction_fields(tmp_path, field, value):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)
    kwargs = {"accepted_text": "replacement", "reason": "reason", "reviewer": "Alex"}
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        apply_segment_correction(job_dir, "c1:0001", **kwargs)


def test_rejects_invalid_schema_and_duplicate_segment_ids(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)
    path = job_dir / "outputs" / "full_transcript.json"
    payload = _read_payload(job_dir)
    payload["schema_version"] = "wrong.schema.v1"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        apply_segment_correction(job_dir, "c1:0001", "replacement", "reason", "Alex")

    _write_fixture(job_dir, duplicate_segment_id=True)
    with pytest.raises(ValueError, match="duplicate"):
        apply_segment_correction(job_dir, "c1:0001", "replacement", "reason", "Alex")


def test_rejects_unknown_segment_and_stale_expected_hash_without_mutation(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)
    before = (job_dir / "outputs" / "full_transcript.json").read_bytes()

    with pytest.raises(ValueError, match="unknown"):
        apply_segment_correction(job_dir, "missing:0001", "replacement", "reason", "Alex")
    with pytest.raises(ValueError, match="stale|hash"):
        apply_segment_correction(
            job_dir,
            "c1:0001",
            "replacement",
            "reason",
            "Alex",
            expected_original_sha256="0" * 64,
        )

    assert (job_dir / "outputs" / "full_transcript.json").read_bytes() == before


def test_second_correction_is_append_only_and_uses_latest_text(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)
    first = apply_segment_correction(
        job_dir, "c1:0001", "First accepted wording.", "First review", "Alex", corrected_at="2026-08-28T04:00:00Z"
    )
    first_event_before = _read_payload(job_dir)["correction_history"][0].copy()
    second = apply_segment_correction(
        job_dir,
        "c1:0001",
        "Second accepted wording.",
        "Second review",
        "Blair",
        expected_original_sha256=_sha256("First accepted wording."),
        corrected_at="2026-08-28T05:00:00Z",
    )

    history = _read_payload(job_dir)["correction_history"]
    assert len(history) == 2
    assert history[0] == first_event_before
    assert history[0]["correction_id"] == first["correction_id"]
    assert history[1]["correction_id"] == second["correction_id"]
    assert history[1]["original_text"] == "First accepted wording."
    assert history[1]["original_text_sha256"] == _sha256("First accepted wording.")


def test_regenerates_all_derived_exports_from_corrected_json(tmp_path):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)
    apply_segment_correction(job_dir, "c1:0001", "Corrected & final.", "Fix", "Alex", corrected_at=CORRECTED_AT)

    outputs = job_dir / "outputs"
    txt = (outputs / "full_transcript.txt").read_text(encoding="utf-8")
    html = (outputs / "full_transcript.html").read_text(encoding="utf-8")
    srt = (outputs / "full_transcript.srt").read_text(encoding="utf-8")
    vtt = (outputs / "full_transcript.vtt").read_text(encoding="utf-8")
    assert "Corrected & final." in txt
    assert "Corrected &amp; final." in html
    assert "00:00:00,000 --> 00:00:03,000" in srt
    assert "Corrected &amp; final." in srt
    assert vtt.startswith("WEBVTT\n")
    assert "00:00:00.000 --> 00:00:03.000" in vtt


def test_correction_runs_without_network_access(tmp_path, monkeypatch):
    job_dir = tmp_path / "jobs" / JOB_ID
    _write_fixture(job_dir)

    def fail_network(*args, **kwargs):
        raise AssertionError("correction ledger attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    apply_segment_correction(job_dir, "c1:0001", "Offline correction.", "Fix", "Alex", corrected_at=CORRECTED_AT)
    assert _read_payload(job_dir)["segments"][0]["text"] == "Offline correction."
