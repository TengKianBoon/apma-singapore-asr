"""Strict deterministic final draft derived from a Goal H rescue comparison."""

from __future__ import annotations

from copy import deepcopy
import html
import json
from pathlib import Path
from typing import Any

from services.transcript_agreement import AGREEMENT_DISCLAIMER, pairwise_similarity
from services.transcript_alignment import PROVIDER_ORDER


FINAL_DRAFT_POLICY = (
    "GREEN windows select one existing provider candidate verbatim by deterministic "
    "representative similarity. AMBER and RED windows remain review-required. No text "
    "is synthesized, merged, translated, cleaned, or declared correct."
)


def _available_candidates(
    region: dict[str, Any], provider_order: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    candidates = region.get("candidates")
    if not isinstance(candidates, dict):
        raise ValueError("Rescue region candidates must be an object")
    available: dict[str, dict[str, Any]] = {}
    for provider in provider_order:
        candidate = candidates.get(provider)
        if not isinstance(candidate, dict) or candidate.get("missing") is True:
            continue
        text = candidate.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        available[provider] = candidate
    return available


def select_deterministic_representative(
    region: dict[str, Any],
    provider_order: tuple[str, ...] = PROVIDER_ORDER,
) -> tuple[str, dict[str, Any], dict[str, float]]:
    """Return the medoid-like candidate with deterministic provider-order tie breaking."""

    available = _available_candidates(region, provider_order)
    if not available:
        raise ValueError("GREEN region has no selectable provider candidate")
    scores: dict[str, float] = {}
    for provider, candidate in available.items():
        comparisons = [
            pairwise_similarity(candidate["text"], other["text"])["derived_similarity"]
            for other_provider, other in available.items()
            if other_provider != provider
        ]
        scores[provider] = round(
            sum(comparisons) / len(comparisons) if comparisons else 1.0,
            6,
        )
    selected_provider = min(
        available,
        key=lambda provider: (-scores[provider], provider_order.index(provider)),
    )
    return selected_provider, available[selected_provider], scores


def _selected_provenance(candidate: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(
        {
            key: value
            for key, value in candidate.items()
            if key not in {"text", "missing"}
        }
    )


def build_final_draft(
    rescue_comparison: dict[str, Any], *, source_sha256: str
) -> dict[str, Any]:
    """Build a deterministic final draft without mutating or rewriting source candidates."""

    source_regions = rescue_comparison.get("regions")
    if not isinstance(source_regions, list):
        raise ValueError("Rescue comparison regions must be a list")
    seen_ids: set[str] = set()
    provider_order = tuple(rescue_comparison.get("provider_order") or PROVIDER_ORDER)
    if not provider_order or len(set(provider_order)) != len(provider_order):
        raise ValueError("Rescue provider_order must contain unique provider codes")
    windows: list[dict[str, Any]] = []
    auto_accepted = 0
    review_required = 0

    for region in source_regions:
        if not isinstance(region, dict):
            raise ValueError("Each rescue region must be an object")
        window_id = str(region.get("region_id") or "")
        if not window_id or window_id in seen_ids:
            raise ValueError("Every rescue window must have one unique region_id")
        seen_ids.add(window_id)
        start = float(region["global_start_sec"])
        end = float(region["global_end_sec"])
        if end <= start:
            raise ValueError(f"Invalid global bounds for {window_id}")
        status = str(region.get("agreement_status") or "")
        if status not in {"GREEN", "AMBER", "RED"}:
            raise ValueError(f"Unsupported derived status for {window_id}: {status}")

        provider_candidates = deepcopy(region.get("candidates"))
        window: dict[str, Any] = {
            "window_id": window_id,
            "global_start_sec": region["global_start_sec"],
            "global_end_sec": region["global_end_sec"],
            "duration_seconds": region.get("duration_seconds", end - start),
            "derived_status": status,
            "derived_status_basis": AGREEMENT_DISCLAIMER,
            "clip_id": region.get("clip_id"),
            "clip_sha256": region.get("clip_sha256"),
            "timing_authority": region.get("timing_authority"),
            "boundary": deepcopy(region.get("boundary")),
            "provider_candidates": provider_candidates,
        }
        if status == "GREEN":
            selected_provider, selected_candidate, scores = select_deterministic_representative(
                region, provider_order
            )
            selected_text = selected_candidate["text"]
            window.update(
                {
                    "final_text": selected_text,
                    "selected_text": selected_text,
                    "selected_provider": selected_provider,
                    "selected_candidate_provenance": _selected_provenance(selected_candidate),
                    "selection_method": "deterministic_representative",
                    "representative_mean_similarities": scores,
                    "auto_accepted": True,
                    "review_required": False,
                    "correctness_claimed": False,
                }
            )
            auto_accepted += 1
        else:
            window.update(
                {
                    "final_text": None,
                    "selected_text": None,
                    "selected_provider": None,
                    "selected_candidate_provenance": None,
                    "selection_method": None,
                    "representative_mean_similarities": None,
                    "auto_accepted": False,
                    "review_required": True,
                    "correctness_claimed": False,
                }
            )
            review_required += 1
        windows.append(window)

    return {
        "schema_version": "apma.final-draft.v1",
        "provider_order": list(provider_order),
        "source_rescue_comparison": {
            "schema_version": rescue_comparison.get("schema_version"),
            "sha256": source_sha256,
            "modified": False,
        },
        "policy": FINAL_DRAFT_POLICY,
        "agreement_disclaimer": AGREEMENT_DISCLAIMER,
        "statistics": {
            "total_windows": len(windows),
            "auto_accepted": auto_accepted,
            "review_required": review_required,
            "status_counts": {
                status: sum(1 for window in windows if window["derived_status"] == status)
                for status in ("GREEN", "AMBER", "RED")
            },
        },
        "windows": windows,
    }


def _format_time(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_final_draft_artifacts(final_draft: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "FINAL_DRAFT.json"
    html_path = output_dir / "FINAL_DRAFT.html"
    json_path.write_text(json.dumps(final_draft, indent=2, ensure_ascii=False), encoding="utf-8")

    provider_order = tuple(final_draft.get("provider_order") or PROVIDER_ORDER)
    rows: list[str] = []
    for window in final_draft["windows"]:
        status = str(window["derived_status"])
        if window["auto_accepted"]:
            final_cell = (
                f"<strong>AUTO-ACCEPTED EXISTING CANDIDATE — {html.escape(window['selected_provider'])}</strong>"
                f"<pre>{html.escape(window['final_text'])}</pre>"
                "<small>No correctness claim.</small>"
            )
        else:
            final_cell = (
                "<strong>REVIEW REQUIRED</strong><p>No final text has been selected, merged, or generated.</p>"
            )
        candidates = "".join(
            f"<section><h4>{html.escape(provider)}</h4><pre>{html.escape(str(window['provider_candidates'][provider].get('text') or ''))}</pre></section>"
            for provider in provider_order
        )
        rows.append(
            f'<tr class="{status.lower()}"><td>{_format_time(window["global_start_sec"])}<br>to<br>'
            f'{_format_time(window["global_end_sec"])}</td><td><strong>{status}</strong><br>'
            f"DERIVED agreement only</td><td>{final_cell}</td><td>{candidates}</td></tr>"
        )

    page = """<!doctype html><html><head><meta charset="utf-8"><title>APMA Strict Final Draft</title>
<style>body{font-family:Arial,sans-serif;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #aeb6bf;padding:10px;vertical-align:top}th{background:#1f4e78;color:#fff}pre{white-space:pre-wrap;word-break:break-word;font:14px/1.45 Arial,sans-serif}.green td:nth-child(2){background:#c6efce}.amber td:nth-child(2){background:#ffeb9c}.red td:nth-child(2){background:#ffc7ce}.note{padding:12px;background:#eef3f8;border-left:4px solid #1f4e78}section{border-top:1px solid #d5d8dc}section:first-child{border-top:0}h4{margin-bottom:4px}</style>
</head><body><h1>APMA Goal I — Strict Final Draft</h1>"""
    page += (
        '<p class="note">GREEN means derived provider agreement, not correctness. GREEN text is one '
        "unchanged provider candidate selected deterministically. AMBER and RED remain review-required. "
        "No wording was generated, merged, translated, or cleaned.</p>"
        "<table><thead><tr><th>TIME</th><th>STATUS</th><th>FINAL DRAFT / REVIEW REQUIRED</th>"
        "<th>PROVIDER CANDIDATES</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )
    html_path.write_text(page, encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


__all__ = [
    "FINAL_DRAFT_POLICY",
    "build_final_draft",
    "select_deterministic_representative",
    "write_final_draft_artifacts",
]
