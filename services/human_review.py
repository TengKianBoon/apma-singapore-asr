"""File-backed human exception review for a strict APMA final draft."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
from typing import Any

from services.transcript_alignment import PROVIDER_ORDER


DECISION_TYPES = {"provider_candidate", "manual_correction", "unclear"}
SPEAKER_DECISION_TYPES = {"confirmed", "uncertain", "overlap", "not_applicable"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _format_time(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def _merged_duration(windows: list[dict[str, Any]]) -> float:
    intervals = sorted(
        (
            float(window.get("global_start_sec", 0.0)),
            float(window.get("global_end_sec", 0.0)),
        )
        for window in windows
        if float(window.get("global_end_sec", 0.0))
        > float(window.get("global_start_sec", 0.0))
    )
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
            continue
        total += current_end - current_start
        current_start, current_end = start, end
    return total + current_end - current_start


def speaker_sensitive_window_ids(overlay: dict[str, Any]) -> set[str]:
    """Return windows where provider-native diarization needs human attention."""

    selected: set[str] = set()
    for item in overlay.get("windows") or []:
        if not isinstance(item, dict):
            continue
        evidence = item.get("speaker_evidence")
        if not isinstance(evidence, dict):
            continue
        segments = [
            segment
            for segment in evidence.get("segments") or []
            if isinstance(segment, dict)
        ]
        speaker_ids = {
            str(segment.get("speaker_id") or segment.get("provider_speaker") or "")
            for segment in segments
            if str(segment.get("speaker_id") or segment.get("provider_speaker") or "")
        }
        missing_label = any(
            not str(segment.get("speaker_id") or segment.get("provider_speaker") or "")
            for segment in segments
        )
        overlaps = any(
            float(left.get("global_end_sec", 0.0) or 0.0)
            > float(right.get("global_start_sec", 0.0) or 0.0)
            and str(left.get("speaker_id") or left.get("provider_speaker") or "")
            != str(right.get("speaker_id") or right.get("provider_speaker") or "")
            for index, left in enumerate(segments)
            for right in segments[index + 1 :]
        )
        window_id = str(item.get("window_id") or "")
        if window_id and (len(speaker_ids) > 1 or missing_label or overlaps):
            selected.add(window_id)
    return selected


class ReviewWorkspace:
    """One local, single-user review workspace backed by JSON files."""

    def __init__(
        self,
        final_draft_path: Path,
        audio_dir: Path,
        output_dir: Path,
        *,
        targeted_verification_requested: bool = False,
        source_audio_seconds: float | None = None,
        speaker_review_window_ids: set[str] | None = None,
    ):
        self.final_draft_path = Path(final_draft_path).resolve()
        self.audio_dir = Path(audio_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        if not self.final_draft_path.is_file():
            raise FileNotFoundError(f"FINAL_DRAFT.json not found: {self.final_draft_path}")
        if not self.audio_dir.is_dir():
            raise FileNotFoundError(f"Review audio directory not found: {self.audio_dir}")
        self._source_bytes = self.final_draft_path.read_bytes()
        self.source_sha256 = _sha256_bytes(self._source_bytes)
        self.source = json.loads(self._source_bytes.decode("utf-8"))
        self.targeted_verification_requested = bool(targeted_verification_requested)
        self.source_audio_seconds = (
            None if source_audio_seconds is None else max(0.0, float(source_audio_seconds))
        )
        if self.source.get("schema_version") != "apma.final-draft.v1":
            raise ValueError("Review input must use apma.final-draft.v1")
        self.provider_order = tuple(self.source.get("provider_order") or PROVIDER_ORDER)
        if not self.provider_order or len(set(self.provider_order)) != len(
            self.provider_order
        ):
            raise ValueError("FINAL_DRAFT provider_order must contain unique provider codes")
        windows = self.source.get("windows")
        if not isinstance(windows, list):
            raise ValueError("FINAL_DRAFT windows must be a list")
        ids = [str(window.get("window_id") or "") for window in windows]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ValueError("FINAL_DRAFT window_id values must be unique")
        self._windows = {window_id: window for window_id, window in zip(ids, windows)}
        requested_speaker_windows = set(speaker_review_window_ids or set())
        unknown_speaker_windows = requested_speaker_windows.difference(self._windows)
        if unknown_speaker_windows:
            raise ValueError("Speaker-review window IDs must exist in FINAL_DRAFT")
        self.speaker_review_window_ids = requested_speaker_windows

    @property
    def decisions_path(self) -> Path:
        return self.output_dir / "review_decisions.json"

    @property
    def reviewed_json_path(self) -> Path:
        return self.output_dir / "FINAL_REVIEWED.json"

    @property
    def reviewed_html_path(self) -> Path:
        return self.output_dir / "FINAL_REVIEWED.html"

    @property
    def speaker_decisions_path(self) -> Path:
        return self.output_dir / "speaker_review_decisions.json"

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.decisions_path.is_file():
            return {}
        payload = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "apma.review-decisions.v1":
            raise ValueError("Unsupported review decision schema")
        if payload.get("source_final_draft_sha256") != self.source_sha256:
            raise ValueError("Saved decisions do not match the current FINAL_DRAFT source")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Saved review decisions must be a list")
        result: dict[str, dict[str, Any]] = {}
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Saved review decision must be an object")
            window_id = str(decision.get("window_id") or "")
            if window_id not in self._windows or window_id in result:
                raise ValueError("Saved review decision has an invalid or duplicate window_id")
            result[window_id] = decision
        return result

    def _write_decisions(self, decisions: dict[str, dict[str, Any]]) -> None:
        ordered = [
            decisions[window["window_id"]]
            for window in self.source["windows"]
            if window["window_id"] in decisions
        ]
        _atomic_json(
            self.decisions_path,
            {
                "schema_version": "apma.review-decisions.v1",
                "source_final_draft_sha256": self.source_sha256,
                "decisions": ordered,
            },
        )

    def _load_speaker_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.speaker_decisions_path.is_file():
            return {}
        payload = json.loads(self.speaker_decisions_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "apma.speaker-review-decisions.v1":
            raise ValueError("Unsupported speaker review decision schema")
        if payload.get("source_final_draft_sha256") != self.source_sha256:
            raise ValueError("Saved speaker decisions do not match the current FINAL_DRAFT source")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Saved speaker review decisions must be a list")
        result: dict[str, dict[str, Any]] = {}
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Saved speaker review decision must be an object")
            window_id = str(decision.get("window_id") or "")
            if window_id not in self._windows or window_id in result:
                raise ValueError(
                    "Saved speaker review decision has an invalid or duplicate window_id"
                )
            result[window_id] = decision
        return result

    def _write_speaker_decisions(
        self, decisions: dict[str, dict[str, Any]]
    ) -> None:
        ordered = [
            decisions[window["window_id"]]
            for window in self.source["windows"]
            if window["window_id"] in decisions
        ]
        _atomic_json(
            self.speaker_decisions_path,
            {
                "schema_version": "apma.speaker-review-decisions.v1",
                "source_final_draft_sha256": self.source_sha256,
                "decisions": ordered,
            },
        )

    def _audio_path(self, window_id: str) -> Path:
        if window_id not in self._windows:
            raise ValueError(f"Unknown review window: {window_id}")
        index = next(
            index
            for index, window in enumerate(self.source["windows"], start=1)
            if window["window_id"] == window_id
        )
        path = (self.audio_dir / f"chunk-{index:05d}.wav").resolve()
        try:
            path.relative_to(self.audio_dir)
        except ValueError as exc:
            raise ValueError("Review audio path escapes its configured directory") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Exact review clip is missing for {window_id}")
        expected_sha = str(self._windows[window_id].get("clip_sha256") or "")
        actual_sha = _sha256_file(path)
        if not expected_sha or actual_sha != expected_sha:
            raise ValueError(f"Review audio SHA-256 does not match {window_id}")
        return path

    def audio(self, window_id: str) -> tuple[Path, dict[str, Any]]:
        window = self._windows.get(window_id)
        if window is None:
            raise ValueError(f"Unknown review window: {window_id}")
        path = self._audio_path(window_id)
        return path, {
            "window_id": window_id,
            "global_start_sec": window["global_start_sec"],
            "global_end_sec": window["global_end_sec"],
            "clip_sha256": window["clip_sha256"],
            "timing_authority": window.get("timing_authority"),
        }

    def save_decision(
        self,
        window_id: str,
        decision_type: str,
        *,
        selected_provider: str | None = None,
        final_text: str | None = None,
        reviewed_at: str | None = None,
    ) -> dict[str, Any]:
        window = self._windows.get(window_id)
        if window is None:
            raise ValueError(f"Unknown review window: {window_id}")
        if not window.get("review_required"):
            raise ValueError("GREEN auto-accepted windows do not require a Goal J decision")
        if decision_type not in DECISION_TYPES:
            raise ValueError(
                "decision_type must be provider_candidate, manual_correction, or unclear"
            )
        candidates = window.get("provider_candidates")
        if not isinstance(candidates, dict):
            raise ValueError("Review window provider candidates are missing")

        if decision_type == "provider_candidate":
            if selected_provider not in self.provider_order:
                raise ValueError("selected_provider must identify a retained provider candidate")
            candidate = candidates.get(selected_provider)
            if not isinstance(candidate, dict) or not isinstance(candidate.get("text"), str):
                raise ValueError("Selected provider candidate is unavailable")
            accepted_text = candidate["text"]
            selected_provenance = deepcopy(
                {key: value for key, value in candidate.items() if key not in {"text", "missing"}}
            )
        elif decision_type == "manual_correction":
            if not isinstance(final_text, str) or not final_text.strip():
                raise ValueError("manual_correction final_text must contain text")
            accepted_text = final_text
            selected_provider = None
            selected_provenance = None
        else:
            accepted_text = None
            selected_provider = None
            selected_provenance = None

        decision = {
            "window_id": window_id,
            "global_start_sec": window["global_start_sec"],
            "global_end_sec": window["global_end_sec"],
            "decision_type": decision_type,
            "selected_provider": selected_provider,
            "final_text": accepted_text,
            "final_text_sha256": (
                None
                if accepted_text is None
                else _sha256_bytes(accepted_text.encode("utf-8"))
            ),
            "reviewed_at": reviewed_at or _utc_now(),
            "selected_candidate_provenance": selected_provenance,
            "original_candidates": deepcopy(candidates),
            "reason_code": "audio_still_unclear" if decision_type == "unclear" else None,
        }
        decisions = self._load_decisions()
        decisions[window_id] = decision
        self._write_decisions(decisions)
        self.write_reviewed_outputs(decisions)
        return deepcopy(decision)

    def save_speaker_decision(
        self,
        window_id: str,
        speaker_status: str,
        *,
        reviewed_at: str | None = None,
    ) -> dict[str, Any]:
        window = self._windows.get(window_id)
        if window is None:
            raise ValueError(f"Unknown review window: {window_id}")
        if (
            not window.get("review_required")
            and window_id not in self.speaker_review_window_ids
        ):
            raise ValueError("This window is not selected for speaker verification")
        if speaker_status not in SPEAKER_DECISION_TYPES:
            raise ValueError(
                "speaker_status must be confirmed, uncertain, overlap, or not_applicable"
            )
        decision = {
            "window_id": window_id,
            "global_start_sec": window["global_start_sec"],
            "global_end_sec": window["global_end_sec"],
            "speaker_status": speaker_status,
            "reviewed_at": reviewed_at or _utc_now(),
            "provider_speaker_evidence_modified": False,
        }
        decisions = self._load_speaker_decisions()
        decisions[window_id] = decision
        self._write_speaker_decisions(decisions)
        self.write_reviewed_outputs(speaker_decisions=decisions)
        return deepcopy(decision)

    def _verification_summary(
        self,
        decisions: dict[str, dict[str, Any]],
        speaker_decisions: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        source_windows = list(self.source["windows"])
        content_selected = [
            window for window in source_windows if window.get("review_required")
        ]
        content_selected_ids = {
            str(window["window_id"]) for window in content_selected
        }
        selected = [
            window
            for window in source_windows
            if str(window["window_id"]) in content_selected_ids
            or str(window["window_id"]) in self.speaker_review_window_ids
        ]
        selected_ids = {str(window["window_id"]) for window in selected}
        required_speaker_ids = (
            selected_ids
            if self.targeted_verification_requested
            else selected_ids.intersection(speaker_decisions)
        )
        selected_seconds = _merged_duration(selected)
        source_seconds = self.source_audio_seconds
        if not source_seconds:
            source_seconds = _merged_duration(source_windows)
        content_counts = {
            decision_type: sum(
                1
                for window_id, decision in decisions.items()
                if window_id in selected_ids
                and decision.get("decision_type") == decision_type
            )
            for decision_type in sorted(DECISION_TYPES)
        }
        speaker_counts = {
            status: sum(
                1
                for window_id, decision in speaker_decisions.items()
                if window_id in selected_ids
                and decision.get("speaker_status") == status
            )
            for status in sorted(SPEAKER_DECISION_TYPES)
        }
        content_reviewed = sum(content_counts.values())
        content_resolved = (
            content_counts["provider_candidate"] + content_counts["manual_correction"]
        )
        speaker_reviewed = sum(speaker_counts.values())
        unresolved = len(content_selected) - content_resolved
        speaker_attention_required = (
            sum(
                1
                for window_id in required_speaker_ids
                if speaker_decisions.get(window_id, {}).get("speaker_status")
                in {"uncertain", "overlap"}
            )
            + sum(1 for window_id in required_speaker_ids if window_id not in speaker_decisions)
        )
        windows_requiring_attention = sum(
            1
            for window in selected
            if (
                (
                    str(window["window_id"]) in content_selected_ids
                    and decisions.get(str(window["window_id"]), {}).get(
                        "decision_type"
                    )
                    not in {"provider_candidate", "manual_correction"}
                )
                or (
                    str(window["window_id"]) in required_speaker_ids
                    and speaker_decisions.get(str(window["window_id"]), {}).get(
                        "speaker_status"
                    )
                    not in {"confirmed", "not_applicable"}
                )
            )
        )
        if content_reviewed == 0 and speaker_reviewed == 0:
            completion_label = "Automated transcript"
        elif unresolved or speaker_attention_required:
            completion_label = "Selected-window human reviewed with unresolved items"
        else:
            completion_label = "Selected-window human confirmed"
        status_counts = {
            status: sum(1 for window in selected if window.get("derived_status") == status)
            for status in ("RED", "AMBER")
        }
        return {
            "requested": self.targeted_verification_requested,
            "scope": "selected_flagged_windows",
            "completion_label": completion_label,
            "coverage_statement": (
                "Only APMA-selected flagged windows were presented for human review; "
                "this is not a full-audio human review."
            ),
            "accuracy_uplift_claimed": False,
            "selected_window_count": len(selected),
            "selected_audio_seconds": round(selected_seconds, 3),
            "source_audio_seconds": round(float(source_seconds or 0.0), 3),
            "selected_audio_share_percent": (
                round((selected_seconds / float(source_seconds)) * 100.0, 2)
                if source_seconds
                else None
            ),
            "risk_status_counts": status_counts,
            "content_review_window_count": len(content_selected),
            "speaker_sensitive_window_count": len(self.speaker_review_window_ids),
            "content_decisions": {
                "reviewed": content_reviewed,
                "resolved": content_resolved,
                **content_counts,
            },
            "speaker_decisions": {
                "reviewed": speaker_reviewed,
                "pending": sum(
                    1
                    for window_id in required_speaker_ids
                    if window_id not in speaker_decisions
                ),
                **speaker_counts,
            },
            "unresolved_selected_windows": unresolved,
            "speaker_attention_required": speaker_attention_required,
            "windows_requiring_attention": windows_requiring_attention,
        }

    def build_reviewed(
        self,
        decisions: dict[str, dict[str, Any]] | None = None,
        speaker_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        decisions = decisions if decisions is not None else self._load_decisions()
        speaker_decisions = (
            speaker_decisions
            if speaker_decisions is not None
            else self._load_speaker_decisions()
        )
        windows: list[dict[str, Any]] = []
        resolved = 0
        for source_window in self.source["windows"]:
            window = deepcopy(source_window)
            window_id = window["window_id"]
            content_review_selected = bool(source_window.get("review_required"))
            speaker_review_selected = window_id in self.speaker_review_window_ids
            window["targeted_verification_selection"] = {
                "selected": content_review_selected or speaker_review_selected,
                "content_review": content_review_selected,
                "speaker_review": speaker_review_selected,
            }
            if window.get("auto_accepted"):
                selection_method = str(window.get("selection_method") or "")
                assisted = selection_method == "gpt56_sol_assisted_reconciliation"
                window["review_resolution"] = {
                    "decision_type": (
                        "auto_accepted_derived_reconciliation"
                        if assisted
                        else "auto_accepted_provider_candidate"
                    ),
                    "selected_provider": window.get("selected_provider"),
                    "final_text": window.get("final_text"),
                    "reviewed_at": None,
                    "source": selection_method or "deterministic representative",
                    "correctness_claimed": False,
                }
                resolved += 1
            elif (
                window_id in decisions
                and decisions[window_id].get("decision_type") != "unclear"
            ):
                decision = deepcopy(decisions[window_id])
                window["final_text"] = decision["final_text"]
                window["review_required"] = False
                window["review_resolution"] = decision
                resolved += 1
            elif window_id in decisions:
                window["final_text"] = None
                window["review_required"] = True
                window["review_resolution"] = deepcopy(decisions[window_id])
            else:
                window["final_text"] = None
                window["review_resolution"] = None
            window["speaker_review_resolution"] = deepcopy(
                speaker_decisions.get(window_id)
            )
            windows.append(window)
        verification = self._verification_summary(decisions, speaker_decisions)
        return {
            "schema_version": "apma.final-reviewed.v1",
            "provider_order": list(self.provider_order),
            "source_final_draft": {
                "sha256": self.source_sha256,
                "modified": False,
            },
            "statistics": {
                "total_windows": len(windows),
                "resolved_windows": resolved,
                "unresolved_windows": len(windows) - resolved,
                "human_review_decisions": len(decisions),
            },
            "targeted_verification": verification,
            "windows": windows,
        }

    def write_reviewed_outputs(
        self,
        decisions: dict[str, dict[str, Any]] | None = None,
        speaker_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        payload = self.build_reviewed(decisions, speaker_decisions)
        _atomic_json(self.reviewed_json_path, payload)
        temporary = self.reviewed_html_path.with_suffix(".html.tmp")
        temporary.write_text(render_reviewed_html(payload), encoding="utf-8")
        temporary.replace(self.reviewed_html_path)
        return {
            "json": str(self.reviewed_json_path),
            "html": str(self.reviewed_html_path),
        }

    def view(self) -> dict[str, Any]:
        decisions = self._load_decisions()
        speaker_decisions = self._load_speaker_decisions()
        reviewed = self.build_reviewed(decisions, speaker_decisions)
        windows = []
        for window in reviewed["windows"]:
            item = deepcopy(window)
            item["audio_url"] = f"/api/review/audio/{window['window_id']}"
            windows.append(item)
        return {
            "source_final_draft_sha256": self.source_sha256,
            "provider_order": list(self.provider_order),
            "statistics": reviewed["statistics"],
            "targeted_verification": reviewed["targeted_verification"],
            "windows": windows,
            "outputs": {
                "json": str(self.reviewed_json_path),
                "html": str(self.reviewed_html_path),
            },
        }


def render_reviewed_html(payload: dict[str, Any]) -> str:
    provider_order = tuple(payload.get("provider_order") or PROVIDER_ORDER)
    rows = []
    for window in payload["windows"]:
        resolution = window.get("review_resolution")
        final_text = resolution.get("final_text") if isinstance(resolution, dict) else None
        decision = resolution.get("decision_type") if isinstance(resolution, dict) else "review_required"
        candidates = "".join(
            f"<h4>{html.escape(provider)}</h4><pre>{html.escape(str(window['provider_candidates'][provider].get('text') or ''))}</pre>"
            for provider in provider_order
        )
        rows.append(
            f"<tr><td>{_format_time(window['global_start_sec'])}<br>to<br>{_format_time(window['global_end_sec'])}</td>"
            f"<td>{html.escape(str(window['derived_status']))}</td><td>{html.escape(str(decision))}"
            f"<pre>{html.escape(str(final_text or 'REVIEW REQUIRED'))}</pre></td><td>{candidates}</td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>APMA Final Reviewed</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #bbb;padding:10px;vertical-align:top}th{background:#1f4e78;color:#fff}pre{white-space:pre-wrap;word-break:break-word}</style>"
        "</head><body><h1>APMA FINAL REVIEWED</h1><p>Human decisions are recorded with unchanged source candidates and provenance.</p>"
        "<table><thead><tr><th>TIME</th><th>STATUS</th><th>FINAL TEXT / DECISION</th><th>ORIGINAL PROVIDER CANDIDATES</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


REVIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APMA Human Exception Review</title>
  <style>
    :root{font-family:Arial,sans-serif;color:#17202a;background:#f5f7f9}*{box-sizing:border-box}
    body{margin:0}header,main{width:min(1180px,calc(100% - 32px));margin:auto}header{padding:24px 0 16px}
    h1{margin:0 0 6px}.notice{padding:12px;border-left:4px solid #1f6feb;background:#eaf2fd;line-height:1.45}
    #summary{margin:14px 0}.verification-summary{background:#e8f5f2;border:1px solid #a9d4ca;border-radius:8px;padding:14px;line-height:1.5}.verification-summary strong{display:block;font-size:17px}.verification-summary span{display:block;color:#385b56;font-size:13px;margin-top:4px}.window,#speaker-mapping{background:#fff;border:1px solid #d8e0e6;border-radius:8px;margin:14px 0;padding:16px}
    .review-toolbar{position:sticky;top:0;z-index:4;display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:rgba(245,247,249,.96);padding:10px 0;border-bottom:1px solid #d8e0e6;backdrop-filter:blur(8px)}.review-toolbar button{background:#fff;color:#344054;border:1px solid #cbd5e1}.review-toolbar button[aria-pressed="true"]{background:#17202a;color:#fff;border-color:#17202a}.review-toolbar .next{margin-left:auto;background:#1f6feb;color:#fff;border-color:#1f6feb}.visible-count{font-size:13px;color:#57606a}.window:focus{outline:3px solid rgba(31,111,235,.25);outline-offset:3px}.empty-review{padding:28px;text-align:center;background:#fff;border:1px dashed #aeb8c2;border-radius:8px;margin-top:14px;color:#57606a}
    .window-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.status{font-weight:bold;padding:5px 8px;border-radius:5px}
    .GREEN{background:#c6efce}.AMBER{background:#ffeb9c}.RED{background:#ffc7ce}.state{margin:12px 0;padding:10px;background:#f6f8fa;border-left:4px solid #7f8c8d}.state small{display:block;margin-top:4px;color:#57606a}
    .candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.candidate{border:1px solid #d8e0e6;padding:10px;min-width:0}
    pre,textarea{white-space:pre-wrap;word-break:break-word;font:14px/1.45 Arial,sans-serif}pre{margin:6px 0}audio{width:100%;margin:10px 0}.audio-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:-2px 0 10px}.audio-tools button,.audio-tools select{background:#fff;color:#344054;border:1px solid #cbd5e1;padding:7px 9px;border-radius:6px;font-weight:bold}.speaker-check{margin-top:12px;padding:12px;border:1px solid #b8d7d1;border-radius:7px;background:#f0faf7}.speaker-check h3{margin:0 0 4px}.speaker-check p{margin:0 0 8px;color:#48615e;font-size:13px}.speaker-check button{background:#20766b}.speaker-check button.attention{background:#a85d16}.unclear{background:#9a6700}
    .actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}button{border:0;border-radius:6px;padding:9px 12px;background:#1f6feb;color:#fff;font-weight:bold;cursor:pointer}
    button:disabled{background:#9aa4af;cursor:not-allowed}.reconcile{background:#fff;border:2px solid #1f6feb;border-radius:8px;margin:0 0 16px;padding:16px}.reconcile h2{margin-top:0}.cost{font-weight:bold;margin:10px 0}.approve{display:flex;gap:8px;align-items:flex-start;margin:12px 0}
    button.manual{background:#5b6570}textarea{width:100%;min-height:100px;border:1px solid #aeb8c2;border-radius:6px;padding:10px}.saved{color:#1a7f64;font-weight:bold}.error{color:#b42318;font-weight:bold}
    .mapping-row{display:grid;grid-template-columns:minmax(160px,1fr) minmax(160px,1fr) minmax(190px,1fr) auto;gap:8px;align-items:end;margin:10px 0}.mapping-row label{font-size:12px;font-weight:bold}.mapping-row input{display:block;width:100%;margin-top:4px;padding:8px;border:1px solid #aeb8c2;border-radius:5px}.scope{font-size:12px;color:#57606a;word-break:break-all}
    @media(max-width:800px){.candidates,.mapping-row{grid-template-columns:1fr}.window-head{display:block}.status{display:inline-block;margin-top:8px}.review-toolbar .next{margin-left:0}}
  </style>
</head>
<body>
  <header><h1>APMA Targeted Human Verification</h1><p class="notice">AI identifies where attention is needed; you listen only to the relevant exact clips and verify the words and speakers before release. Selected-window verification does not mean the full recording was manually reviewed. If GPT-5.6 Sol assistance is used, its proposed draft is clearly labelled and never replaces the original provider evidence.</p></header>
  <main><section id="reconciliation" class="reconcile" hidden><h2>Reduce this review with GPT-5.6 Sol</h2><p>Instead of reviewing over one thousand mismatched rows, APMA gives GPT-5.6 Sol the three transcripts for each <strong>same exact audio chunk</strong>. GPT-5.6 Sol reads text only; it does not hear the audio. It prepares a proposed draft and leaves uncertain chunks for you to hear and correct below.</p><div id="reconciliation-status">Calculating the estimate…</div><label class="approve"><input id="approve-reconciliation" type="checkbox"><span id="approve-reconciliation-label">I approve this one GPT-5.6 Sol run using the exact displayed estimate plus 15% reserve.</span></label><button id="run-reconciliation" disabled>Run GPT-5.6 Sol assisted comparison</button></section><div id="summary">Loading review windows…</div><div id="message" aria-live="polite"></div><nav class="review-toolbar" aria-label="Review filters"><button type="button" data-filter="unresolved" aria-pressed="true">Needs review</button><button type="button" data-filter="RED" aria-pressed="false">Red</button><button type="button" data-filter="AMBER" aria-pressed="false">Amber</button><button type="button" data-filter="resolved" aria-pressed="false">Resolved</button><button type="button" data-filter="all" aria-pressed="false">All</button><span class="visible-count" id="visible-count"></span><button class="next" id="next-window" type="button">Next visible area</button></nav><section id="speaker-mapping" hidden><h2>Human speaker labels</h2><p>Enter a meeting label only when you know it. APMA always keeps the original provider ID in brackets. A meeting speaker ID may be reused manually across separately scoped runs; APMA does not infer that match.</p><div id="speaker-mapping-rows"></div></section><div id="windows"></div></main>
  <script>
    let providers = [];
    let reviewPayload = null;
    let activeFilter = "unresolved";
    let nextVisibleIndex = -1;
    const windowsBox = document.getElementById("windows");
    const summary = document.getElementById("summary");
    const message = document.getElementById("message");
    const speakerMapping = document.getElementById("speaker-mapping");
    const speakerMappingRows = document.getElementById("speaker-mapping-rows");
    const reconciliation = document.getElementById("reconciliation");
    const reconciliationStatus = document.getElementById("reconciliation-status");
    const approveReconciliation = document.getElementById("approve-reconciliation");
    const runReconciliationButton = document.getElementById("run-reconciliation");
    const visibleCount = document.getElementById("visible-count");
    const nextWindowButton = document.getElementById("next-window");
    const jobId = new URLSearchParams(window.location.search).get("job_id");
    const reviewQuery = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    let reconciliationEstimate = null;

    function timeLabel(value) {
      const total = Math.round(Number(value) * 1000);
      const h = Math.floor(total / 3600000);
      const m = Math.floor((total % 3600000) / 60000);
      const s = Math.floor((total % 60000) / 1000);
      const ms = total % 1000;
      return [h,m,s].map((item) => String(item).padStart(2,"0")).join(":") + "." + String(ms).padStart(3,"0");
    }

    function textElement(tag, text, className) {
      const node = document.createElement(tag);
      node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    async function api(path, options={}) {
      const response = await fetch(path, options);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Review request failed");
      return payload;
    }

    async function saveDecision(windowId, decisionType, selectedProvider, finalText) {
      message.textContent = "Saving review decision…";
      message.className = "";
      try {
        await api("/api/review/decisions" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, window_id:windowId, decision_type:decisionType, selected_provider:selectedProvider, final_text:finalText})
        });
        message.textContent = "Decision saved. It will remain after refresh or reopen.";
        message.className = "saved";
        await loadReview();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }

    async function saveSpeakerDecision(windowId, speakerStatus) {
      message.textContent = "Saving speaker verification…";
      message.className = "";
      try {
        await api("/api/review/speaker-decisions" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, window_id:windowId, speaker_status:speakerStatus})
        });
        message.textContent = "Speaker verification saved. Provider-native speaker evidence remains unchanged.";
        message.className = "saved";
        await loadReview();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }

    function dollars(value) {
      return "$" + Number(value || 0).toFixed(2);
    }

    async function loadReconciliationEstimate() {
      if (!jobId) return;
      try {
        reconciliationEstimate = await api("/api/quality/reconcile/estimate" + reviewQuery);
        reconciliation.hidden = false;
        if (reconciliationEstimate.already_completed) {
          const active = reconciliationEstimate.active_reconciliation || {};
          reconciliationStatus.textContent = `GPT-5.6 Sol assisted comparison is active: ${active.window_count || reconciliationEstimate.window_count} exact audio chunks, ${active.review_required || 0} requiring human review.`;
          approveReconciliation.hidden = true;
          runReconciliationButton.disabled = true;
          runReconciliationButton.textContent = "GPT-5.6 Sol comparison completed";
          return;
        }
        reconciliationStatus.textContent = `${reconciliationEstimate.window_count} exact audio chunks · estimated GPT-5.6 Sol cost ${dollars(reconciliationEstimate.estimated_cost_usd)} · approval limit including 15% reserve ${dollars(reconciliationEstimate.run_budget_cap_usd)} · configured maximum ${dollars(reconciliationEstimate.max_reconciliation_cost_per_job_usd)}. No audio is sent to GPT-5.6 Sol.`;
        approveReconciliation.hidden = false;
        runReconciliationButton.disabled = !reconciliationEstimate.within_cap || !approveReconciliation.checked;
      } catch (error) {
        reconciliation.hidden = false;
        reconciliationStatus.textContent = `GPT-5.6 Sol estimate is unavailable: ${error.message}`;
        reconciliationStatus.className = "error";
        approveReconciliation.hidden = true;
        runReconciliationButton.disabled = true;
      }
    }

    async function runReconciliation() {
      if (!reconciliationEstimate || !approveReconciliation.checked) return;
      runReconciliationButton.disabled = true;
      reconciliationStatus.textContent = "GPT-5.6 Sol is comparing the three retained transcripts chunk by chunk. Completed chunks are retained if a retry is needed…";
      try {
        await api("/api/quality/reconcile/run" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, confirm_live_reconciliation:true, run_budget_cap_usd:reconciliationEstimate.run_budget_cap_usd})
        });
        window.location.reload();
      } catch (error) {
        reconciliationStatus.textContent = error.message;
        reconciliationStatus.className = "error";
        runReconciliationButton.disabled = false;
      }
    }

    async function saveSpeakerMapping(localSpeakerKey, displayName, canonicalSpeakerId) {
      message.textContent = "Saving human speaker label…";
      message.className = "";
      try {
        await api("/api/review/speaker-mappings" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, local_speaker_key:localSpeakerKey, display_name:displayName, canonical_speaker_id:canonicalSpeakerId || null})
        });
        message.textContent = "Speaker label saved. The provider ID remains visible and the label will remain after refresh.";
        message.className = "saved";
        await loadReview();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }

    function renderSpeakerMappings(payload) {
      const speakers = payload.speaker_mappings && payload.speaker_mappings.local_speakers;
      if (!Array.isArray(speakers) || !speakers.length) {
        speakerMapping.hidden = true;
        speakerMappingRows.replaceChildren();
        return;
      }
      speakerMapping.hidden = false;
      const rows = speakers.map((speaker) => {
        const row = document.createElement("div");
        row.className = "mapping-row";
        const identity = document.createElement("div");
        identity.append(textElement("strong", speaker.display_label));
        identity.append(textElement("div", `${speaker.provider_code} / ${speaker.run_id || "no run"} / ${speaker.chunk_filename || "no chunk"}`, "scope"));
        const nameLabel = textElement("label", "Human display name");
        const name = document.createElement("input");
        name.value = speaker.mapping ? speaker.mapping.display_name : "";
        name.placeholder = "Example: John or Speaker B";
        nameLabel.append(name);
        const canonicalLabel = textElement("label", "Meeting speaker ID (optional)");
        const canonical = document.createElement("input");
        canonical.value = speaker.mapping ? speaker.mapping.canonical_speaker_id : "";
        canonical.placeholder = "Auto-created if blank";
        canonicalLabel.append(canonical);
        const button = textElement("button", "Save label");
        button.addEventListener("click", () => saveSpeakerMapping(speaker.local_speaker_key, name.value, canonical.value));
        row.append(identity, nameLabel, canonicalLabel, button);
        return row;
      });
      speakerMappingRows.replaceChildren(...rows);
    }

    function renderWindow(window) {
      const card = document.createElement("article");
      card.className = "window";
      card.id = `window-${window.window_id}`;
      card.tabIndex = -1;
      const head = document.createElement("div");
      head.className = "window-head";
      head.append(textElement("h2", `${window.window_id} — ${timeLabel(window.global_start_sec)} to ${timeLabel(window.global_end_sec)}`));
      head.append(textElement("span", window.derived_status, `status ${window.derived_status}`));
      card.append(head);

      const resolution = window.review_resolution;
      const state = document.createElement("div");
      state.className = "state";
      if (resolution) {
        state.append(textElement("strong", `Current final state: ${resolution.decision_type}`));
        state.append(textElement("pre", resolution.final_text || ""));
        if (resolution.selected_provider) state.append(textElement("small", `Source provider: ${resolution.selected_provider}`));
      } else {
        state.append(textElement("strong", "Current final state: REVIEW REQUIRED"));
      }
      card.append(state);

      if (window.speaker_review_resolution) {
        const speakerState = document.createElement("div");
        speakerState.className = "state";
        speakerState.append(textElement("strong", `Speaker check: ${window.speaker_review_resolution.speaker_status.replaceAll("_", " ")}`));
        speakerState.append(textElement("small", "This human decision is separate from the retained provider speaker evidence."));
        card.append(speakerState);
      }

      const speakerEvidence = window.speaker_evidence;
      if (speakerEvidence && Array.isArray(speakerEvidence.segments) && speakerEvidence.segments.length) {
        const evidence = document.createElement("div");
        evidence.className = "state";
        evidence.append(textElement("strong", "Gem35T provider-native speaker/timing evidence"));
        speakerEvidence.segments.forEach((segment) => {
          evidence.append(textElement("pre", `${timeLabel(segment.global_start_sec)}–${timeLabel(segment.global_end_sec)} ${segment.display_label || segment.speaker_id || "UNLABELLED"}: ${segment.text || ""}`));
        });
        card.append(evidence);
      }

      const candidates = document.createElement("div");
      candidates.className = "candidates";
      providers.forEach((provider) => {
        const box = document.createElement("section");
        box.className = "candidate";
        box.append(textElement("h3", provider));
        box.append(textElement("pre", window.provider_candidates[provider].text || ""));
        candidates.append(box);
      });
      card.append(candidates);

      const selection = window.targeted_verification_selection || {
        selected: !window.auto_accepted,
        content_review: !window.auto_accepted,
        speaker_review: false
      };
      if (selection.selected) {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "metadata";
        audio.src = window.audio_url;
        card.append(audio);

        const audioTools = document.createElement("div");
        audioTools.className = "audio-tools";
        const replay = textElement("button", "Replay clip");
        replay.type = "button";
        replay.addEventListener("click", () => { audio.currentTime = 0; audio.play().catch(() => {}); });
        const speedLabel = textElement("label", "Playback speed");
        const speed = document.createElement("select");
        [["0.75×",0.75],["1×",1],["1.25×",1.25]].forEach(([label,value]) => {
          const option = document.createElement("option"); option.textContent = label; option.value = String(value); speed.append(option);
        });
        speed.value = "1";
        speed.addEventListener("change", () => { audio.playbackRate = Number(speed.value); });
        speedLabel.append(speed);
        audioTools.append(replay, speedLabel);
        card.append(audioTools);

        if (selection.content_review) {
          const actions = document.createElement("div");
          actions.className = "actions";
          providers.forEach((provider) => {
            const button = textElement("button", `Use ${provider}`);
            button.addEventListener("click", () => saveDecision(window.window_id, "provider_candidate", provider, null));
            actions.append(button);
          });
          const unclearButton = textElement("button", "Still unclear", "unclear");
          unclearButton.addEventListener("click", () => saveDecision(window.window_id, "unclear", null, null));
          actions.append(unclearButton);
          card.append(actions);

          const textarea = document.createElement("textarea");
          textarea.placeholder = "Type an exact manual correction here. No AI rewriting.";
          if (resolution && resolution.decision_type === "manual_correction") textarea.value = resolution.final_text;
          card.append(textarea);
          const manualButton = textElement("button", "Save manual correction", "manual");
          manualButton.addEventListener("click", () => saveDecision(window.window_id, "manual_correction", null, textarea.value));
          card.append(manualButton);
        }

        const speakerCheck = document.createElement("section");
        speakerCheck.className = "speaker-check";
        speakerCheck.append(textElement("h3", "Speaker verification"));
        speakerCheck.append(textElement("p", "Choose what you hear in this clip. This does not rename or overwrite the provider evidence."));
        const speakerActions = document.createElement("div");
        speakerActions.className = "actions";
        [
          ["Speaker labels sound right", "confirmed", ""],
          ["Speaker uncertain", "uncertain", "attention"],
          ["Overlapping speakers", "overlap", "attention"],
          ["No speaker check needed", "not_applicable", ""]
        ].forEach(([label,status,className]) => {
          const button = textElement("button", label, className);
          button.addEventListener("click", () => saveSpeakerDecision(window.window_id, status));
          speakerActions.append(button);
        });
        speakerCheck.append(speakerActions);
        card.append(speakerCheck);
      }
      return card;
    }

    function matchesFilter(window) {
      if (activeFilter === "all") return true;
      const selection = window.targeted_verification_selection || {
        selected: !window.auto_accepted,
        content_review: !window.auto_accepted
      };
      const speakerStatus = window.speaker_review_resolution && window.speaker_review_resolution.speaker_status;
      const speakerRequired = Boolean(
        (reviewPayload.targeted_verification || {}).requested && selection.selected
      ) || Boolean(window.speaker_review_resolution);
      const speakerResolved = !speakerRequired || speakerStatus === "confirmed" || speakerStatus === "not_applicable";
      const unresolved = (selection.content_review && Boolean(window.review_required)) || !speakerResolved;
      if (activeFilter === "unresolved") return unresolved;
      if (activeFilter === "resolved") return !unresolved;
      return window.derived_status === activeFilter;
    }

    function renderVisibleWindows() {
      if (!reviewPayload) return;
      const visible = reviewPayload.windows.filter(matchesFilter);
      nextVisibleIndex = -1;
      visibleCount.textContent = `${visible.length} shown`;
      nextWindowButton.disabled = visible.length === 0;
      if (!visible.length) {
        windowsBox.replaceChildren(textElement("div", "No areas match this filter.", "empty-review"));
        return;
      }
      windowsBox.replaceChildren(...visible.map(renderWindow));
    }

    async function loadReview() {
      try {
        const payload = await api("/api/review" + reviewQuery);
        providers = Array.isArray(payload.provider_order) && payload.provider_order.length
          ? payload.provider_order
          : ["M3ASR", "gptTr", "Gem35T"];
        const verification = payload.targeted_verification || {};
        const share = verification.selected_audio_share_percent;
        summary.replaceChildren();
        const box = document.createElement("div");
        box.className = "verification-summary";
        box.append(textElement("strong", verification.completion_label || "Automated transcript"));
        box.append(textElement("span", `${verification.selected_window_count || 0} selected clips · ${Math.round(verification.selected_audio_seconds || 0)} seconds${share === null || share === undefined ? "" : ` · ${share}% of source audio`}`));
        const content = verification.content_decisions || {};
        const speakers = verification.speaker_decisions || {};
        box.append(textElement("span", `${content.resolved || 0} content decisions resolved · ${content.unclear || 0} still unclear · ${speakers.reviewed || 0} speaker checks · ${speakers.pending || 0} speaker checks pending`));
        box.append(textElement("span", verification.coverage_statement || "Selected-window verification is not a full-audio human review."));
        summary.append(box);
        reviewPayload = payload;
        renderSpeakerMappings(payload);
        renderVisibleWindows();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }
    approveReconciliation.addEventListener("change", () => {
      runReconciliationButton.disabled = !reconciliationEstimate || !reconciliationEstimate.within_cap || !approveReconciliation.checked;
    });
    runReconciliationButton.addEventListener("click", runReconciliation);
    document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
      activeFilter = button.dataset.filter;
      document.querySelectorAll("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
      renderVisibleWindows();
    }));
    nextWindowButton.addEventListener("click", () => {
      const cards = Array.from(windowsBox.querySelectorAll(".window"));
      if (!cards.length) return;
      nextVisibleIndex = (nextVisibleIndex + 1) % cards.length;
      cards[nextVisibleIndex].scrollIntoView({behavior:"smooth", block:"start"});
      cards[nextVisibleIndex].focus({preventScroll:true});
    });
    loadReconciliationEstimate();
    loadReview();
  </script>
</body>
</html>"""


__all__ = [
    "DECISION_TYPES",
    "SPEAKER_DECISION_TYPES",
    "REVIEW_HTML",
    "ReviewWorkspace",
    "render_reviewed_html",
    "speaker_sensitive_window_ids",
]
