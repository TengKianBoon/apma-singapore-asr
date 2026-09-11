from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.config import load_config
from services.selective_rescue import (
    build_rescue_comparison,
    build_rescue_windows,
    estimate_live_costs,
    run_live_provider_rescue,
    write_rescue_artifacts,
)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_live_gates(args: argparse.Namespace, env: dict[str, str] | None = None) -> list[str]:
    env = env or os.environ
    errors: list[str] = []
    if not args.confirm_live_api:
        errors.append("Missing --confirm-live-api")
    if _parse_bool(env.get("DRY_RUN"), True):
        errors.append("DRY_RUN must be false")
    for gate in (
        "ENABLE_LIVE_MERALION_TRANSCRIPTION",
        "ENABLE_LIVE_OPENAI_TRANSCRIPTION",
        "ENABLE_LIVE_GEMINI_TRANSCRIPTION",
    ):
        if not _parse_bool(env.get(gate), False):
            errors.append(f"{gate} must be true")
    for credential in ("MERALION_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        if not env.get(credential):
            errors.append(f"{credential} must be set in the environment")
    billing_mode = str(env.get("MERALION_BILLING_MODE", "trial_free")).strip().lower()
    if billing_mode not in {"trial_free", "metered"}:
        errors.append("MERALION_BILLING_MODE must be trial_free or metered")
    elif billing_mode == "metered" and not str(
        env.get("MERALION_PRICE_PER_MINUTE_USD") or ""
    ).strip():
        errors.append("MERALION_PRICE_PER_MINUTE_USD must be configured in metered mode")
    if not 0 < float(args.max_cost_usd) <= 5.0:
        errors.append("--max-cost-usd must be above 0 and at most 5")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one guarded live three-provider selective-rescue proof."
    )
    parser.add_argument("--comparison-json", required=True)
    parser.add_argument("--source-audio", required=True)
    parser.add_argument("--source-global-start-sec", required=True, type=float)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--comparison-label", required=True)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--max-cost-usd", required=True, type=float)
    parser.add_argument("--confirm-live-api", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gate_errors = validate_live_gates(args)
    if gate_errors:
        print("Selective-rescue live proof was not started:")
        for error in gate_errors:
            print(f"- {error}")
        return 2

    comparison_path = Path(args.comparison_json)
    source_audio = Path(args.source_audio)
    artifact_root = Path(args.artifact_root)
    if not comparison_path.is_file() or not source_audio.is_file():
        print("Comparison JSON and source audio must both exist.")
        return 2
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    cfg = load_config()
    cfg.storage_path = str(artifact_root / "provider-jobs")
    cfg.max_cost_per_job_usd = float(args.max_cost_usd)
    cfg.enable_provider_timestamps = False

    started = time.perf_counter()
    selected, windows, source_identity = build_rescue_windows(
        source_audio,
        comparison,
        artifact_root / "extracted-clips",
        cfg,
        source_global_start_sec=float(args.source_global_start_sec),
    )
    if not 4 <= len(windows) <= 8:
        raise RuntimeError(f"Proof requires 4-8 windows; planned {len(windows)}")
    costs = estimate_live_costs(windows, cfg, float(args.max_cost_usd))
    print(
        f"Selected {selected['region_id']} with {len(windows)} exact clips; "
        f"combined estimate ${costs['combined_estimated_cost_usd']:.6f}, "
        f"cap ${costs['combined_cap_usd']:.2f}."
    )
    provider_results, provider_summaries = run_live_provider_rescue(
        windows, cfg, artifact_root, costs
    )
    total_elapsed = time.perf_counter() - started
    rescue = build_rescue_comparison(
        selected,
        windows,
        provider_results,
        provider_summaries,
        costs,
        source_identity,
        artifact_root,
        comparison_source_identity={
            "label": args.comparison_label,
            "sha256": _sha256(comparison_path),
            "modified": False,
        },
        source_label=args.source_label,
        total_processing_seconds=total_elapsed,
    )
    paths = write_rescue_artifacts(rescue, artifact_root)
    summary = {
        "selected_region": selected["region_id"],
        "windows": [
            {
                "window_id": item["window_id"],
                "global_start_sec": item["global_start_sec"],
                "global_end_sec": item["global_end_sec"],
                "duration_seconds": item["duration_seconds"],
                "boundary_strategy": item["boundary"]["strategy"],
            }
            for item in windows
        ],
        "agreement_counts": rescue["agreement_analysis"]["status_counts"],
        "provider_summaries": provider_summaries,
        "cost_summary": costs,
        "total_processing_seconds": round(total_elapsed, 3),
        "artifacts": paths,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
