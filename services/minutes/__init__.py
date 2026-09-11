from services.minutes.base import MinutesGenerator
from services.minutes.mock import MockMinutesGenerator
from services.minutes.openai_adapter import LiveOpenAIMinutesGenerator
from services.minutes.presets import get_minutes_style_preset, list_minutes_style_presets


def get_minutes_generator(name: str = "mock") -> MinutesGenerator:
    engine = (name or "mock").strip().lower()
    if engine == "mock":
        return MockMinutesGenerator()
    raise ValueError(f"Unknown minutes generator: {name}")


__all__ = [
    "MinutesGenerator",
    "LiveOpenAIMinutesGenerator",
    "MockMinutesGenerator",
    "get_minutes_generator",
    "get_minutes_style_preset",
    "list_minutes_style_presets",
]
