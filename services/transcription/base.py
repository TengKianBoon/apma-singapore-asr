from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from services.config import Config


class Transcriber(ABC):
    """Abstract base class for transcription backends.

    Implementations must be deterministic in dry-run mode and must not
    perform network calls or external API usage.
    """

    @abstractmethod
    def transcribe_chunk(self, job_id: str, chunk_meta: dict, cfg: Config) -> dict:
        """Transcribe a single chunk and return transcript metadata.

        Must write any transcript artifact(s) to disk under the job's
        storage path (e.g. `jobs/<job_id>/transcripts/`).
        """

    def transcribe_chunks(self, job_id: str, chunks: List[dict], cfg: Config) -> List[dict]:
        """Default implementation: iterate over chunks and transcribe each."""
        results = []
        for c in chunks:
            results.append(self.transcribe_chunk(job_id, c, cfg))
        return results


__all__ = ["Transcriber"]
