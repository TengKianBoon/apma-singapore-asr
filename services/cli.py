"""CLI entrypoint for APMA V5 (Task 3 scaffold).

Provides a minimal argparse driven CLI that runs safely in dry-run mode
and emits a JSON-line verification log. No service logic is implemented
in Task 3.
"""

import argparse
import platform
import sys
from typing import Optional

from services.config import load_config
from services.logger import init_logging, get_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apma-service", description="APMA V5 service (dry-run)")
    parser.add_argument("--job-id", type=str, help="Optional job id for correlation", default=None)
    parser.add_argument("--config", type=str, help="Optional path to .env or config file", default=None)
    parser.add_argument("--ingest", type=str, help="Path to .wav file to ingest and process", default=None)
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    # task 3: do not allow disabling DRY_RUN; always safe
    if args.job_id:
        cfg.job_id = args.job_id

    log_level = "DEBUG" if args.verbose and args.verbose > 0 else cfg.log_level
    init_logging(log_level)
    logger = get_logger(__name__, job_id=cfg.job_id)

    # If ingest requested, run ingest -> preprocess -> chunker pipeline (dry-run)
    if getattr(args, "ingest", None):
        import uuid
        from services import runner as runner_mod
        from services.errors import JobExistsError

        job_id = cfg.job_id or args.job_id or str(uuid.uuid4())
        cfg.job_id = job_id
        try:
            result = runner_mod.run_job(job_id, args.ingest, cfg)
        except JobExistsError as e:
            print(f"Job exists: {e}")
            logger.error("job exists: %s", e)
            return 2

        state = result.get("state")
        chunks = result.get("chunks", []) or []
        errors = result.get("errors", []) or []
        print(f"Job {job_id} finished with state={state}. chunks={len(chunks)} errors={len(errors)}")
        if state != "completed":
            logger.error("job finished in failed state: %s", state)
            return 1
        logger.info("dry-run ingest complete")
        return 0

    # Print dry-run verification info and emit one structured log line
    print("APMA V5 dry-run verification")
    print("Python", platform.python_version())
    print("DRY_RUN=" + str(cfg.dry_run))

    logger.info("dry-run verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
