from typing import Optional

from services.transcription.base import Transcriber
from services.transcription.mock import MockTranscriber
from services.transcription.openai_adapter import OpenAITranscriber


def get_transcriber(name: Optional[str] = None) -> Transcriber:
    """Factory for obtaining a transcriber implementation.

    Defaults to the mock transcriber when `name` is None.
    """
    engine = (name or "mock").lower()
    if engine == "mock":
        return MockTranscriber()
    if engine == "openai":
        return OpenAITranscriber()
    if engine in {"meralion", "m3asr"}:
        from services.transcription.external_adapter import MeralionTranscriber

        return MeralionTranscriber()
    if engine in {"gemini", "google", "gem37f", "gem35t"}:
        from services.transcription.external_adapter import GeminiTranscriber

        return GeminiTranscriber()
    if engine in {"alibaba", "qwen", "qwena3ft", "qwen-filetrans", "qwen_filetrans"}:
        from services.transcription.external_adapter import QwenFiletransTranscriber

        return QwenFiletransTranscriber()
    raise ValueError(f"Unknown transcriber engine: {name}")


__all__ = ["get_transcriber", "Transcriber", "MockTranscriber", "OpenAITranscriber"]
