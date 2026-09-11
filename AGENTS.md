# AGENTS — Contributor Guardrails for APMA V5 MVP

Purpose: Keep Phase‑1 development safe, auditable, and single‑user. Follow these rules before making changes.

Rules:

- **No secrets:** Never commit API keys, credentials, or private keys. Use `.env` locally and keep `.env.example` as a template.
- **No real private audio:** Do not add real meeting recordings to the repository. `sample_audio/` may only contain licensed or synthetic test audio.
- **Plan before broad changes:** Create a short plan (design + acceptance criteria) before implementing cross-cutting or large changes.
- **Small commits only:** Each commit/PR should be narrowly scoped and include tests where applicable.
- **Dry‑run default:** Tooling and CLI default to `DRY_RUN=true` to avoid billable API calls. Dry‑run must be the default for local development and CI.
- **Cost caps mandatory:** All jobs must run a preflight cost estimate and enforce `MAX_COST_PER_JOB_USD` before making billable API calls.
- **Tests required:** Unit + integration (dry‑run) tests are required before merging changes that affect runtime behavior.
- **n8n orchestration only:** `n8n` workflows orchestrate jobs (upload, enqueue, poll, fetch results). Heavy processing belongs in the Python service under `services/`.
- **Python service owns heavy processing:** Chunking, transcription, diarization, merging, translation, and summarization must live in the `services/` Python service and run in Docker (Python 3.11/3.12) for reproducibility.
- **No broad rewrites:** Prefer incremental changes; avoid large-formatting-only or mass-rewrite commits.

Developer checklist before PR:

- Add or update unit tests and integration dry‑run tests.
- Ensure `DRY_RUN` default is preserved in new code paths.
- Confirm preflight cost estimation is present for APIs that bill per token or usage.
- Run the repository tooling inside Docker (Python 3.11 or 3.12) locally before opening a PR.

Owner / Contact: fill locally.
