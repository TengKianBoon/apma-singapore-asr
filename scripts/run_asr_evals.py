"""Run the dependency-free APMA ASR golden-set evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.evaluation.asr import (  # noqa: E402
    build_provider_scorecards,
    build_release_gate,
    evaluate_case,
)


DEFAULT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "asr" / "golden_cases.json"


def build_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    results = [evaluate_case(case) for case in cases]
    scorecards = build_provider_scorecards(results)
    count = len(results)
    mean_wer = sum(item["word_error_rate"] for item in results) / count if count else 0.0
    mean_cer = sum(item["character_error_rate"] for item in results) / count if count else 0.0
    return {
        "schema_version": "apma.asr-eval.v1",
        "case_count": count,
        "languages": sorted({str(item["language"]) for item in results}),
        "mean_word_error_rate": mean_wer,
        "mean_character_error_rate": mean_cer,
        "max_word_error_rate": max((item["word_error_rate"] for item in results), default=0.0),
        "max_character_error_rate": max(
            (item["character_error_rate"] for item in results),
            default=0.0,
        ),
        "provider_scorecards": scorecards,
        "release_gate": build_release_gate(scorecards),
        "cases": results,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline APMA ASR regression metrics.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="JSON golden-set file (default: tests/fixtures/asr/golden_cases.json).",
    )
    parser.add_argument("--output", type=Path, help="Optional path for the JSON report.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("ASR fixture must contain a JSON list")

    report = build_report(cases)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"ASR evaluation report: {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
