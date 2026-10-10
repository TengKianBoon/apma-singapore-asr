import json
import socket
from pathlib import Path

import pytest

from services import job as job_mod
from services.pilot_evidence import (
    PILOT_EVENTS_FILENAME,
    PILOT_LATEST_FILENAME,
    build_pilot_summary,
    load_pilot_evidence,
    record_pilot_evidence,
    verify_pilot_event_chain,
)


def _job(
    storage: Path,
    job_id: str,
    *,
    duration_seconds: float = 3600.0,
    provider_cost: float = 0.6,
    correction_count: int = 2,
) -> Path:
    job_dir = job_mod.create_job(job_id, str(storage))
    transcript_path = job_dir / "outputs" / "full_transcript.json"
    transcript_path.write_text(
        json.dumps(
            {
                "segments": [
                    {"segment_id": f"s{index}", "text": "not copied to pilot evidence"}
                    for index in range(10)
                ],
                "correction_history": [
                    {"correction_id": f"c{index}", "accepted_text": "also not copied"}
                    for index in range(correction_count)
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = job_mod.read_manifest(job_dir)
    manifest.update(
        {
            "state": "complete",
            "source": {"sha256": f"sha-{job_id}"},
            "preprocess": {"duration_seconds": duration_seconds},
            "outputs": {"full_transcript_json": str(transcript_path)},
            "quality": {
                "providers": {"M3ASR": {}, "QwenA3FT": {}},
                "stages": {"REVIEW": {"unresolved_windows": 0}},
            },
            "actual_cost_usd": provider_cost,
        }
    )
    job_mod.write_manifest(job_dir, manifest)
    return job_dir


def _payload(
    participant_code: str = "P001",
    *,
    classification: str = "consented_private",
    cohort_code: str = "SG-HOK-01",
) -> dict:
    consented = classification == "consented_private"
    return {
        "cohort_code": cohort_code,
        "participant_code": participant_code,
        "data_classification": classification,
        "use_case": "oral_history",
        "language_mix": ["hokkien", "singlish", "mandarin"],
        "task_status": "completed",
        "consent_record_id": "CR-001" if consented else None,
        "purpose_notice_confirmed": consented,
        "consent_confirmed": consented,
        "provider_processing_disclosed": consented,
        "withdrawal_route_disclosed": consented,
        "retention_review_due_on": "2026-12-31" if consented else None,
        "public_excerpt_authorized": False,
        "review_minutes": 15.0,
        "delivery_labor_cost_usd": 5.0,
        "support_cost_usd": 1.0,
        "price_charged_usd": 10.0,
        "trust_before": 2,
        "trust_after": 4,
        "confidence_to_share": 4,
        "transcript_usable": True,
        "would_use_again": True,
        "primary_blocker": "none",
        "reference_evaluation": {
            "dialect_units_total": 20,
            "dialect_units_correct": 16,
            "meaning_units_total": 40,
            "meaning_units_correct": 36,
            "critical_terms_total": 10,
            "critical_terms_correct": 9,
            "speaker_turns_total": 20,
            "speaker_attribution_errors": 2,
            "unsupported_content_events": 1,
            "omission_events": 2,
        },
    }


def test_records_privacy_minimised_hash_linked_pilot_evidence(tmp_path):
    job_dir = _job(tmp_path / "jobs", "pilot-job-1")

    first = record_pilot_evidence(
        job_dir, _payload(), recorded_at="2026-09-11T01:00:00Z"
    )
    updated_payload = _payload()
    updated_payload["review_minutes"] = 12.0
    second = record_pilot_evidence(
        job_dir, updated_payload, recorded_at="2026-09-11T02:00:00Z"
    )

    loaded = load_pilot_evidence(job_dir)
    serialized = json.dumps(loaded, ensure_ascii=False)
    assert loaded["recorded"] is True
    assert loaded["integrity"] == {
        "ok": True,
        "event_count": 2,
        "latest_event_sha256": second["event_sha256"],
    }
    assert second["previous_event_sha256"] == first["event_sha256"]
    assert second["derived_job_metrics"]["duration_seconds"] == 3600.0
    assert second["derived_job_metrics"]["correction_count"] == 2
    assert second["derived_job_metrics"]["corrections_per_100_segments"] == 20.0
    assert second["derived_job_metrics"]["dialect_reference_accuracy_percent"] == 80.0
    assert second["derived_job_metrics"]["total_delivery_cost_usd"] == 6.6
    assert second["derived_job_metrics"]["gross_margin_percent"] == 34.0
    assert "not copied to pilot evidence" not in serialized
    assert "also not copied" not in serialized
    assert (job_dir / "pilot" / PILOT_EVENTS_FILENAME).is_file()
    assert (job_dir / "pilot" / PILOT_LATEST_FILENAME).is_file()


def test_consented_private_record_requires_governance_attestations(tmp_path):
    job_dir = _job(tmp_path / "jobs", "pilot-job-2")
    payload = _payload()
    payload["provider_processing_disclosed"] = False

    with pytest.raises(ValueError, match="provider_processing_disclosed"):
        record_pilot_evidence(job_dir, payload)

    payload = _payload()
    payload["retention_review_due_on"] = "2026-02-30"
    with pytest.raises(ValueError, match="valid calendar date"):
        record_pilot_evidence(job_dir, payload)


def test_rejects_direct_identifiers_free_text_and_invalid_reference_counts(tmp_path):
    job_dir = _job(tmp_path / "jobs", "pilot-job-3")
    with pytest.raises(ValueError, match="participant_name"):
        record_pilot_evidence(job_dir, {**_payload(), "participant_name": "Alice"})
    with pytest.raises(ValueError, match="notes"):
        record_pilot_evidence(job_dir, {**_payload(), "notes": "private comment"})

    payload = _payload()
    payload["reference_evaluation"]["dialect_units_correct"] = 21
    with pytest.raises(ValueError, match="cannot exceed"):
        record_pilot_evidence(job_dir, payload)

    payload = _payload()
    payload["review_minutes"] = float("nan")
    with pytest.raises(ValueError, match="review_minutes"):
        record_pilot_evidence(job_dir, payload)


def test_summary_excludes_synthetic_and_suppresses_private_references(tmp_path):
    storage = tmp_path / "jobs"
    first = _job(storage, "pilot-a", correction_count=2)
    second = _job(storage, "pilot-b", provider_cost=0.4, correction_count=1)
    third = _job(storage, "pilot-c", provider_cost=0.0, correction_count=0)
    record_pilot_evidence(first, _payload("P001"), recorded_at="2026-09-11T01:00:00Z")
    second_payload = _payload("P001")
    second_payload["would_use_again"] = False
    second_payload["transcript_usable"] = False
    second_payload["primary_blocker"] = "accuracy"
    record_pilot_evidence(second, second_payload, recorded_at="2026-09-11T02:00:00Z")
    record_pilot_evidence(
        third,
        _payload("SYNTH-1", classification="synthetic"),
        recorded_at="2026-09-11T03:00:00Z",
    )

    summary = build_pilot_summary(
        storage,
        cohort_code="SG-HOK-01",
        generated_at="2026-09-12T00:00:00Z",
    )
    serialized = json.dumps(summary)

    assert summary["sample"] == {
        "records_scanned": 3,
        "eligible_jobs": 2,
        "participants": 1,
    }
    assert summary["privacy"]["synthetic_records_excluded"] == 1
    assert summary["adoption"]["completion_rate_percent"] == 100.0
    assert summary["adoption"]["transcript_usable_rate_percent"] == 50.0
    assert summary["adoption"]["would_use_again_rate_percent"] == 50.0
    assert summary["adoption"]["repeat_participant_rate_percent"] == 100.0
    assert summary["accuracy"]["dialect_reference_accuracy_percent"] == 80.0
    assert summary["correction_effort"]["corrections_per_100_segments"] == 15.0
    assert summary["trust"]["average_change"] == 2.0
    assert "P001" not in serialized
    assert "CR-001" not in serialized
    assert "sha-pilot" not in serialized


def test_capture_and_summary_do_not_attempt_network_access(tmp_path, monkeypatch):
    job_dir = _job(tmp_path / "jobs", "pilot-offline")

    def fail_network(*args, **kwargs):
        raise AssertionError("pilot evidence attempted network access")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    record_pilot_evidence(job_dir, _payload())
    summary = build_pilot_summary(tmp_path / "jobs")

    assert summary["sample"]["eligible_jobs"] == 1
    assert verify_pilot_event_chain(job_dir)["ok"] is True
