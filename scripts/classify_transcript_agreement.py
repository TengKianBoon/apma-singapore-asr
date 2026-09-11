"""Add deterministic derived agreement statuses to a Goal D comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.transcript_agreement import AgreementThresholds, classify_comparison
from services.transcript_alignment import comparison_html


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv: list[str] | None = None) -> int:
    defaults = AgreementThresholds()
    parser = argparse.ArgumentParser()
    parser.add_argument("--comparison-json", required=True, type=Path)
    parser.add_argument("--comparison-html", required=True, type=Path)
    parser.add_argument("--green-min-pairwise", type=float, default=defaults.green_min_pairwise)
    parser.add_argument("--green-mean-pairwise", type=float, default=defaults.green_mean_pairwise)
    parser.add_argument("--red-min-pairwise", type=float, default=defaults.red_min_pairwise)
    parser.add_argument("--red-mean-pairwise", type=float, default=defaults.red_mean_pairwise)
    parser.add_argument("--red-length-ratio", type=float, default=defaults.red_length_ratio)
    args = parser.parse_args(argv)

    comparison = json.loads(args.comparison_json.read_text(encoding="utf-8"))
    classified = classify_comparison(
        comparison,
        AgreementThresholds(
            green_min_pairwise=args.green_min_pairwise,
            green_mean_pairwise=args.green_mean_pairwise,
            red_min_pairwise=args.red_min_pairwise,
            red_mean_pairwise=args.red_mean_pairwise,
            red_length_ratio=args.red_length_ratio,
        ),
    )
    _atomic_write(
        args.comparison_json,
        json.dumps(classified, indent=2, ensure_ascii=False),
    )
    _atomic_write(args.comparison_html, comparison_html(classified))

    counts = classified["agreement_analysis"]["status_counts"]
    for status in ("GREEN", "AMBER", "RED"):
        print(f"{status}={counts[status]}")
    for region in classified["regions"]:
        print(
            f"{region['region_id']}={region['agreement_status']}: "
            f"{region['agreement_reasons'][0]}"
        )
    print(f"COMPARISON_JSON={args.comparison_json.resolve()}")
    print(f"COMPARISON_HTML={args.comparison_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
