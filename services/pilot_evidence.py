"""Privacy-minimised evidence capture for a small, consented APMA pilot.

The pilot dataset deliberately stores structured outcomes and derived job facts,
not audio, transcript text, participant contact details, or free-form notes.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from services import job as job_mod


PILOT_SCHEMA_VERSION = "apma.pilot-evidence.v1"
PILOT_SUMMARY_SCHEMA_VERSION = "apma.pilot-summary.v1"
PILOT_NOTICE_VERSION = "apma-pilot-notice-v1"
PILOT_DIR_NAME = "pilot"
PILOT_EVENTS_FILENAME = "evidence-events.jsonl"
PILOT_LATEST_FILENAME = "evidence-latest.json"

CODE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

DATA_CLASSIFICATIONS = {"synthetic", "consented_private"}
TASK_STATUSES = {"completed", "abandoned", "failed"}
USE_CASES = {
    "research_interview",
    "oral_history",
    "internal_meeting",
    "family_history",
    "service_evaluation",
    "other_evaluation",
}
LANGUAGES = {
    "hokkien",
    "singlish",
    "mandarin",
    "english",
    "teochew",
    "cantonese",
    "hakka",
    "hainanese",
    "malay",
    "tamil",
    "indonesian",
    "other",
}
PRIMARY_BLOCKERS = {
    "none",
    "accuracy",
    "speaker_labels",
    "review_effort",
    "speed",
    "cost",
    "privacy",
    "usability",
    "other",
}

TOP_LEVEL_INPUT_FIELDS = {
    "cohort_code",
    "participant_code",
    "data_classification",
    "use_case",
    "language_mix",
    "task_status",
    "consent_record_id",
    "purpose_notice_confirmed",
    "consent_confirmed",
    "provider_processing_disclosed",
    "withdrawal_route_disclosed",
    "retention_review_due_on",
    "public_excerpt_authorized",
    "review_minutes",
    "delivery_labor_cost_usd",
    "support_cost_usd",
    "price_charged_usd",
    "trust_before",
    "trust_after",
    "confidence_to_share",
    "transcript_usable",
    "would_use_again",
    "primary_blocker",
    "reference_evaluation",
}

REFERENCE_FIELDS = {
    "dialect_units_total",
    "dialect_units_correct",
    "meaning_units_total",
    "meaning_units_correct",
    "critical_terms_total",
    "critical_terms_correct",
    "speaker_turns_total",
    "speaker_attribution_errors",
    "unsupported_content_events",
    "omission_events",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _required_code(field: str, value: Any) -> str:
    if not isinstance(value, str) or not CODE_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field} must be 1-64 characters using only letters, numbers, '.', '_', or '-'"
        )
    return value


def _required_choice(field: str, value: Any, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return value


def _required_bool(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be true or false")
    return value


def _bounded_number(
    field: str,
    value: Any,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{field} must be between {minimum:g} and {maximum:g}")
    return number


def _bounded_integer(
    field: str,
    value: Any,
    *,
    minimum: int = 0,
    maximum: int = 1_000_000,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _rating(field: str, value: Any) -> int:
    return _bounded_integer(field, value, minimum=1, maximum=5)


def _valid_date(field: str, value: Any, *, required: bool) -> str | None:
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str) or not DATE_PATTERN.fullmatch(value):
        raise ValueError(f"{field} must use YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid calendar date") from exc
    return value


def _language_mix(value: Any) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 8:
        raise ValueError("language_mix must contain 1-8 supported language codes")
    languages: list[str] = []
    for item in value:
        language = _required_choice("language_mix item", item, LANGUAGES)
        if language not in languages:
            languages.append(language)
    return languages


def _reference_evaluation(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ValueError("reference_evaluation must be an object")
    unknown = set(value) - REFERENCE_FIELDS
    if unknown:
        raise ValueError(
            "reference_evaluation contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    result = {
        field: _bounded_integer(f"reference_evaluation.{field}", value.get(field, 0))
        for field in sorted(REFERENCE_FIELDS)
    }
    for prefix in ("dialect_units", "meaning_units", "critical_terms"):
        total = result[f"{prefix}_total"]
        correct = result[f"{prefix}_correct"]
        if correct > total:
            raise ValueError(f"{prefix}_correct cannot exceed {prefix}_total")
    if result["speaker_attribution_errors"] > result["speaker_turns_total"]:
        raise ValueError(
            "speaker_attribution_errors cannot exceed speaker_turns_total"
        )
    return result


def _normalize_input(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Pilot evidence must be a JSON object")
    unknown = set(payload) - TOP_LEVEL_INPUT_FIELDS
    if unknown:
        raise ValueError(
            "Pilot evidence contains unsupported fields: " + ", ".join(sorted(unknown))
        )

    classification = _required_choice(
        "data_classification", payload.get("data_classification"), DATA_CLASSIFICATIONS
    )
    consented = classification == "consented_private"
    normalized = {
        "cohort_code": _required_code("cohort_code", payload.get("cohort_code")),
        "participant_code": _required_code(
            "participant_code", payload.get("participant_code")
        ),
        "data_classification": classification,
        "use_case": _required_choice("use_case", payload.get("use_case"), USE_CASES),
        "language_mix": _language_mix(payload.get("language_mix")),
        "task_status": _required_choice(
            "task_status", payload.get("task_status"), TASK_STATUSES
        ),
        "consent_record_id": (
            _required_code("consent_record_id", payload.get("consent_record_id"))
            if consented
            else None
        ),
        "purpose_notice_confirmed": _required_bool(
            "purpose_notice_confirmed", payload.get("purpose_notice_confirmed", False)
        ),
        "consent_confirmed": _required_bool(
            "consent_confirmed", payload.get("consent_confirmed", False)
        ),
        "provider_processing_disclosed": _required_bool(
            "provider_processing_disclosed",
            payload.get("provider_processing_disclosed", False),
        ),
        "withdrawal_route_disclosed": _required_bool(
            "withdrawal_route_disclosed",
            payload.get("withdrawal_route_disclosed", False),
        ),
        "retention_review_due_on": _valid_date(
            "retention_review_due_on",
            payload.get("retention_review_due_on"),
            required=consented,
        ),
        "public_excerpt_authorized": _required_bool(
            "public_excerpt_authorized",
            payload.get("public_excerpt_authorized", False),
        ),
        "review_minutes": _bounded_number(
            "review_minutes", payload.get("review_minutes", 0), maximum=100_000
        ),
        "delivery_labor_cost_usd": _bounded_number(
            "delivery_labor_cost_usd", payload.get("delivery_labor_cost_usd", 0)
        ),
        "support_cost_usd": _bounded_number(
            "support_cost_usd", payload.get("support_cost_usd", 0)
        ),
        "price_charged_usd": _bounded_number(
            "price_charged_usd", payload.get("price_charged_usd", 0)
        ),
        "trust_before": _rating("trust_before", payload.get("trust_before")),
        "trust_after": _rating("trust_after", payload.get("trust_after")),
        "confidence_to_share": _rating(
            "confidence_to_share", payload.get("confidence_to_share")
        ),
        "transcript_usable": _required_bool(
            "transcript_usable", payload.get("transcript_usable")
        ),
        "would_use_again": _required_bool(
            "would_use_again", payload.get("would_use_again")
        ),
        "primary_blocker": _required_choice(
            "primary_blocker", payload.get("primary_blocker"), PRIMARY_BLOCKERS
        ),
        "reference_evaluation": _reference_evaluation(
            payload.get("reference_evaluation", {})
        ),
    }

    if consented:
        required_attestations = (
            "purpose_notice_confirmed",
            "consent_confirmed",
            "provider_processing_disclosed",
            "withdrawal_route_disclosed",
        )
        missing = [field for field in required_attestations if not normalized[field]]
        if missing:
            raise ValueError(
                "Consented private pilot evidence requires: " + ", ".join(missing)
            )
    elif normalized["public_excerpt_authorized"]:
        raise ValueError("Synthetic QA cannot authorise a participant excerpt")

    return normalized


def _safe_json_from_job(path_value: Any, job_dir: Path) -> dict[str, Any]:
    if not isinstance(path_value, str) or not path_value:
        return {}
    candidate = Path(path_value).resolve()
    try:
        candidate.relative_to(job_dir.resolve())
    except ValueError:
        return {}
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _percent(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 3)


def _per_audio_hour(value: float, audio_hours: float) -> float | None:
    if audio_hours <= 0:
        return None
    return round(value / audio_hours, 6)


def derive_job_metrics(job_dir: Path, normalized: dict[str, Any]) -> dict[str, Any]:
    job_dir = Path(job_dir)
    manifest = job_mod.read_manifest(job_dir)
    if not manifest:
        raise FileNotFoundError("Job manifest was not found")
    job_id = job_mod.validate_job_id(str(manifest.get("job_id") or job_dir.name))

    preprocess = manifest.get("preprocess") if isinstance(manifest.get("preprocess"), dict) else {}
    duration_seconds = float(
        preprocess.get("blob_duration_seconds", preprocess.get("duration_seconds", 0))
        or 0
    )
    if duration_seconds < 0:
        duration_seconds = 0.0
    audio_hours = duration_seconds / 3600.0
    provider_cost = max(0.0, float(manifest.get("actual_cost_usd", 0) or 0))

    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    transcript = _safe_json_from_job(outputs.get("full_transcript_json"), job_dir)
    segments = transcript.get("segments") if isinstance(transcript.get("segments"), list) else []
    corrections = (
        transcript.get("correction_history")
        if isinstance(transcript.get("correction_history"), list)
        else []
    )

    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    stages = quality.get("stages") if isinstance(quality.get("stages"), dict) else {}
    review = stages.get("REVIEW") if isinstance(stages.get("REVIEW"), dict) else {}
    providers = quality.get("providers") if isinstance(quality.get("providers"), dict) else {}

    delivery_cost = (
        provider_cost
        + normalized["delivery_labor_cost_usd"]
        + normalized["support_cost_usd"]
    )
    revenue = normalized["price_charged_usd"]
    gross_margin = revenue - delivery_cost
    reference = normalized["reference_evaluation"]

    return {
        "job_id": job_id,
        "result_state": str(manifest.get("state") or "unknown"),
        "source_sha256": (
            str((manifest.get("source") or {}).get("sha256") or "") or None
        ),
        "provider_codes": sorted(str(code) for code in providers),
        "duration_seconds": round(duration_seconds, 6),
        "audio_hours": round(audio_hours, 9),
        "segment_count": len(segments),
        "correction_count": len(corrections),
        "corrections_per_100_segments": _percent(len(corrections), len(segments)),
        "review_required_count": int(review.get("unresolved_windows", 0) or 0),
        "review_minutes": normalized["review_minutes"],
        "review_minutes_per_audio_hour": _per_audio_hour(
            normalized["review_minutes"], audio_hours
        ),
        "provider_cost_usd": round(provider_cost, 6),
        "delivery_labor_cost_usd": round(
            normalized["delivery_labor_cost_usd"], 6
        ),
        "support_cost_usd": round(normalized["support_cost_usd"], 6),
        "total_delivery_cost_usd": round(delivery_cost, 6),
        "cost_per_reviewed_audio_hour_usd": _per_audio_hour(
            delivery_cost, audio_hours
        ),
        "price_charged_usd": round(revenue, 6),
        "gross_margin_usd": round(gross_margin, 6),
        "gross_margin_percent": _percent(gross_margin, revenue),
        "dialect_reference_accuracy_percent": _percent(
            reference["dialect_units_correct"], reference["dialect_units_total"]
        ),
        "meaning_unit_accuracy_percent": _percent(
            reference["meaning_units_correct"], reference["meaning_units_total"]
        ),
        "critical_term_accuracy_percent": _percent(
            reference["critical_terms_correct"], reference["critical_terms_total"]
        ),
        "speaker_attribution_error_percent": _percent(
            reference["speaker_attribution_errors"], reference["speaker_turns_total"]
        ),
        "unsupported_content_events_per_audio_hour": _per_audio_hour(
            reference["unsupported_content_events"], audio_hours
        ),
        "omission_events_per_audio_hour": _per_audio_hour(
            reference["omission_events"], audio_hours
        ),
        "trust_change": normalized["trust_after"] - normalized["trust_before"],
    }


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _event_sha256(event: dict[str, Any]) -> str:
    unhashed = {key: value for key, value in event.items() if key != "event_sha256"}
    return hashlib.sha256(_canonical_json(unhashed).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _read_events(job_dir: Path) -> list[dict[str, Any]]:
    events_path = Path(job_dir) / PILOT_DIR_NAME / PILOT_EVENTS_FILENAME
    if not events_path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        events_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Pilot event ledger line {line_number} is invalid") from exc
        if not isinstance(event, dict):
            raise ValueError(f"Pilot event ledger line {line_number} is invalid")
        events.append(event)
    return events


def verify_pilot_event_chain(job_dir: Path) -> dict[str, Any]:
    events = _read_events(job_dir)
    previous: str | None = None
    for index, event in enumerate(events):
        if event.get("previous_event_sha256") != previous:
            return {"ok": False, "event_count": len(events), "invalid_index": index}
        if event.get("event_sha256") != _event_sha256(event):
            return {"ok": False, "event_count": len(events), "invalid_index": index}
        previous = str(event["event_sha256"])
    return {
        "ok": True,
        "event_count": len(events),
        "latest_event_sha256": previous,
    }


def record_pilot_evidence(
    job_dir: Path,
    payload: dict[str, Any],
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    job_dir = Path(job_dir)
    normalized = _normalize_input(payload)
    metrics = derive_job_metrics(job_dir, normalized)
    chain = verify_pilot_event_chain(job_dir)
    if not chain["ok"]:
        raise ValueError("Pilot event ledger integrity check failed")

    event = {
        "schema_version": PILOT_SCHEMA_VERSION,
        "event_id": str(uuid.uuid4()),
        "recorded_at": recorded_at or _utc_now(),
        "notice_version": PILOT_NOTICE_VERSION,
        "previous_event_sha256": chain["latest_event_sha256"],
        "evidence_eligible": normalized["data_classification"] == "consented_private",
        "pilot_outcome": normalized,
        "derived_job_metrics": metrics,
    }
    event["event_sha256"] = _event_sha256(event)

    pilot_dir = job_dir / PILOT_DIR_NAME
    pilot_dir.mkdir(parents=True, exist_ok=True)
    events_path = pilot_dir / PILOT_EVENTS_FILENAME
    with events_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(event) + "\n")
        handle.flush()
    _atomic_write_json(pilot_dir / PILOT_LATEST_FILENAME, event)
    return event


def load_pilot_evidence(job_dir: Path) -> dict[str, Any]:
    latest_path = Path(job_dir) / PILOT_DIR_NAME / PILOT_LATEST_FILENAME
    if not latest_path.is_file():
        return {"recorded": False, "integrity": verify_pilot_event_chain(job_dir)}
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    return {
        "recorded": True,
        "latest": payload,
        "integrity": verify_pilot_event_chain(job_dir),
    }


def _mean(values: Iterable[float | int | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 3)


def _sum_reference(records: list[dict[str, Any]], field: str) -> int:
    return sum(
        int(record["pilot_outcome"]["reference_evaluation"].get(field, 0))
        for record in records
    )


def build_pilot_summary(
    storage_root: Path,
    *,
    cohort_code: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(storage_root)
    requested_cohort = (
        _required_code("cohort_code", cohort_code) if cohort_code is not None else None
    )
    scanned = 0
    synthetic = 0
    eligible: list[dict[str, Any]] = []
    if root.is_dir():
        for job_dir in root.iterdir():
            if not job_dir.is_dir() or job_dir.name.startswith("_"):
                continue
            evidence = load_pilot_evidence(job_dir)
            if not evidence.get("recorded"):
                continue
            scanned += 1
            latest = evidence.get("latest") or {}
            outcome = latest.get("pilot_outcome") or {}
            if outcome.get("data_classification") == "synthetic":
                synthetic += 1
                continue
            if not latest.get("evidence_eligible") or not evidence["integrity"].get("ok"):
                continue
            if requested_cohort and outcome.get("cohort_code") != requested_cohort:
                continue
            eligible.append(latest)

    participant_counts: dict[str, int] = {}
    for record in eligible:
        code = record["pilot_outcome"]["participant_code"]
        participant_counts[code] = participant_counts.get(code, 0) + 1
    participant_count = len(participant_counts)
    repeat_participants = sum(count >= 2 for count in participant_counts.values())
    completed = sum(
        record["pilot_outcome"]["task_status"] == "completed" for record in eligible
    )
    usable = sum(record["pilot_outcome"]["transcript_usable"] for record in eligible)
    would_reuse = sum(record["pilot_outcome"]["would_use_again"] for record in eligible)

    total_audio_hours = sum(
        float(record["derived_job_metrics"].get("audio_hours") or 0)
        for record in eligible
    )
    total_segments = sum(
        int(record["derived_job_metrics"].get("segment_count") or 0)
        for record in eligible
    )
    total_corrections = sum(
        int(record["derived_job_metrics"].get("correction_count") or 0)
        for record in eligible
    )
    total_review_minutes = sum(
        float(record["pilot_outcome"].get("review_minutes") or 0)
        for record in eligible
    )
    provider_cost = sum(
        float(record["derived_job_metrics"].get("provider_cost_usd") or 0)
        for record in eligible
    )
    delivery_cost = sum(
        float(record["derived_job_metrics"].get("total_delivery_cost_usd") or 0)
        for record in eligible
    )
    revenue = sum(
        float(record["derived_job_metrics"].get("price_charged_usd") or 0)
        for record in eligible
    )
    gross_margin = revenue - delivery_cost

    dialect_total = _sum_reference(eligible, "dialect_units_total")
    dialect_correct = _sum_reference(eligible, "dialect_units_correct")
    meaning_total = _sum_reference(eligible, "meaning_units_total")
    meaning_correct = _sum_reference(eligible, "meaning_units_correct")
    critical_total = _sum_reference(eligible, "critical_terms_total")
    critical_correct = _sum_reference(eligible, "critical_terms_correct")
    speaker_turns = _sum_reference(eligible, "speaker_turns_total")
    speaker_errors = _sum_reference(eligible, "speaker_attribution_errors")

    generated = generated_at or _utc_now()
    generated_date = date.fromisoformat(generated[:10])
    overdue = sum(
        date.fromisoformat(record["pilot_outcome"]["retention_review_due_on"])
        < generated_date
        for record in eligible
    )
    blocker_counts: dict[str, int] = {}
    for record in eligible:
        blocker = record["pilot_outcome"]["primary_blocker"]
        blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1

    return {
        "schema_version": PILOT_SUMMARY_SCHEMA_VERSION,
        "generated_at": generated,
        "cohort_code": requested_cohort,
        "privacy": {
            "contains_audio": False,
            "contains_transcript_text": False,
            "contains_participant_codes": False,
            "contains_consent_record_ids": False,
            "synthetic_records_excluded": synthetic,
        },
        "sample": {
            "records_scanned": scanned,
            "eligible_jobs": len(eligible),
            "participants": participant_count,
        },
        "adoption": {
            "completion_rate_percent": _percent(completed, len(eligible)),
            "transcript_usable_rate_percent": _percent(usable, len(eligible)),
            "would_use_again_rate_percent": _percent(would_reuse, len(eligible)),
            "repeat_participants": repeat_participants,
            "repeat_participant_rate_percent": _percent(
                repeat_participants, participant_count
            ),
        },
        "correction_effort": {
            "total_review_minutes": round(total_review_minutes, 3),
            "review_minutes_per_audio_hour": _per_audio_hour(
                total_review_minutes, total_audio_hours
            ),
            "total_corrections": total_corrections,
            "corrections_per_100_segments": _percent(
                total_corrections, total_segments
            ),
        },
        "accuracy": {
            "dialect_reference_accuracy_percent": _percent(
                dialect_correct, dialect_total
            ),
            "meaning_unit_accuracy_percent": _percent(meaning_correct, meaning_total),
            "critical_term_accuracy_percent": _percent(
                critical_correct, critical_total
            ),
            "speaker_attribution_error_percent": _percent(
                speaker_errors, speaker_turns
            ),
            "reference_counts": {
                "dialect_units_total": dialect_total,
                "meaning_units_total": meaning_total,
                "critical_terms_total": critical_total,
                "speaker_turns_total": speaker_turns,
            },
        },
        "unit_economics": {
            "total_audio_hours": round(total_audio_hours, 6),
            "provider_cost_usd": round(provider_cost, 6),
            "total_delivery_cost_usd": round(delivery_cost, 6),
            "cost_per_reviewed_audio_hour_usd": _per_audio_hour(
                delivery_cost, total_audio_hours
            ),
            "revenue_usd": round(revenue, 6),
            "gross_margin_usd": round(gross_margin, 6),
            "gross_margin_percent": _percent(gross_margin, revenue),
        },
        "trust": {
            "average_before": _mean(
                record["pilot_outcome"]["trust_before"] for record in eligible
            ),
            "average_after": _mean(
                record["pilot_outcome"]["trust_after"] for record in eligible
            ),
            "average_change": _mean(
                record["derived_job_metrics"]["trust_change"] for record in eligible
            ),
            "average_confidence_to_share": _mean(
                record["pilot_outcome"]["confidence_to_share"]
                for record in eligible
            ),
            "primary_blocker_counts": dict(sorted(blocker_counts.items())),
        },
        "governance": {
            "retention_reviews_overdue": overdue,
            "public_excerpt_authorizations": sum(
                record["pilot_outcome"]["public_excerpt_authorized"]
                for record in eligible
            ),
            "notice_version": PILOT_NOTICE_VERSION,
        },
        "limitations": [
            "This is a small-pilot outcome summary, not a representative population benchmark.",
            "Reference accuracy depends on the quality and consistency of human annotation.",
            "Consent and retention records remain external; APMA stores only structured attestations and references.",
            "No transcript excerpt is included even when separate public use is authorised.",
        ],
    }
