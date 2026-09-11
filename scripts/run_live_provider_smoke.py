from __future__ import annotations

import argparse
import json
import math
import os
import sys
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import job as job_mod
from services import runner
from services.config import Config, load_config
from services.transcription.router import get_runtime_model_status


PROVIDERS: dict[str, dict[str, str]] = {
    "M3ASR": {
        "engine": "meralion",
        "model_attr": "meralion_transcription_model",
        "gate_env": "ENABLE_LIVE_MERALION_TRANSCRIPTION",
        "credential_env": "MERALION_API_KEY",
        "file_limit_attr": "meralion_file_size_limit_bytes",
    },
    "Gem37F": {
        "engine": "gemini",
        "model_attr": "gemini_flash_model",
        "gate_env": "ENABLE_LIVE_GEMINI_TRANSCRIPTION",
        "credential_env": "GEMINI_API_KEY",
        "file_limit_attr": "gemini_inline_file_size_limit_bytes",
    },
    "Gem35T": {
        "engine": "gemini",
        "model_attr": "gemini_transcribe_model",
        "gate_env": "ENABLE_LIVE_GEMINI_TRANSCRIPTION",
        "credential_env": "GEMINI_API_KEY",
        "file_limit_attr": "gemini_inline_file_size_limit_bytes",
    },
    "QwenA3FT": {
        "engine": "qwen_filetrans",
        "model_attr": "qwen_filetrans_model",
        "gate_env": "ENABLE_LIVE_QWEN_FILETRANS_TRANSCRIPTION",
        "credential_env": "DASHSCOPE_API_KEY",
        "file_limit_attr": "qwen_filetrans_file_size_limit_bytes",
    },
}


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        rate = wav.getframerate()
        return 0.0 if rate <= 0 else float(wav.getnframes()) / float(rate)


def validate_live_smoke_gates(
    args: argparse.Namespace,
    env: dict[str, str] | os._Environ[str] | None = None,
) -> list[str]:
    if env is None:
        env = os.environ
    route = PROVIDERS[args.provider_code]
    errors: list[str] = []
    if not args.confirm_live_api:
        errors.append("Missing --confirm-live-api")
    if _parse_bool(env.get("DRY_RUN"), True):
        errors.append("DRY_RUN must be false")
    if not _parse_bool(env.get(route["gate_env"]), False):
        errors.append(f"{route['gate_env']} must be true")
    if not env.get(route["credential_env"]):
        errors.append(f"{route['credential_env']} must be set in the environment")
    billing_mode = str(env.get("MERALION_BILLING_MODE", "trial_free")).strip().lower()
    if args.provider_code == "M3ASR" and billing_mode not in {"trial_free", "metered"}:
        errors.append("MERALION_BILLING_MODE must be trial_free or metered")
    if (
        args.provider_code == "M3ASR"
        and billing_mode == "metered"
        and not str(env.get("MERALION_PRICE_PER_MINUTE_USD", "")).strip()
    ):
        errors.append("MERALION_PRICE_PER_MINUTE_USD must be set in metered mode")
    if not (0 < float(args.max_cost_usd) <= 5.0):
        errors.append("--max-cost-usd must be greater than 0 and no more than 5")
    return errors


def _build_live_config(
    args: argparse.Namespace,
    env: dict[str, str] | os._Environ[str] | None = None,
) -> Config:
    if env is None:
        env = os.environ
    route = PROVIDERS[args.provider_code]
    cfg = load_config()
    cfg.dry_run = False
    cfg.storage_path = args.storage_path
    cfg.max_cost_per_job_usd = float(args.max_cost_usd)
    cfg.max_retries = 1
    cfg.external_transcription_max_retries = 1
    cfg.enable_provider_timestamps = bool(getattr(args, "provider_timestamps", False))
    cfg.transcription_engine = route["engine"]
    model = str(getattr(cfg, route["model_attr"]))
    if args.provider_code == "M3ASR":
        cfg.enable_live_meralion_transcription = True
        cfg.meralion_api_key = env.get("MERALION_API_KEY")
        cfg.meralion_billing_mode = str(
            env.get("MERALION_BILLING_MODE", "trial_free")
        ).strip().lower()
        configured_price = str(env.get("MERALION_PRICE_PER_MINUTE_USD", "")).strip()
        cfg.meralion_price_per_minute_usd = (
            float(configured_price) if configured_price else None
        )
    elif args.provider_code in {"Gem37F", "Gem35T"}:
        cfg.enable_live_gemini_transcription = True
        cfg.gemini_api_key = env.get("GEMINI_API_KEY")
        cfg.gemini_transcription_model = model
    else:
        cfg.enable_live_qwen_filetrans_transcription = True
        cfg.dashscope_api_key = env.get("DASHSCOPE_API_KEY")
        cfg.qwen_filetrans_model = model
        cfg.qwen_filetrans_staging_mode = str(
            env.get("QWEN_FILETRANS_STAGING_MODE", cfg.qwen_filetrans_staging_mode)
        ).strip().lower()
        cfg.dashscope_api_base_url = str(
            env.get("DASHSCOPE_API_BASE_URL", cfg.dashscope_api_base_url)
        ).rstrip("/")
        cfg.dashscope_temporary_upload_api_base_url = str(
            env.get(
                "DASHSCOPE_TEMPORARY_UPLOAD_API_BASE_URL",
                cfg.dashscope_temporary_upload_api_base_url,
            )
        ).rstrip("/")
    return cfg


