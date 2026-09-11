"""Create a timestamp-only comparison from retained provider JSON files."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.transcript_alignment import build_comparison, write_comparison


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--m3asr-json", required=True, type=Path)
    parser.add_argument("--gpttr-json", required=True, type=Path)
    parser.add_argument("--gem35t-json", required=True, type=Path)
    parser.add_argument("--timeline-offset-seconds", required=True, type=float)
    parser.add_argument("--recording-duration-seconds", required=True, type=float)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    comparison = build_comparison(
        {
            "M3ASR": args.m3asr_json,
            "gptTr": args.gpttr_json,
            "Gem35T": args.gem35t_json,
        },
        timeline_offset_seconds=args.timeline_offset_seconds,
        recording_duration_seconds=args.recording_duration_seconds,
    )
    json_path, html_path = write_comparison(comparison, args.output_dir)
    counts = comparison["statistics"]["source_segments"]
    for provider in comparison["provider_order"]:
        print(f"{provider}_SEGMENTS={counts[provider]}")
    print(f"ALIGNED_REGIONS={comparison['statistics']['aligned_regions']}")
    print(f"UNALIGNED_SEGMENTS={comparison['statistics']['unaligned_segments']}")
    print(f"COMPARISON_JSON={json_path.resolve()}")
    print(f"COMPARISON_HTML={html_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
