import wave
import struct
import math
from pathlib import Path


def generate_sine_wav(path: str, duration_sec: float = 1.0, framerate: int = 16000, channels: int = 1, sampwidth: int = 2, freq: float = 440.0):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    n_frames = int(duration_sec * framerate)
    max_amp = (2 ** (8 * sampwidth - 1)) - 1

    with wave.open(path, "w") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(framerate)

        for i in range(n_frames):
            sample = int(0.3 * max_amp * math.sin(2 * math.pi * freq * i / framerate))
            # pack as little-endian signed
            data = struct.pack("<h", sample)
            if channels == 2:
                data = data + data
            wf.writeframes(data)