def _validate_live_outputs(job_id: str, cfg: Config, provider_code: str) -> dict[str, Any]:
    job_dir = Path(cfg.storage_path) / job_id
    manifest = job_mod.read_manifest(job_dir)
    transcripts = manifest.get("transcription", {}).get("transcripts") or []
    if not transcripts or any(item.get("provider_code") != provider_code for item in transcripts):
        raise RuntimeError(f"Live result did not retain {provider_code} provenance")
    raw_paths = [Path(str(item.get("provider_artifact_path") or "")) for item in transcripts]
    if not raw_paths or any(not path.is_file() for path in raw_paths):
        raise RuntimeError("Provider response JSON was not retained")
    retained = manifest.get("transcription", {}).get("retained_outputs") or {}
    json_path = Path(str(retained.get("json") or ""))
    html_path = Path(str(retained.get("html") or ""))
    if not json_path.is_file() or not html_path.is_file():
        raise RuntimeError("Provider-specific retained JSON/HTML was not written")
    if not json_path.name.endswith(f"{provider_code}.json") or not html_path.name.endswith(
        f"{provider_code}.html"
    ):
        raise RuntimeError("Retained output filenames do not carry the provider code")
    credential = {
        "M3ASR": cfg.meralion_api_key,
        "Gem37F": cfg.gemini_api_key,
        "Gem35T": cfg.gemini_api_key,
        "QwenA3FT": cfg.dashscope_api_key,
    }[provider_code]
    if credential:
        for path in [*raw_paths, json_path, html_path, job_dir / "job_manifest.json"]:
            if str(credential) in path.read_text(encoding="utf-8"):
                raise RuntimeError(f"Credential material was written to {path}")
    return {
        "provider_code": provider_code,
        "job_id": job_id,
        "provider_response_json": [str(path) for path in raw_paths],
        "retained_json": str(json_path),
        "retained_html": str(html_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one guarded live APMA external-provider transcription attempt."
    )
    parser.add_argument("--provider-code", required=True, choices=tuple(PROVIDERS))
    parser.add_argument("--attempt-number", required=True, type=int, choices=(1, 2, 3))
    parser.add_argument("--input-wav", required=True, help="Tiny synthetic or licensed WAV file.")
    parser.add_argument("--job-id", help="Job id; defaults to live-<provider>-attempt-<n>.")
    parser.add_argument("--storage-path", default="jobs")
    parser.add_argument("--max-cost-usd", required=True, type=float)
    parser.add_argument("--confirm-live-api", action="store_true")
    parser.add_argument(
        "--provider-timestamps",
        action="store_true",
        help="Opt in to provider-native timestamp enrichment for this comparison run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gate_errors = validate_live_smoke_gates(args)
    if gate_errors:
        print(f"{args.provider_code} live smoke attempt was not started:")
        for error in gate_errors:
            print(f"- {error}")
        return 2

    input_wav = Path(args.input_wav)
    if not input_wav.is_file() or input_wav.suffix.lower() != ".wav":
        print(f"Input must be an existing WAV file: {input_wav}")
        return 2

    cfg = _build_live_config(args)
    route = PROVIDERS[args.provider_code]
    if input_wav.stat().st_size > int(getattr(cfg, route["file_limit_attr"])):
        print(f"Input exceeds the configured {args.provider_code} file-size limit")
        return 2

    model = str(getattr(cfg, route["model_attr"]))
    status = get_runtime_model_status(model, cfg)
    if not status["runnable"]:
        print(f"{args.provider_code} is not runnable: {status['readiness_reason']}")
        return 2
    duration = _wav_duration_seconds(input_wav)
    estimated_cost = math.ceil(duration) / 60.0 * float(status["price_per_minute_usd"])
    if estimated_cost > cfg.max_cost_per_job_usd + 1e-12:
        print(
            f"Estimated cost ${estimated_cost:.6f} exceeds the explicit cap "
            f"${cfg.max_cost_per_job_usd:.6f}"
        )
        return 2

    job_id = args.job_id or (
        f"live-{args.provider_code.lower()}-attempt-{args.attempt_number}"
    )
    print(
        f"Starting {args.provider_code} live attempt {args.attempt_number}/3; "
        f"estimated cost ${estimated_cost:.6f}, cap ${cfg.max_cost_per_job_usd:.6f}."
    )
    result = runner.run_job(job_id, str(input_wav), cfg)
    if result.get("state") != "completed":
        print(json.dumps({"job_id": job_id, "state": result.get("state"), "errors": result.get("errors", [])}, indent=2))
        return 1
    evidence = _validate_live_outputs(job_id, cfg, args.provider_code)
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
