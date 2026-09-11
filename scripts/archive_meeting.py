from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.meeting_archive import DATETIME_SOURCES, archive_meeting


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create one non-destructive APMA meeting archive revision")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--meeting-id", required=True)
    parser.add_argument("--meeting-start", help="ISO-8601 meeting start; omit only when unknown")
    parser.add_argument("--meeting-datetime-source", required=True, choices=sorted(DATETIME_SOURCES))
    parser.add_argument("--project", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--short-name", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = archive_meeting(
        Path(args.job_dir),
        Path(args.archive_root),
        meeting_id=args.meeting_id,
        meeting_start=args.meeting_start,
        meeting_datetime_source=args.meeting_datetime_source,
        likely_project=args.project,
        content=args.content,
        short_name=args.short_name,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
