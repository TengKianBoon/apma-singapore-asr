from __future__ import annotations

import argparse
import math
import os
import struct
import sys
import wave
from pathlib import Path

from services import cli


EXPECTED_OUTPUTS = [
    "chunks",
    "transcripts",
    "outputs/full_transcript.json",
    "outputs/full_transcript.txt",
    "outputs/full_transcript.html",
    "outputs/full_transcript.srt",
    "outputs/full_transcript.vtt",
    "outputs/minutes.md",
    "outputs/action_items.json",
    "outputs/job_summary.json",
    "job_manifest.json",
]


def _generate_synthetic_wav(path: Path, duration_sec: float = 2.0, framerate: int = 8000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * framerate)
    amplitude = 8000
    frequency = 440.0

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        frames = bytearray()
        for i in range(n_frames):
            sample = int(amplitude * math.sin(2 * math.pi * frequency * i / framerate))
            frames.extend(struct.pack("<h", sample))
        wf.writeframes(bytes(frames))


def _expected_paths(job_dir: Path) -> list[Path]:
    return [job_dir / rel for rel in EXPECTED_OUTPUTS]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the APMA V5 Release 0.1 dry-run MVP demo.")
    parser.add_argument("--job-id", default="mvp-dry-run-demo", help="Job id to write under jobs/<job-id>.")
    parser.add_argument("--audio-path", default="sample_audio/synthetic_mvp_demo.wav", help="Local synthetic WAV path to create/use.")
    parser.add_argument("--storage-path", default="jobs", help="Local job storage folder.")
    parser.add_argument("--duration-sec", type=float, default=2.0, help="Synthetic WAV duration in seconds.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    audio_path = Path(args.audio_path)
    storage_path = Path(args.storage_path)
    job_dir = storage_path / args.job_id

    if job_dir.exists():
        print(f"Job folder already exists: {job_dir}")
        print("Use a new --job-id or remove that local demo job folder after checking the path carefully.")
        return 2

    os.environ["DRY_RUN"] = "true"
    os.environ["TRANSCRIPTION_ENGINE"] = "mock"
    os.environ["STORAGE_PATH"] = str(storage_path)

    _generate_synthetic_wav(audio_path, duration_sec=args.duration_sec)
    rc = cli.main(["--ingest", str(audio_path), "--job-id", args.job_id])
    if rc != 0:
        return rc

    missing = [path for path in _expected_paths(job_dir) if not path.exists()]
    if missing:
        print("Dry-run job completed, but expected outputs are missing:")
        for path in missing:
            print(f"- {path}")
        return 1

    print("")
    print("Release 0.1 dry-run outputs:")
    for path in _expected_paths(job_dir):
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
