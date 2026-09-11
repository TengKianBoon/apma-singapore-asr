from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from services.config import Config


class MinutesGenerator(ABC):
    """Interface for meeting-minutes export generators."""

    @abstractmethod
    def generate(self, job_id: str, job_dir: Path, outputs: dict, cfg: Config) -> dict:
        """Generate minutes exports and return manifest output references."""
