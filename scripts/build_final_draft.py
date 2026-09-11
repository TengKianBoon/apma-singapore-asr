from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.final_draft import build_final_draft, write_final_draft_artifacts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a strict deterministic final draft from Goal H rescue JSON."
    )
    parser.add_argument("--input", required=True, help="Existing rescue_comparison.json")
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"Input rescue comparison does not exist: {input_path}")
        return 2
    source = json.loads(input_path.read_text(encoding="utf-8"))
    final_draft = build_final_draft(source, source_sha256=_sha256(input_path))
    paths = write_final_draft_artifacts(final_draft, Path(args.output_dir))
    summary = {
        **final_draft["statistics"],
        "green_selections": [
            {
                "window_id": window["window_id"],
                "selected_provider": window["selected_provider"],
                "selected_text_matches_candidate": (
                    window["selected_text"]
                    == window["provider_candidates"][window["selected_provider"]]["text"]
                ),
            }
            for window in final_draft["windows"]
            if window["auto_accepted"]
        ],
        "artifacts": paths,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
