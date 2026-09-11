"""Application-timed selective rescue for one existing RED comparison region."""

from __future__ import annotations

from copy import copy, deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any, Callable
import wave

from services import chunker
from services import job as job_mod
from services.config import Config, paid_cost_authorization_amount
from services.transcript_agreement import AGREEMENT_DISCLAIMER, classify_comparison
from services.transcription import get_transcriber
from services.transcription.models import configure_model_chunking
from services.transcription.router import get_runtime_model_status


PROVIDER_ORDER = ("gptTr", "gpt4oDiarz", "Gem35T")
QUALITY_PROVIDER_MODELS = {
    "gptTr": "gpt-transcribe",
    "gpt4oDiarz": "gpt-4o-transcribe-diarize",
    "Gem35T": "gemini-3.5-transcribe",
}
PROVIDER_ROUTES = {
    "gptTr": ("openai", "openai_recommended_model"),
    "gpt4oDiarz": ("openai", "openai_diarize_model"),
    "Gem35T": ("gemini", "gemini_transcribe_model"),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _format_time(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def select_first_red_region(comparison: dict[str, Any]) -> dict[str, Any]:
    """Return the first existing RED region without mutating the comparison."""

    for region in comparison.get("regions") or []:
        if isinstance(region, dict) and region.get("agreement_status") == "RED":
            selected = deepcopy(region)
            start = float(selected["global_start_sec"])
            end = float(selected["global_end_sec"])
            if end <= start:
                raise ValueError("Selected RED region has invalid global bounds")
            return selected
    raise ValueError("Comparison contains no RED region")


def _extract_region_wav(
    source_audio: Path,
    output_wav: Path,
    *,
    source_global_start_sec: float,
    region_global_start_sec: float,
    region_global_end_sec: float,
    cfg: Config,
) -> None:
    local_start = region_global_start_sec - source_global_start_sec
    duration = region_global_end_sec - region_global_start_sec
    if local_start < -1e-6 or duration <= 0:
        raise ValueError("RED region is outside the supplied source timeline")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    filter_value = (
        f"atrim=start={max(0.0, local_start):.9f}:"
        f"duration={duration:.9f},asetpts=PTS-STARTPTS"
    )
    command = [
        str(cfg.ffmpeg_binary),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_audio),
        "-af",
        filter_value,
        "-ac",
        str(cfg.normalized_audio_channels),
        "-ar",
        str(cfg.normalized_audio_framerate),
        "-sample_fmt",
        "s16",
        str(output_wav),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not output_wav.is_file():
        raise RuntimeError("FFmpeg could not extract the selected comparison region")


def _wav_metadata(path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav_file:
        rate = int(wav_file.getframerate())
        frames = int(wav_file.getnframes())
        width = int(wav_file.getsampwidth())
        channels = int(wav_file.getnchannels())
    return {
        "framerate": rate,
        "n_frames": frames,
        "sampwidth": width,
        "n_channels": channels,
        "bytes_per_second": rate * width * channels,
        "processing_audio_sha256": _sha256(path),
        "processing_audio_size_bytes": path.stat().st_size,
    }


def build_rescue_windows(
    source_audio: Path,
    comparison: dict[str, Any],
    extraction_dir: Path,
    cfg: Config,
    *,
    source_global_start_sec: float,
    target_duration_sec: float = 24.0,
    min_duration_sec: float = 15.0,
    max_duration_sec: float = 30.0,
    boundary_search_window_sec: float = 6.0,
    max_windows: int = 8,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Extract and silence-split one RED region into application-timed clips."""

    selected = select_first_red_region(comparison)
    region_start = float(selected["global_start_sec"])
    region_end = float(selected["global_end_sec"])
    extraction_dir.mkdir(parents=True, exist_ok=True)
    region_wav = extraction_dir / "selected-red-region.wav"
    _extract_region_wav(
        source_audio,
        region_wav,
        source_global_start_sec=source_global_start_sec,
        region_global_start_sec=region_start,
        region_global_end_sec=region_end,
        cfg=cfg,
    )

    meta = _wav_metadata(region_wav)
    expected_duration = region_end - region_start
    actual_duration = float(meta["n_frames"]) / float(meta["framerate"])
    if abs(actual_duration - expected_duration) > 0.1:
        raise RuntimeError("Extracted RED-region duration does not match its global bounds")

    rescue_cfg = copy(cfg)
    rescue_cfg.smart_chunking_enabled = True
    rescue_cfg.default_chunk_duration_sec = target_duration_sec
    rescue_cfg.min_chunk_duration_sec = min_duration_sec
    rescue_cfg.max_chunk_duration_sec = max_duration_sec
    rescue_cfg.overlap_seconds = 0
    rescue_cfg.chunk_boundary_search_window_sec = boundary_search_window_sec
    local_chunks = chunker.chunk_file(str(region_wav), extraction_dir, meta, rescue_cfg)
    if not 1 <= len(local_chunks) <= max_windows:
        raise RuntimeError(
            f"Rescue window count {len(local_chunks)} is outside the allowed 1-{max_windows} range"
        )

    source_identity = {
        "source_audio_sha256": _sha256(source_audio),
        "source_audio_size_bytes": source_audio.stat().st_size,
        "source_global_start_sec": float(source_global_start_sec),
        "selected_region_audio_sha256": meta["processing_audio_sha256"],
    }
    windows: list[dict[str, Any]] = []
    for index, local in enumerate(local_chunks, start=1):
        clip_path = extraction_dir / "chunks" / str(local["filename"])
        local_start = float(local["start_sec"])
        local_end = float(local["end_sec"])
        global_start = region_start + local_start
        global_end = min(region_end, region_start + local_end)
        duration = global_end - global_start
        if duration <= 0 or duration > max_duration_sec + 0.1:
            raise RuntimeError("Rescue clip violates its configured duration bounds")
        clip_sha = _sha256(clip_path)
        item = deepcopy(local)
        item.update(
            {
                "window_id": f"rescue-window-{index:05d}",
                "clip_id": f"clip-{index:05d}-{clip_sha[:12]}",
                "path": str(clip_path),
                "clip_sha256": clip_sha,
                "clip_local_start_sec": local_start,
                "clip_local_end_sec": local_end,
                "start_sec": global_start,
                "end_sec": global_end,
                "global_start_sec": global_start,
                "global_end_sec": global_end,
                "duration_seconds": duration,
                "source_identity": source_identity,
                "timing_authority": "application_owned_audio_clip_bounds",
            }
        )
        windows.append(item)

    for previous, current in zip(windows, windows[1:]):
        if abs(float(previous["global_end_sec"]) - float(current["global_start_sec"])) > 0.001:
            raise RuntimeError("Rescue windows must be contiguous and globally ordered")
    if abs(float(windows[0]["global_start_sec"]) - region_start) > 0.001:
        raise RuntimeError("Rescue windows do not start at the selected region boundary")
    if abs(float(windows[-1]["global_end_sec"]) - region_end) > 0.1:
        raise RuntimeError("Rescue windows do not reach the selected region end")
    return selected, windows, source_identity


def build_review_comparison_from_regions(
    source_audio: Path,
    comparison: dict[str, Any],
    extraction_dir: Path,
    cfg: Config,
    *,
    source_global_start_sec: float,
    source_sha256: str,
    allow_red: bool = False,
) -> dict[str, Any]:
    """Attach exact application-owned audio clips to existing non-RED regions.

    This is the conservative no-rescue path: provider candidates and derived
    statuses are copied unchanged, no provider is called, and no timestamps are
    manufactured.
    """

    source_regions = comparison.get("regions")
    if not isinstance(source_regions, list) or not source_regions:
        raise ValueError("Comparison must contain at least one region")
    if not allow_red and any(
        region.get("agreement_status") == "RED" for region in source_regions
    ):
        raise ValueError("Coarse review fallback is only valid when no RED region exists")

    chunks_dir = extraction_dir / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    regions: list[dict[str, Any]] = []
    previous_start: float | None = None
    for index, source_region in enumerate(source_regions, start=1):
        region = deepcopy(source_region)
        start = float(region["global_start_sec"])
        end = float(region["global_end_sec"])
        if end <= start or (previous_start is not None and start < previous_start):
            raise ValueError("Comparison regions must have valid ordered global bounds")
        previous_start = start

        clip_path = chunks_dir / f"chunk-{index:05d}.wav"
        _extract_region_wav(
            source_audio,
            clip_path,
            source_global_start_sec=source_global_start_sec,
            region_global_start_sec=start,
            region_global_end_sec=end,
            cfg=cfg,
        )
        metadata = _wav_metadata(clip_path)
        actual_duration = float(metadata["n_frames"]) / float(metadata["framerate"])
        if abs(actual_duration - (end - start)) > 0.1:
            raise RuntimeError("Extracted review clip duration does not match global bounds")
        clip_sha = metadata["processing_audio_sha256"]
        clip_id = f"clip-{index:05d}-{clip_sha[:12]}"
        region.update(
            {
                "clip_id": clip_id,
                "clip_sha256": clip_sha,
                "duration_seconds": end - start,
                "timing_authority": "application_owned_audio_clip_bounds",
                "boundary": {"strategy": "existing_comparison_region"},
            }
        )
        for candidate in (region.get("candidates") or {}).values():
            if isinstance(candidate, dict):
                candidate.update(
                    {
                        "clip_id": clip_id,
                        "clip_sha256": clip_sha,
                        "timing_authority": "application_owned_audio_clip_bounds",
                    }
                )
        regions.append(region)

    result = deepcopy(comparison)
    result.update(
        {
            "schema_version": "apma.coarse-review-comparison.v1",
            "purpose": (
                "Conservative human-review input preserving existing comparison "
                "regions when live selective rescue is not required or is deferred."
            ),
            "source_comparison": {"sha256": source_sha256, "modified": False},
            "source_audio": {
                "source_audio_sha256": _sha256(source_audio),
                "source_audio_size_bytes": source_audio.stat().st_size,
                "source_global_start_sec": float(source_global_start_sec),
            },
            "timing_policy": (
                "Exact APMA-extracted clip bounds are authoritative; existing provider "
                "candidates and derived statuses are unchanged."
            ),
            "provider_calls": 0,
            "regions": regions,
        }
    )
    return result


def configure_quality_provider_model(base_cfg: Config, provider_code: str) -> Config:
    """Return a copy locked to the approved V1 Quality provider model."""

    if provider_code not in QUALITY_PROVIDER_MODELS:
        raise ValueError(f"Unsupported Quality provider code: {provider_code}")
    cfg = copy(base_cfg)
    model = QUALITY_PROVIDER_MODELS[provider_code]
    if provider_code == "gptTr":
        cfg.openai_recommended_model = model
        cfg.openai_model = model
    elif provider_code == "gpt4oDiarz":
        cfg.openai_diarize_model = model
        cfg.openai_model = model
    elif provider_code == "Gem35T":
        cfg.gemini_transcribe_model = model
        cfg.gemini_transcription_model = model
    return cfg


def configure_quality_chunking(base_cfg: Config) -> Config:
    """Use the tightest request envelope required by the fixed Quality providers."""

    cfg = copy(base_cfg)
    provider_policies = []
    for provider_code in PROVIDER_ORDER:
        provider_cfg = configure_quality_provider_model(cfg, provider_code)
        provider_policies.append(
            configure_model_chunking(
                provider_cfg, QUALITY_PROVIDER_MODELS[provider_code]
            )
        )
    cfg.target_max_chunk_bytes = min(
        int(policy.target_max_chunk_bytes) for policy in provider_policies
    )
    cfg.hard_max_chunk_bytes = min(
        int(policy.hard_max_chunk_bytes) for policy in provider_policies
    )
    cfg.default_chunk_duration_sec = min(
        int(policy.default_chunk_duration_sec) for policy in provider_policies
    )
    cfg.max_chunk_duration_sec = min(
        int(policy.max_chunk_duration_sec) for policy in provider_policies
    )
    cfg.min_chunk_duration_sec = min(
        int(policy.min_chunk_duration_sec) for policy in provider_policies
    )
    cfg.external_transcription_timeout_seconds = max(
        float(policy.external_transcription_timeout_seconds)
        for policy in provider_policies
    )
    return cfg


def configure_live_provider(base_cfg: Config, provider_code: str) -> Config:
    cfg = configure_quality_provider_model(base_cfg, provider_code)
    model = QUALITY_PROVIDER_MODELS[provider_code]
    # Provider comparison evidence keeps each model's own script, punctuation,
    # speaker labels, and formatting. Any later derived draft is kept separate.
    cfg.chinese_script_preference = "preserve"
    cfg.dry_run = False
    cfg.max_retries = 1
    cfg.openai_max_retries = 1
    # External Quality providers get at most three bounded attempts. This lets
    # their HTTP Retry-After instruction handle a transient 429 without turning
    # a long job into an unbounded retry loop.
    cfg.external_transcription_max_retries = min(
        3, max(1, int(base_cfg.external_transcription_max_retries))
    )
    cfg.enable_provider_timestamps = False
    engine, model_attr = PROVIDER_ROUTES[provider_code]
    cfg.transcription_engine = engine
    if engine == "meralion":
        cfg.enable_live_meralion_transcription = True
    elif engine == "openai":
        cfg.enable_live_openai_transcription = True
        cfg.openai_model = str(getattr(cfg, model_attr))
    elif engine == "gemini":
        cfg.enable_live_gemini_transcription = True
        cfg.gemini_transcription_model = str(getattr(cfg, model_attr))
        # Gem35T's provider-native VERBATIM contract supplies speaker labels and
        # word timestamps. Each APMA chunk remains far below its 30-minute limit.
        cfg.enable_provider_timestamps = True
        cfg.enable_gemini_speaker_attribution = True
    return configure_model_chunking(cfg, model)


def estimate_live_costs(
    windows: list[dict[str, Any]], base_cfg: Config, combined_cap_usd: float
) -> dict[str, Any]:
    if not 0 < float(combined_cap_usd) <= 5.0:
        raise ValueError("Combined selective-rescue cap must be above USD 0 and at most USD 5")
    providers: dict[str, Any] = {}
    combined = 0.0
    for provider_code in PROVIDER_ORDER:
        cfg = configure_live_provider(base_cfg, provider_code)
        model = QUALITY_PROVIDER_MODELS[provider_code]
        status = get_runtime_model_status(model, cfg)
        if not status["runnable"]:
            raise RuntimeError(f"{provider_code} is not runnable: {status['readiness_reason']}")
        estimate = sum(
            math.ceil(float(window["duration_seconds"]))
            / 60.0
            * float(status["price_per_minute_usd"])
            for window in windows
        )
        providers[provider_code] = {
            "model": model,
            "price_per_minute_usd": float(status["price_per_minute_usd"]),
            "estimated_cost_usd": round(estimate, 6),
            "billing_mode": status["billing_mode"],
            "cost_cap_included": status["cost_cap_included"],
            "billing_display": status["billing_display"],
        }
        if status["cost_cap_included"]:
            combined += estimate
    authorization_total = paid_cost_authorization_amount(combined, base_cfg)
    if authorization_total > float(combined_cap_usd) + 1e-12:
        raise RuntimeError(
            f"Buffered paid-provider authorization ${authorization_total:.6f} "
            f"exceeds cap ${combined_cap_usd:.6f}"
        )
    return {
        "combined_cap_usd": float(combined_cap_usd),
        "combined_estimated_cost_usd": round(combined, 6),
        "paid_provider_estimated_total_usd": round(combined, 6),
        "paid_provider_cost_buffer_percent": float(
            base_cfg.paid_provider_cost_buffer_percent
        ),
        "paid_provider_authorization_total_usd": round(authorization_total, 6),
        "providers": providers,
    }


def _relative_reference(path_value: str | None, artifact_root: Path) -> str | None:
    if not path_value:
        return None
    path = Path(path_value).resolve()
    try:
        return path.relative_to(artifact_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def run_live_provider_rescue(
    windows: list[dict[str, Any]],
    base_cfg: Config,
    artifact_root: Path,
    costs: dict[str, Any],
    *,
    transcriber_factory: Callable[[str], Any] = get_transcriber,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Run each exact application-owned clip once through all three providers."""

    results: dict[str, list[dict[str, Any]]] = {}
    summaries: dict[str, Any] = {}
    jobs_root = artifact_root / "provider-jobs"
    for provider_code in PROVIDER_ORDER:
        cfg = configure_live_provider(base_cfg, provider_code)
        cfg.storage_path = str(jobs_root)
        cfg.max_cost_per_job_usd = float(costs["combined_cap_usd"])
        job_id = f"goal-h-{provider_code.lower()}"
        job_dir = job_mod.create_job(job_id, cfg.storage_path)
        manifest = job_mod.read_manifest(job_dir)
        manifest["source"] = {
            "timing_authority": "application_owned_audio_clip_bounds",
            "clip_sha256": [window["clip_sha256"] for window in windows],
        }
        manifest["chunks"] = windows
        job_mod.write_manifest(job_dir, manifest)

        started = time.perf_counter()
        provider_results = transcriber_factory(PROVIDER_ROUTES[provider_code][0]).transcribe_chunks(
            job_id, windows, cfg
        )
        elapsed = time.perf_counter() - started
        if len(provider_results) != len(windows):
            raise RuntimeError(f"{provider_code} did not return one candidate per rescue window")
        for window, result in zip(windows, provider_results):
            if result.get("chunk_sha256") != window["clip_sha256"]:
                raise RuntimeError(f"{provider_code} result does not match the supplied clip")
        results[provider_code] = provider_results
        summaries[provider_code] = {
            **costs["providers"][provider_code],
            "provider_calls": len(provider_results),
            "processing_seconds": round(elapsed, 3),
            "adapter_recorded_cost_usd": round(
                sum(float(item.get("actual_cost_usd", 0.0)) for item in provider_results), 6
            ),
            "actual_provider_billed_cost_usd": None,
            "actual_cost_note": "Provider billing total was not returned by the transcription response.",
        }
    return results, summaries


def build_rescue_comparison(
    selected_region: dict[str, Any],
    windows: list[dict[str, Any]],
    provider_results: dict[str, list[dict[str, Any]]],
    provider_summaries: dict[str, Any],
    cost_summary: dict[str, Any],
    source_identity: dict[str, Any],
    artifact_root: Path,
    *,
    comparison_source_identity: dict[str, Any],
    source_label: str,
    total_processing_seconds: float,
) -> dict[str, Any]:
    regions: list[dict[str, Any]] = []
    for index, window in enumerate(windows):
        candidates: dict[str, Any] = {}
        for provider_code in PROVIDER_ORDER:
            result = provider_results[provider_code][index]
            candidates[provider_code] = {
                "missing": False,
                "text": result["text"],
                "provider": result.get("provider"),
                "provider_code": provider_code,
                "model": result.get("model"),
                "run_id": result.get("run_id"),
                "request_id": result.get("request_id"),
                "clip_id": window["clip_id"],
                "clip_sha256": window["clip_sha256"],
                "global_start_sec": window["global_start_sec"],
                "global_end_sec": window["global_end_sec"],
                "timing_authority": "application_owned_audio_clip_bounds",
                "provider_timestamp_used": False,
                "transcript_ref": _relative_reference(result.get("transcript_path"), artifact_root),
                "provider_response_ref": _relative_reference(
                    result.get("provider_artifact_path"), artifact_root
                ),
            }
        regions.append(
            {
                "region_id": window["window_id"],
                "clip_id": window["clip_id"],
                "clip_sha256": window["clip_sha256"],
                "global_start_sec": window["global_start_sec"],
                "global_end_sec": window["global_end_sec"],
                "duration_seconds": window["duration_seconds"],
                "timing_authority": "application_owned_audio_clip_bounds",
                "boundary": window["boundary"],
                "candidates": candidates,
            }
        )
    comparison = {
        "schema_version": "apma.rescue-comparison.v1",
        "created_at": _utc_now(),
        "provider_order": list(PROVIDER_ORDER),
        "purpose": "Fine selective-rescue proof for one existing RED region.",
        "agreement_label": AGREEMENT_DISCLAIMER,
        "source_comparison": comparison_source_identity,
        "source_audio": {"label": source_label, **source_identity},
        "selected_coarse_region": {
            "region_id": selected_region.get("region_id"),
            "agreement_status": selected_region.get("agreement_status"),
            "global_start_sec": selected_region["global_start_sec"],
            "global_end_sec": selected_region["global_end_sec"],
            "agreement_reasons": selected_region.get("agreement_reasons") or [],
        },
        "timing_policy": (
            "Exact APMA-extracted clip bounds are authoritative. Provider-native timestamps "
            "were neither required nor used."
        ),
        "provider_summaries": provider_summaries,
        "cost_summary": cost_summary,
        "total_processing_seconds": round(float(total_processing_seconds), 3),
        "statistics": {
            "source_coarse_regions_processed": 1,
            "rescue_windows": len(regions),
            "median_window_duration_seconds": round(
                statistics.median(region["duration_seconds"] for region in regions), 3
            ),
        },
        "regions": regions,
    }
    classified = classify_comparison(comparison)
    classified["statistics"]["provider_calls"] = {
        provider: summary["provider_calls"] for provider, summary in provider_summaries.items()
    }
    return classified


def write_rescue_artifacts(comparison: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "rescue_comparison.json"
    html_path = output_dir / "rescue_comparison.html"
    json_path.write_text(json.dumps(comparison, indent=2, ensure_ascii=False), encoding="utf-8")

    provider_order = tuple(comparison.get("provider_order") or PROVIDER_ORDER)
    rows = []
    for region in comparison["regions"]:
        cells = []
        for provider in provider_order:
            candidate = region["candidates"][provider]
            cells.append(f"<td><pre>{html.escape(str(candidate['text']))}</pre></td>")
        status = str(region["agreement_status"])
        rows.append(
            f'<tr class="{status.lower()}"><td>{html.escape(_format_time(region["global_start_sec"]))}'
            f'<br>to<br>{html.escape(_format_time(region["global_end_sec"]))}</td>'
            f"<td><strong>{html.escape(status)}</strong><br>DERIVED agreement only</td>"
            + "".join(cells)
            + "</tr>"
        )
    selected = comparison["selected_coarse_region"]
    html_text = """<!doctype html><html><head><meta charset="utf-8">
<title>APMA Goal H selective rescue comparison</title>
<style>body{font-family:Arial,sans-serif;margin:24px;color:#17202a}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #aeb6bf;padding:10px;vertical-align:top}th{background:#1f4e78;color:white}pre{white-space:pre-wrap;word-break:break-word;margin:0;font:14px/1.45 Arial,sans-serif}.green td:nth-child(2){background:#c6efce}.amber td:nth-child(2){background:#ffeb9c}.red td:nth-child(2){background:#ffc7ce}.note{background:#eef3f8;padding:12px;border-left:4px solid #1f4e78}</style>
</head><body><h1>APMA Goal H — Fine Selective-Rescue Proof</h1>"""
    html_text += (
        '<p class="note">Statuses are <strong>DERIVED ASR agreement/disagreement only</strong>; '
        "not correctness, provider confidence, or winner selection. Exact APMA-extracted clip "
        "bounds are the timing authority; provider timestamps were not used.</p>"
        f"<p><strong>Selected coarse RED region:</strong> {html.escape(str(selected['region_id']))} "
        f"({_format_time(selected['global_start_sec'])}–{_format_time(selected['global_end_sec'])})</p>"
        "<table><thead><tr><th>TIME</th><th>DERIVED STATUS</th>"
        + "".join(f"<th>{html.escape(provider)}</th>" for provider in provider_order)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )
    html_path.write_text(html_text, encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}


__all__ = [
    "PROVIDER_ORDER",
    "QUALITY_PROVIDER_MODELS",
    "build_review_comparison_from_regions",
    "build_rescue_comparison",
    "build_rescue_windows",
    "configure_quality_chunking",
    "configure_live_provider",
    "configure_quality_provider_model",
    "estimate_live_costs",
    "run_live_provider_rescue",
    "select_first_red_region",
    "write_rescue_artifacts",
]
