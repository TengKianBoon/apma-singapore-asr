"""Structured JSON-line logging helper using Python stdlib logging.

Lightweight JSON formatter and logger factory for Task 3 scaffolding.
"""

from typing import Optional
import logging
import json
import sys
import os
import datetime


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        obj = {
            "ts": ts,
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
            "job_id": getattr(record, "job_id", None),
            "pid": os.getpid(),
        }
        if record.exc_info:
            obj["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


def init_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str, job_id: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
    return logging.LoggerAdapter(logger, {"job_id": job_id}) if job_id else logger
