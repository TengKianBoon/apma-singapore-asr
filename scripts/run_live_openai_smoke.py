from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import runner
from services.config import load_config


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _generate_synthetic_wav(path: Path, duration_sec: float = 2.0, framerate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * framerate)
    amplitude = 8000
    frequency = 440.0
    frames = bytearray()
    for i in range(n_frames):
        sample = int(amplitude * math.sin(2 * math.pi * frequency * i / framerate))
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(bytes(frames))


def _wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        framerate = wf.getframerate()
        if framerate <= 0:
            return 0.0
        return float(wf.getnframes()) / float(framerate)


def _estimate_cost_usd(duration_seconds: float, cfg) -> float:
    minutes = math.ceil(max(0.0, duration_seconds)) / 60.0
    price = cfg.openai_price_per_minute.get(cfg.openai_model, 0.0)
    return float(minutes * price)


def validate_live_smoke_gates(args, env: dict[str, str] | None = None) -> list[str]:
    env = env or os.environ
    errors = []
    if not args.confirm_live_api:
        errors.append("Missing --confirm-live-api")
    if _parse_bool(env.get("DRY_RUN"), True):
        errors.append("DRY_RUN must be false")
    if not _parse_bool(env.get("ENABLE_LIVE_OPENAI_TRANSCRIPTION"), False):
        errors.append("ENABLE_LIVE_OPENAI_TRANSCRIPTION must be true")
    if env.get("TRANSCRIPTION_ENGINE", "").strip().lower() != "openai":
        errors.append("TRANSCRIPTION_ENGINE must be openai")
    if not env.get("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY must be set in the environment")
    return errors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a guarded manual live OpenAI transcription smoke test.")
    parser.add_argument("--input-wav", required=True, help="Tiny non-private WAV file to transcribe.")
    parser.add_argument("--job-id", default="live-openai-smoke", help="Job id to write under jobs/<job-id>.")
    parser.add_argument("--storage-path", default="jobs", help="Local job storage folder.")
    parser.add_argument("--confirm-live-api", action="store_true", help="Required acknowledgement that this can call the live OpenAI API.")
    parser.add_argument(
        "--provider-timestamps",
        action="store_true",
        help="Opt in to provider-native gptTr segment timestamps for this run.",
    )
    parser.add_argument("--generate-synthetic-wav", action="store_true", help="Create a tiny synthetic WAV at --input-wav before running.")
    parser.add_argument("--synthetic-duration-sec", type=float, default=2.0, help="Synthetic WAV duration when --generate-synthetic-wav is used.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    gate_errors = validate_live_smoke_gates(args)
    if gate_errors:
        print("Live OpenAI smoke test refused to run:")
        for error in gate_errors:
            print(f"- {error}")
        return 2

    input_wav = Path(args.input_wav)
    if args.generate_synthetic_wav:
        _generate_synthetic_wav(input_wav, duration_sec=args.synthetic_duration_sec)
    if not input_wav.exists():
        print(f"Input WAV does not exist: {input_wav}")
        return 2
    if input_wav.suffix.lower() != ".wav":
        print(f"Input file must be a .wav file: {input_wav}")
        return 2

    cfg = load_config()
    cfg.dry_run = False
    cfg.enable_live_openai_transcription = True
    cfg.transcription_engine = "openai"
    cfg.storage_path = args.storage_path
    cfg.openai_api_key = os.environ.get("OPENAI_API_KEY")
    cfg.enable_provider_timestamps = bool(args.provider_timestamps)

    size_bytes = input_wav.stat().st_size
    if size_bytes > int(cfg.openai_file_size_limit_bytes):
        print(f"Input WAV is too large for configured OpenAI file limit: {size_bytes} bytes")
        return 2

    duration_seconds = _wav_duration_seconds(input_wav)
    estimated_cost = _estimate_cost_usd(duration_seconds, cfg)
    if estimated_cost > float(cfg.max_cost_per_job_usd):
        print(f"Estimated cost {estimated_cost:.6f} exceeds MAX_COST_PER_JOB_USD={cfg.max_cost_per_job_usd}")
        return 2

    print("DO NOT RUN UNLESS APPROVED: live OpenAI API call is enabled for this command.")
    print(f"Estimated transcription cost: ${estimated_cost:.6f}")
    result = runner.run_job(args.job_id, str(input_wav), cfg)
    print(f"Job {args.job_id} finished with state={result.get('state')}")
    return 0 if result.get("state") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
