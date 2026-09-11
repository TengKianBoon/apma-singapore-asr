from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.config import Config
from services.errors import CostLimitExceededError
from services.minutes.presets import get_minutes_style_preset, normalize_minutes_style
from services.text_normalization import normalize_generated_text, simplified_chinese_instruction


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def estimate_text_tokens(text: str) -> int:
    # Conservative rough estimate for preflight only. Actual billing is reported by OpenAI.
    return max(1, int((len(text or "") + 3) // 4))


def minutes_model_options(cfg: Config) -> list[dict[str, Any]]:
    labels = [
        (cfg.openai_minutes_model, "Default"),
        (cfg.openai_minutes_premium_model, "Premium"),
        (cfg.openai_minutes_best_model, "Best Quality"),
    ]
    seen: set[str] = set()
    options = []
    for model, label in labels:
        if not model or model in seen:
            continue
        seen.add(model)
        price = cfg.openai_minutes_price_per_million_tokens.get(model, {"input": 0.0, "output": 0.0})
        options.append(
            {
                "model": model,
                "label": label,
                "input_per_1m_usd": float(price.get("input", 0.0)),
                "output_per_1m_usd": float(price.get("output", 0.0)),
            }
        )
    return options


def estimate_minutes_cost(transcript_text: str, style: str, model: str, cfg: Config) -> dict[str, Any]:
    style_key = normalize_minutes_style(style)
    price = cfg.openai_minutes_price_per_million_tokens.get(model, {"input": 0.0, "output": 0.0})
    input_tokens = estimate_text_tokens(transcript_text)
    output_tokens = int(cfg.openai_minutes_output_tokens_by_style.get(style_key, 2200))
    input_cost = (input_tokens / 1_000_000.0) * float(price.get("input", 0.0))
    output_cost = (output_tokens / 1_000_000.0) * float(price.get("output", 0.0))
    total = float(input_cost + output_cost)
    return {
        "ok": True,
        "style": style_key,
        "model": model,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_total_tokens": input_tokens + output_tokens,
        "estimated_cost_usd": total,
        "run_budget_cap_usd": total,
        "max_minutes_cost_per_job_usd": float(cfg.max_minutes_cost_per_job_usd),
        "within_cap": total <= float(cfg.max_minutes_cost_per_job_usd),
        "price_per_million_tokens_usd": {
            "input": float(price.get("input", 0.0)),
            "output": float(price.get("output", 0.0)),
        },
    }


class OpenAIMinutesHttpClient:
    """Minimal standard-library client for guarded live minutes generation."""

    def __init__(self, api_key: str, api_url: str, timeout_seconds: float):
        self.api_key = api_key
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_config(cls, cfg: Config) -> "OpenAIMinutesHttpClient":
        api_key = cfg.openai_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required for live OpenAI minutes generation")
        return cls(
            api_key=api_key,
            api_url=cfg.openai_minutes_api_url,
            timeout_seconds=float(cfg.openai_minutes_timeout_seconds),
        )

    def generate_minutes(self, *, model: str, prompt: str, transcript_text: str) -> dict[str, Any]:
        body = json.dumps(
            {
                "model": model,
                "input": [
                    {
                        "role": "system",
                        "content": (
                            "You produce factual, evidence-preserving meeting minutes. "
                            "Do not invent facts. Preserve important details and separate confirmed facts from suggestions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"{prompt}\n\nTranscript:\n{transcript_text}",
                    },
                ],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI minutes request failed: HTTP {exc.code}: {detail}") from exc

        text = self._extract_text(payload)
        if not text:
            raise RuntimeError("OpenAI minutes response did not include text")
        return {"text": text, "raw_response": payload}

    def _extract_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]
        chunks: list[str] = []
        for item in payload.get("output", []) or []:
            for content in item.get("content", []) or []:
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "\n".join(chunks).strip()


class LiveOpenAIMinutesGenerator:
    def __init__(self, client: Any | None = None):
        self.client = client

    def _client(self, cfg: Config) -> Any:
        return self.client or OpenAIMinutesHttpClient.from_config(cfg)

    def check_gates(self, cfg: Config, confirm_live_minutes: bool) -> list[str]:
        errors = []
        if getattr(cfg, "dry_run", True):
            errors.append("DRY_RUN must be false for live minutes generation.")
        if not getattr(cfg, "enable_live_openai_minutes", False):
            errors.append("ENABLE_LIVE_OPENAI_MINUTES must be true.")
        if not confirm_live_minutes:
            errors.append("Live minutes generation requires explicit confirmation.")
        if not (getattr(cfg, "openai_api_key", None) or os.environ.get("OPENAI_API_KEY")):
            errors.append("OPENAI_API_KEY must be set in the server environment.")
        return errors

    def generate(
        self,
        job_id: str,
        job_dir: Path,
        outputs: dict[str, Any],
        cfg: Config,
        *,
        model: str,
        minutes_style: str,
        run_budget_cap_usd: float,
        confirm_live_minutes: bool,
    ) -> dict[str, str]:
        transcript_path = outputs.get("full_transcript_txt")
        if not transcript_path or not Path(transcript_path).exists():
            raise RuntimeError("Full transcript text is required before generating live minutes.")

        gate_errors = self.check_gates(cfg, confirm_live_minutes)
        if gate_errors:
            raise RuntimeError("; ".join(gate_errors))

        preset = get_minutes_style_preset(minutes_style)
        transcript_text = Path(transcript_path).read_text(encoding="utf-8")
        estimate = estimate_minutes_cost(transcript_text, preset.key, model, cfg)
        if abs(float(run_budget_cap_usd) - float(estimate["run_budget_cap_usd"])) > 0.000001:
            raise RuntimeError("Minutes budget cap does not match the current estimate. Estimate again before running.")
        if not estimate["within_cap"]:
            raise CostLimitExceededError("Estimated minutes generation cost exceeds MAX_MINUTES_COST_PER_JOB_USD.")

        prompt = (
            f"{preset.prompt}\n\nChinese script rule:\n"
            f"{simplified_chinese_instruction(cfg.chinese_script_preference)}"
        )
        response = self._client(cfg).generate_minutes(
            model=model,
            prompt=prompt,
            transcript_text=transcript_text,
        )
        minutes_text, script_normalization = normalize_generated_text(
            response["text"], cfg.chinese_script_preference
        )

        job_dir = Path(job_dir)
        outputs_dir = job_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        minutes_path = outputs_dir / "minutes.md"
        action_items_path = outputs_dir / "action_items.json"
        job_summary_path = outputs_dir / "job_summary.json"

        minutes_path.write_text(minutes_text, encoding="utf-8")
        action_items = {
            "job_id": job_id,
            "generator": "openai",
            "minutes_style": preset.key,
            "model": model,
            "note": "Structured action items are included in minutes.md. Dedicated extraction can be added later.",
            "action_items": [],
        }
        summary = {
            "job_id": job_id,
            "generator": "openai",
            "state": "completed",
            "minutes_style": preset.key,
            "model": model,
            "created_at": _now_iso(),
            "preflight": estimate,
            "script_normalization": script_normalization,
            "outputs": {
                "full_transcript_txt": outputs.get("full_transcript_txt"),
                "full_transcript_json": outputs.get("full_transcript_json"),
                "minutes_md": str(minutes_path),
                "action_items_json": str(action_items_path),
                "job_summary_json": str(job_summary_path),
            },
        }
        action_items_path.write_text(json.dumps(action_items, indent=2), encoding="utf-8")
        job_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return {
            "minutes_md": str(minutes_path),
            "action_items_json": str(action_items_path),
            "job_summary_json": str(job_summary_path),
        }
